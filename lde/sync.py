"""Хранение проекта на сервере LDE — как на git-сервере, но сервер не видит содержимого.

На сервере: зашифрованные пакеты git (`git bundle`, по одному на отправку), зашифрованные «ссылки»
(ветка и версии-теги → коммит) с атомарным обновлением «сравни и замени», зашифрованные витрины
версий (PDF документов) для веб-панели. Ключ проекта — случайный, для каждого участника зашифрован
его открытым ключом. Слияние при получении — по блокам (lde/merge.py), конфликты — блоками в тексте.

    publish()  — создать проект на сервере и отправить историю
    push()     — отправить новое (сначала нужно получить чужое, как в git)
    pull()     — получить и слить
    sync()     — pull + push
    clone()    — забрать проект с сервера в новую папку
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from lde import crypto, gitops
from lde.server_client import Server, ServerError

BRANCH = "main"            # логическое имя основной ветки на сервере


def _cfg(root: Path, key: str) -> str:
    return gitops.git(root, "config", "--get", f"lde.remote.{key}", check=False).strip()


def _set(root: Path, key: str, val: str):
    gitops.git(root, "config", f"lde.remote.{key}", str(val))


def remote_info(root: Path) -> dict | None:
    pid = _cfg(root, "project")
    return {"project": pid, "url": _cfg(root, "url"), "seq": int(_cfg(root, "seq") or 0)} if pid else None


def _branch(root: Path) -> str:
    return gitops.git(root, "symbolic-ref", "--short", "HEAD", check=False).strip() or "master"


def _project_key(srv: Server, d: dict) -> bytes:
    return crypto.unwrap_key(srv.priv, crypto.b64d(d["key_wrap"]), "project")


def _server(root: Path) -> tuple[Server, str]:
    info = remote_info(root)
    if not info:
        raise ServerError("проект не опубликован на сервере: lde publish")
    return Server(info["url"]), info["project"]


def _refs(srv: Server, pid: str, key: bytes) -> tuple[dict, dict]:
    d = srv.json("GET", f"/api/projects/{pid}")
    refs = {}
    for name, r in (d.get("refs") or {}).items():
        refs[name] = {"sha": json.loads(crypto.open_(key, crypto.b64d(r["value"]), "project-ref:" + name))["sha"],
                      "version": r["version"]}
    return d, refs


# ------------------------------------------------------------------ pull ----
def _apply_packs(root: Path, srv: Server, pid: str, key: bytes) -> int:
    seq = int(_cfg(root, "seq") or 0)
    packs = srv.json("GET", f"/api/projects/{pid}/packs?after={seq}")
    for p in packs:
        _, data, _ = srv.call("GET", f"/api/projects/{pid}/blobs/{p['blob']}")
        bundle = crypto.open_(key, data, "project-pack")
        with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as f:
            f.write(bundle)
            bpath = f.name
        try:
            gitops.git(root, "fetch", "-q", "--no-tags", bpath, "+refs/heads/*:refs/lde/in/*", "+refs/tags/*:refs/tags/*")
        finally:
            Path(bpath).unlink(missing_ok=True)
        seq = p["seq"]
    _set(root, "seq", seq)
    return len(packs)


def pull(root: Path) -> dict:
    """Получить новое с сервера и слить с текущим (по блокам). Конфликты — блоками в тексте."""
    root = Path(root).resolve()
    gitops.checkpoint_external(root)
    srv, pid = _server(root)
    key = _project_key(srv, srv.json("GET", f"/api/projects/{pid}"))
    d, refs = _refs(srv, pid, key)
    n = _apply_packs(root, srv, pid, key)
    main = refs.get("branch:" + BRANCH)
    if not main:
        return {"packs": n, "merged": False, "conflicts": 0, "note": "на сервере ещё нет истории"}
    sha = main["sha"]
    gitops.git(root, "update-ref", "refs/lde/remote/main", sha)
    head = gitops.head(root)
    if not head:
        gitops.git(root, "reset", "-q", "--hard", sha)
        return {"packs": n, "merged": True, "fast_forward": True, "conflicts": 0}
    if head == sha or subprocess_ok(root, "merge-base", "--is-ancestor", sha, "HEAD"):
        return {"packs": n, "merged": False, "conflicts": 0, "note": "у вас всё новое"}
    if subprocess_ok(root, "merge-base", "--is-ancestor", "HEAD", sha):
        gitops.git(root, "merge", "-q", "--ff-only", sha)
        return {"packs": n, "merged": True, "fast_forward": True, "conflicts": 0}
    # расхождение: слияние по блокам (драйвер lde); конфликты в тексте — блоками, в прочих файлах —
    # остаётся ваш вариант (серверный — в refs/lde/remote/main, его можно вернуть через «Историю»)
    from lde.merge import install_driver
    install_driver(root)
    gitops.git(root, "merge", "--no-ff", "-m", "Синхронизация с сервером: слияние", sha, check=False)
    conflicts, kept = 0, []
    unmerged = gitops.git(root, "diff", "--name-only", "--diff-filter=U", check=False).split()
    for f in unmerged:
        if f.endswith(".md") and (root / f).exists():
            conflicts += (root / f).read_text(encoding="utf-8", errors="replace").count("<<<<<<< ")
        else:
            gitops.git(root, "checkout", "--ours", "--", f, check=False)
            kept.append(f)
    if unmerged:
        gitops.git(root, "add", "-A")
        gitops.git(root, "commit", "-q", "-m", f"Синхронизация с сервером: слияние (конфликтов в тексте: {conflicts})",
                   author=None if gitops._identity_ok(root) else ("LDE", "lde@lde.local"))
    return {"packs": n, "merged": True, "fast_forward": False, "conflicts": conflicts, "kept_ours": kept}


def subprocess_ok(root: Path, *args) -> bool:
    import subprocess
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode == 0


# ------------------------------------------------------------------ push ----
def _view(root: Path) -> dict | None:
    """Витрина версии для веб-панели: PDF документов (если собраны и не устарели)."""
    import base64
    from lde.project import Project, list_documents
    docs = []
    for dd in list_documents(root):
        p = Project(root, dd["key"])
        pdf = root / "out" / f"{p.name}.pdf"
        if pdf.exists():
            docs.append({"key": dd["key"], "title": p.title, "pdf": base64.b64encode(pdf.read_bytes()).decode()})
    return {"time": time.time(), "commit": gitops.head(root), "documents": docs} if docs else None


def push(root: Path, view: bool = True) -> dict:
    root = Path(root).resolve()
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    srv, pid = _server(root)
    d0 = srv.json("GET", f"/api/projects/{pid}")
    key = _project_key(srv, d0)
    d, refs = _refs(srv, pid, key)
    _apply_packs(root, srv, pid, key)
    head = gitops.head(root)
    main = refs.get("branch:" + BRANCH)
    if main and main["sha"] != head and not subprocess_ok(root, "merge-base", "--is-ancestor", main["sha"], "HEAD"):
        raise ServerError("на сервере есть новое — сначала получите его: lde pull (или «Синхронизировать»)", 409)
    tags = [t for t in gitops.git(root, "tag", "-l", "lde/v/*").split() if t]
    want = {"branch:" + BRANCH: head}
    for t in tags:
        want["tag:" + t] = gitops.git(root, "rev-parse", f"refs/tags/{t}").strip()
    changed = {n: s for n, s in want.items() if refs.get(n, {}).get("sha") != s}
    if not changed:
        return {"pushed": False, "note": "на сервере всё актуально"}
    br = _branch(root)
    refspecs = [f"refs/heads/{br}"] + [f"refs/tags/{t}" for n, t in ((n, n[4:]) for n in changed) if n.startswith("tag:")]
    exclude = [f"^{r['sha']}" for r in refs.values() if subprocess_ok(root, "cat-file", "-e", r["sha"] + "^{commit}")]
    with tempfile.TemporaryDirectory(prefix="lde-push-") as td:
        bpath = Path(td) / "p.bundle"
        import subprocess
        r = subprocess.run(["git", "bundle", "create", "-q", str(bpath), *refspecs, *exclude], cwd=root,
                           capture_output=True, text=True)
        if r.returncode != 0 and "empty bundle" not in r.stderr:
            raise ServerError("git bundle: " + r.stderr.strip())
        if bpath.exists():
            pk = srv.json("PUT", f"/api/projects/{pid}/packs", raw=crypto.seal(key, bpath.read_bytes(), "project-pack", compress=False))
    ups = [{"name": n, "old_version": refs.get(n, {}).get("version", 0),
            "value": crypto.b64e(crypto.seal(key, json.dumps({"sha": s}).encode(), "project-ref:" + n))}
           for n, s in changed.items()]
    srv.json("POST", f"/api/projects/{pid}/refs", {"updates": ups})
    _apply_packs(root, srv, pid, key)          # свой пакет — тоже «получен» (номер)
    if view:
        v = _view(root)
        if v:
            srv.json("PUT", f"/api/projects/{pid}/views",
                     raw=crypto.seal(key, json.dumps(v, ensure_ascii=False).encode(), "project-view"))
    gitops.git(root, "update-ref", "refs/lde/remote/main", head)
    return {"pushed": True, "refs": len(changed)}


def sync(root: Path) -> dict:
    a = pull(root)
    b = push(root) if not a.get("conflicts") else {"pushed": False, "note": "сначала решите конфликты"}
    return {"pull": a, "push": b}


# --------------------------------------------------------- публикация ----
def publish(root: Path, title: str | None = None, server: Server | None = None) -> dict:
    from lde.project import Project
    root = Path(root).resolve()
    if remote_info(root):
        raise ServerError("проект уже на сервере (lde push / lde sync)")
    srv = server or Server()
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    p = Project(root)
    key = crypto.new_key()
    meta = {"title": title or p.title, "name": p.name, "created": time.time()}
    d = srv.json("POST", "/api/projects", {
        "meta_enc": crypto.b64e(crypto.seal(key, json.dumps(meta, ensure_ascii=False).encode(), "project-meta")),
        "key_wrap": crypto.b64e(crypto.wrap_key(srv.pub, key, "project"))})
    _set(root, "url", srv.url)
    _set(root, "project", d["id"])
    _set(root, "seq", 0)
    r = push(root)
    return {"id": d["id"], **r}


def list_projects(server: Server | None = None) -> list[dict]:
    srv = server or Server()
    out = []
    for p in srv.json("GET", "/api/projects"):
        try:
            key = crypto.unwrap_key(srv.priv, crypto.b64d(p["key_wrap"]), "project")
            meta = json.loads(crypto.open_(key, crypto.b64d(p["meta_enc"]), "project-meta"))
        except Exception:
            meta = {"title": "(не расшифровать)"}
        out.append({"id": p["id"], "title": meta.get("title"), "role": p["role"], "owner": p["owner"], "updated": p["updated"]})
    return out


def clone(pid: str, dest: Path, server: Server | None = None) -> dict:
    srv = server or Server()
    dest = Path(dest).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ServerError(f"папка {dest} не пуста")
    dest.mkdir(parents=True, exist_ok=True)
    gitops.git(dest, "init", "-q")
    _set(dest, "url", srv.url)
    _set(dest, "project", pid)
    _set(dest, "seq", 0)
    r = pull(dest)
    gitops.ensure_repo(dest)
    return {"dir": str(dest), **r}


def add_member(root: Path, username: str, role: str) -> dict:
    srv, pid = _server(root)
    d = srv.json("GET", f"/api/projects/{pid}")
    key = _project_key(srv, d)
    pk = srv.json("GET", f"/api/users/{username.lower()}/pubkey")
    srv.json("POST", f"/api/projects/{pid}/members", {
        "username": pk["username"], "role": role,
        "key_wrap": crypto.b64e(crypto.wrap_key(crypto.b64d(pk["pubkey"]), key, "project"))})
    return {"ok": True}
