"""Версии проекта поверх git: именованные версии, история сеансами, отмена/повтор, откат.

Человеку — «версии», «история», «вернуть»; внутри — теги `lde/v/…`, коммиты и `git revert`.
Ничего не теряется: откат и отмена — новые точки сохранения.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from lde import gitops

TAG_PREFIX = "lde/v/"
UNDO_TRAILER = "LDE-Undo-Of"
REDO_TRAILER = "LDE-Redo-Of"
SESSION_GAP = 30 * 60          # пауза больше 30 минут — новый сеанс
KINDS = {"manual": "вручную", "submit": "сдача", "share": "ссылка", "review": "рецензия", "restore": "откат",
         "sync": "синхронизация"}


def _slug(name: str) -> str:
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                   "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    s = "".join(tr.get(c, c) for c in name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]
    return s or "v"


def _ident(root: Path):
    return None if gitops._identity_ok(root) else ("LDE", "lde@lde.local")


# ------------------------------------------------------------------ версии ----
def create(root: Path, name: str, note: str = "", kind: str = "manual", rev: str = "HEAD") -> dict:
    """Именованная версия = аннотированный тег на точке сохранения (несохранённое — сначала точкой)."""
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    if not gitops.head(root):
        gitops.commit(root, "Начало истории")
    name = (name or "").strip() or time.strftime("Версия %d.%m.%Y %H:%M")
    base = TAG_PREFIX + time.strftime("%Y%m%d-%H%M%S-") + _slug(name)
    tag, i = base, 1
    while gitops.git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).strip():
        i += 1
        tag = f"{base}-{i}"
    msg = json.dumps({"name": name, "note": note, "kind": kind}, ensure_ascii=False)
    gitops.git(root, "tag", "-a", tag, "-m", msg, rev, author=_ident(root))
    return {"tag": tag, "name": name, "kind": kind, "commit": gitops.git(root, "rev-parse", f"{tag}^{{commit}}").strip()}


def list_versions(root: Path) -> list[dict]:
    out = gitops.git(root, "for-each-ref", "--sort=-creatordate",
                     "--format=%(refname:short)%1f%(*objectname)%1f%(creatordate:unix)%1f%(contents)%1e",
                     f"refs/tags/{TAG_PREFIX}", check=False)
    res = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        tag, commit, ts, body = rec.split("\x1f", 3)
        try:
            meta = json.loads(body.strip())
        except json.JSONDecodeError:
            meta = {"name": body.strip() or tag, "note": "", "kind": "manual"}
        res.append({"tag": tag, "commit": commit, "time": int(ts or 0), "name": meta.get("name", tag),
                    "note": meta.get("note", ""), "kind": meta.get("kind", "manual"),
                    "kind_ru": KINDS.get(meta.get("kind", "manual"), "")})
    return res


def resolve(root: Path, ref: str) -> str:
    """Имя версии, тег, хеш, «HEAD~2» → хеш коммита."""
    if not ref:
        raise ValueError("не указана версия")
    for v in list_versions(root):
        if ref in (v["tag"], v["name"]) or v["tag"].endswith("/" + ref):
            return v["commit"]
    h = gitops.git(root, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False).strip()
    if not h:
        raise ValueError(f"нет версии «{ref}» (lde versions — список)")
    return h


def delete(root: Path, tag: str):
    gitops.git(root, "tag", "-d", tag)


# ----------------------------------------------------------------- история ----
def history(root: Path, n: int = 300) -> list[dict]:
    """Точки сохранения, сгруппированные в сеансы (пауза > 30 мин — новый сеанс), с версиями."""
    out = gitops.git(root, "log", f"-{n}", "--format=%x1e%H%x1f%an%x1f%at%x1f%s%x1f%b%x1d", "--name-only", check=False)
    tags = {}
    for v in list_versions(root):
        tags.setdefault(v["commit"], []).append(v)
    points = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        head, _, files = rec.partition("\x1d")
        parts = head.split("\x1f")
        if len(parts) < 5:
            continue
        h, an, at, subj, body = parts[0], parts[1], int(parts[2]), parts[3], parts[4]
        points.append({"hash": h, "author": an, "time": at, "subject": subj,
                       "files": [f for f in files.split("\n") if f.strip()],
                       "undo": UNDO_TRAILER in body or REDO_TRAILER in body, "versions": tags.get(h, [])})
    sessions, cur = [], None
    for p in points:   # от новых к старым
        if cur is None or cur["start"] - p["time"] > SESSION_GAP:
            cur = {"start": p["time"], "end": p["time"], "points": [], "authors": []}
            sessions.append(cur)
        cur["start"] = p["time"]
        cur["points"].append(p)
        if p["author"] not in cur["authors"]:
            cur["authors"].append(p["author"])
    return sessions


# ------------------------------------------------------ отмена и повтор ----
def _undo_state(root: Path) -> dict:
    p = root / ".lde" / "undo.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"stack": []}


def _save_undo_state(root: Path, st: dict):
    p = root / ".lde" / "undo.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(st), encoding="utf-8")


def _revert(root: Path, rev: str, subject: str, trailer: str) -> str:
    env_author = _ident(root)
    r = subprocess.run(["git", "revert", "--no-edit", rev], cwd=root, capture_output=True, text=True,
                       env=gitops._env(env_author))
    if r.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=root, capture_output=True)
        raise RuntimeError("не получилось: эти изменения пересекаются с более поздними правками. "
                           "Сравните версии и верните нужные элементы по отдельности.")
    gitops.git(root, "commit", "--amend", "-q", "-m", f"{subject}\n\n{trailer}: {rev}", author=env_author)
    return gitops.head(root)


def _undone_set(root: Path) -> set:
    """Точки, отменённые и не повторённые (по пометкам в сообщениях коммитов)."""
    out = gitops.git(root, "log", "-400", "--format=%H%x1f%b%x1e", check=False)
    undo_of, redone = {}, set()
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, _, body = rec.partition("\x1f")
        m = re.search(rf"^{UNDO_TRAILER}: (\w+)", body, re.M)
        if m:
            undo_of[h] = m.group(1)
        m = re.search(rf"^{REDO_TRAILER}: (\w+)", body, re.M)
        if m:
            redone.add(m.group(1))
    return {x for u, x in undo_of.items() if u not in redone}


def undo(root: Path) -> dict:
    """Отменить последнее действие; повторный вызов отменяет предыдущее и т. д. (без потерь)."""
    gitops.checkpoint_external(root)
    out = gitops.git(root, "log", "-400", "--format=%H%x1f%s%x1f%b%x1e", check=False)
    undone = _undone_set(root)
    roots = set(gitops.git(root, "rev-list", "--max-parents=0", "HEAD", check=False).split())
    st = _undo_state(root)
    target = None
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, subj, body = (rec.split("\x1f") + ["", ""])[:3]
        if UNDO_TRAILER in body or REDO_TRAILER in body or h in undone:
            continue
        if subj == "Начало истории" or h in roots:
            break        # первую точку (создание проекта) не отменяем
        target = (h, subj)
        break
    if not target:
        raise RuntimeError("отменять нечего")
    new = _revert(root, target[0], f"Отменено: {target[1]}", UNDO_TRAILER)
    stack = st.get("stack", []) if st.get("head") == gitops.git(root, "rev-parse", "HEAD~1").strip() else []
    stack.append({"undone": target[0], "undo": new, "subject": target[1]})
    _save_undo_state(root, {"stack": stack, "head": new})
    return {"subject": target[1], "commit": new, "can_redo": True}


def can_redo(root: Path) -> bool:
    st = _undo_state(root)
    return bool(st.get("stack")) and st.get("head") == gitops.head(root)


def redo(root: Path) -> dict:
    """Повторить отменённое (только сразу после отмен, пока не было новых правок)."""
    st = _undo_state(root)
    if not can_redo(root):
        raise RuntimeError("повторять нечего (после отмены были новые правки)")
    e = st["stack"].pop()
    new = _revert(root, e["undo"], f"Повторено: {e['subject']}", REDO_TRAILER)
    _save_undo_state(root, {"stack": st["stack"], "head": new})
    return {"subject": e["subject"], "commit": new, "can_redo": bool(st["stack"])}


# ------------------------------------------------------------------ откат ----
def restore(root: Path, ref: str, only: list[str] | None = None) -> dict:
    """Вернуть проект (или только указанные файлы) к версии — новой точкой; текущее остаётся в истории."""
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    rev = resolve(root, ref)
    name = next((v["name"] for v in list_versions(root) if v["commit"] == rev), rev[:8])
    paths = only or ["."]
    # файлы, которых в той версии не было, — убрать (иначе «вернуть» оставит лишнее)
    added = gitops.git(root, "diff", "--name-only", "--diff-filter=A", rev, "HEAD", "--", *paths, check=False).split()
    for f in added:
        gitops.git(root, "rm", "-q", "--", f, check=False)
    gitops.git(root, "checkout", rev, "--", *paths, check=False)
    h = gitops.commit(root, f"Возвращено к версии «{name}»" + (f": {', '.join(only)}" if only else ""))
    return {"changed": bool(h), "commit": h, "name": name}


def show_file(root: Path, ref: str, path: str) -> str | None:
    return gitops.show(root, resolve(root, ref), path)
