"""Git как журнал точек сохранения: коммит на каждую правку, откат — git revert (без потерь)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GITIGNORE = """# LDE: производное, восстанавливается командой (lde build / lde update)
/out/
/.lde/
calc/*/out/
__pycache__/
*.pyc
"""

STUDIO_AUTHOR = ("LDE Studio", "studio@lde.local")


def _env(author=None) -> dict:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    name, mail = author or (None, None)
    if name:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = name
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = mail
    return env


def git(root: Path, *args, check=True, author=None, input=None) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=_env(author), input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def _identity_ok(root: Path) -> bool:
    return bool(git(root, "config", "user.email", check=False).strip())


def ensure_repo(root: Path):
    if not (root / ".git").exists():
        git(root, "init", "-q")
    if not (root / ".git" / "info" / "attributes").exists() or "merge=lde" not in \
            (root / ".git" / "info" / "attributes").read_text(encoding="utf-8", errors="replace"):
        from lde.merge import install_driver
        install_driver(root)
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")


def is_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", check=False).strip())


def commit(root: Path, message: str, author=None, paths: list[str] | None = None) -> str | None:
    """Зафиксировать изменения (все или указанные файлы). Вернуть хеш или None, если нечего."""
    ensure_repo(root)
    if paths:
        git(root, "add", "-A", "--", *paths)
    else:
        git(root, "add", "-A")
    if not git(root, "diff", "--cached", "--name-only", check=False).strip():
        return None
    if author is None and not _identity_ok(root):
        author = ("LDE", "lde@lde.local")
    git(root, "commit", "-q", "-m", message, author=author)
    return git(root, "rev-parse", "HEAD").strip()


def checkpoint_external(root: Path) -> str | None:
    """Чужие несохранённые изменения (агент, редактор) — отдельной точкой, до правки Studio."""
    if is_dirty(root):
        return commit(root, "Изменения вне Studio (агент или редактор)")
    return None


def head(root: Path) -> str:
    """Хеш текущей точки; пусто — истории ещё нет (на пустом репозитории rev-parse печатает «HEAD»)."""
    return git(root, "rev-parse", "-q", "--verify", "HEAD", check=False).strip()


def log(root: Path, n=30) -> list[dict]:
    out = git(root, "log", f"-{n}", "--format=%H%x1f%an%x1f%at%x1f%s", check=False)
    res = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        h, an, at, s = line.split("\x1f")
        res.append({"hash": h, "author": an, "time": int(at), "subject": s})
    return res


def undo(root: Path, rev: str = "HEAD") -> str:
    """Отменить точку сохранения новым коммитом (git revert). Несохранённое — сначала точкой."""
    checkpoint_external(root)
    subj = git(root, "log", "-1", "--format=%s", rev).strip()
    r = subprocess.run(["git", "revert", "--no-edit", rev], cwd=root, capture_output=True, text=True,
                       env=_env(None if _identity_ok(root) else ("LDE", "lde@lde.local")))
    if r.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=root, capture_output=True)
        raise RuntimeError("не удалось отменить: изменения пересекаются с более поздними правками. "
                           "Отмените сначала более поздние точки.")
    git(root, "commit", "--amend", "-q", "-m", f"Отменено: {subj}",
        author=None if _identity_ok(root) else ("LDE", "lde@lde.local"))
    return subj


def show(root: Path, rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def set_seen(root: Path, rev: str | None = None):
    rev = rev or head(root)
    if rev:
        git(root, "update-ref", "refs/lde/seen", rev)


def get_seen(root: Path) -> str:
    return git(root, "rev-parse", "-q", "--verify", "refs/lde/seen", check=False).strip()
