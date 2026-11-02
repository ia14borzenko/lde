"""LDE Studio — локальный сервер (stdlib) + страница в окне Chrome (режим приложения).

Документ — PDF настоящей вёрстки (страницы рендерятся в PNG, перерисовываются только изменившиеся).
Клик по месту на странице → элемент (карта положений из сборки) → редактор элемента.
Правка → минимальная замена в исходнике → git-коммит → пересборка. Внешние правки (агент)
подхватываются наблюдателем за файлами.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from lde import calcs as calcmod
from lde import gitops
from lde.build import build_pdf
from lde.check import run_check, stale_summary
from lde.project import Project, list_documents
from lde.studio import model

STATIC = Path(__file__).parent / "static"
WATCH_EXCLUDE = (".git", ".lde", "out", "__pycache__")


def human_time(ts: float) -> str:
    lt = time.localtime(ts)
    now = time.localtime()
    hm = time.strftime("%H:%M", lt)
    if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
        return f"сегодня, {hm}"
    if lt.tm_yday == now.tm_yday - 1 and lt.tm_year == now.tm_year:
        return f"вчера, {hm}"
    return time.strftime("%d.%m.%Y, %H:%M", lt)


class Studio:
    def __init__(self, root: Path | None):
        self.root = Path(root).resolve() if root else None
        self.doc = "main"
        self.review = None          # режим разбора правок: {id, building, error, pages, marks, pdf}
        self.lock = threading.RLock()
        self.clients: list[queue.Queue] = []
        self.build_state = {"state": "idle", "time": 0, "duration": 0, "errors": [], "warnings": []}
        self.pages: list[dict] = []
        self.marks: list[dict] = []
        self.need_build = threading.Event()
        self.calc_running: dict | None = None
        self.snapshot = {}
        self.timings: list[dict] = []
        self.proj: Project | None = None
        self.mcp = None             # MCP-сервер для агентов (lde.mcp_server.studio_start)
        self.stop = False
        if self.root:
            self.open(self.root)
        threading.Thread(target=self._builder, daemon=True).start()
        threading.Thread(target=self._watcher, daemon=True).start()

    # ------------------------------------------------------------ project ----
    def open(self, root: Path, doc: str | None = None):
        self.root = Path(root).resolve()
        keys = [d["key"] for d in list_documents(self.root)]
        if doc is None:
            doc = load_view(self.root).get("doc", "main")
        self.doc = doc if doc in keys else "main"
        self.pages, self.marks = [], []
        gitops.ensure_repo(self.root)
        if not gitops.get_seen(self.root) and gitops.head(self.root):
            gitops.set_seen(self.root)
        self.reload()
        self.snapshot = self._scan()
        self._load_map()
        self.need_build.set()
        remember_recent(self.root)

    def close(self):
        with self.lock:
            self.root, self.proj, self.doc = None, None, "main"
            self.pages, self.marks, self.snapshot = [], [], {}
            self.build_state = {"state": "idle", "time": 0, "duration": 0, "errors": [], "warnings": []}

    def switch_doc(self, doc: str):
        if doc not in [d["key"] for d in list_documents(self.root)]:
            raise ValueError(f"нет документа «{doc}»")
        with self.lock:
            self.doc = doc
            self.pages, self.marks = [], []
        self.reload()
        self._load_map()
        save_view(self.root, {"doc": doc})
        self.need_build.set()

    def reload(self):
        with self.lock:
            if self.root is None:
                return None
            self.proj = Project(self.root, self.doc)
        return self.proj

    def project(self) -> Project:
        with self.lock:
            return self.proj

    def emit(self, kind: str, **data):
        msg = json.dumps({"type": kind, **data}, ensure_ascii=False)
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    # -------------------------------------------------------------- build ----
    def _builder(self):
        while not self.stop:
            self.need_build.wait()
            time.sleep(0.12)   # дебаунс серии правок
            self.need_build.clear()
            if not self.root:
                continue
            t0 = time.time()
            self.build_state.update({"state": "building"})
            self.emit("build", state="building")
            try:
                proj = self.reload()
                if proj is None:
                    continue
                st = proj.calc_statuses()
                res = build_pdf(proj, st)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "errors": [{"message": f"сбой сборки: {e}"}]}
                traceback.print_exc()
            if res.get("ok"):
                self._load_map()
                changed = self._page_hashes()
                self.build_state = {"state": "ok", "time": time.time(), "duration": round(time.time() - t0, 2),
                                    "errors": [], "warnings": res.get("warnings", [])[:20]}
                self.timings.append({"t": time.time(), "build": res["duration"], "total": round(time.time() - t0, 2),
                                     "passes": res.get("passes")})
                self.emit("build", state="ok", pages=self.pages, changed=changed, duration=self.build_state["duration"])
            else:
                self.build_state = {"state": "error", "time": time.time(), "duration": round(time.time() - t0, 2),
                                    "errors": res.get("errors", []), "warnings": []}
                self.emit("build", state="error", errors=self.build_state["errors"])
            self.emit("status")

    def _load_map(self):
        mp = self.project().build_dir / "map.json"
        self.marks = []
        if mp.exists():
            try:
                self.marks = json.loads(mp.read_text(encoding="utf-8"))["marks"]
            except Exception:
                self.marks = []
        if not self.pages:
            self._page_hashes()

    def review_png(self, n: int, width: int) -> Path:
        """Страница документа «как у рецензента» (сборка служебной ветки) — PNG с кэшем по хешу."""
        import pymupdf as fitz
        rv = self.review
        h = rv["pages"][n - 1]["hash"]
        cache = self.root / ".lde" / "pages"
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / f"r{h}-{width}.png"
        if not out.exists():
            with fitz.open(rv["pdf"]) as doc:
                pg = doc[n - 1]
                pix = pg.get_pixmap(matrix=fitz.Matrix(width / pg.rect.width, width / pg.rect.width), alpha=False)
                tmp = out.with_suffix(".tmp")
                pix.save(str(tmp), output="png")
                tmp.replace(out)
        return out

    def start_review(self, sid: str) -> dict:
        """Режим разбора правок: забрать копию рецензента и собрать документ «как у рецензента» (в фоне)."""
        from lde import share
        rv = share.pull(self.root, sid)
        self.review = {"id": sid, "building": True, "error": None, "pages": [], "marks": [], "pdf": None}
        self.mark_self_write()

        def work():
            import hashlib as _h
            import pymupdf as fitz
            try:
                r = share.build_review(self.root, sid, self.doc)
                if not r.get("ok"):
                    self.review.update(building=False, error=r.get("error"))
                else:
                    pages = []
                    with fitz.open(r["pdf"]) as d:
                        for i, pg in enumerate(d):
                            hh = _h.sha256()
                            for x in pg.get_contents():
                                hh.update(d.xref_stream_raw(x) or b"")
                            pages.append({"n": i + 1, "hash": hh.hexdigest()[:16], "w": pg.rect.width, "h": pg.rect.height})
                    marks = json.loads(Path(r["map"]).read_text(encoding="utf-8")).get("marks", [])
                    self.review.update(building=False, pages=pages, marks=marks, pdf=r["pdf"])
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.review.update(building=False, error=str(e))
            self.emit("review", state="ready" if not self.review.get("error") else "error", error=self.review.get("error"))
        threading.Thread(target=work, daemon=True).start()
        return rv

    def pdf_path(self) -> Path:
        return self.root / "out" / f"{self.project().name}.pdf"

    def _page_hashes(self) -> list[int]:
        import pymupdf as fitz
        pdf = self.pdf_path()
        if not pdf.exists():
            self.pages = []
            return []
        old = {p["n"]: p["hash"] for p in self.pages}
        pages = []
        with fitz.open(pdf) as doc:
            for i, pg in enumerate(doc):
                h = hashlib.sha256()
                for x in pg.get_contents():
                    h.update(doc.xref_stream_raw(x) or b"")
                for img in pg.get_images(full=True):
                    h.update(hashlib.sha256(doc.xref_stream_raw(img[0]) or b"").digest())
                r = pg.rect
                pages.append({"n": i + 1, "hash": h.hexdigest()[:16], "w": r.width, "h": r.height})
        self.pages = pages
        return [p["n"] for p in pages if old.get(p["n"]) != p["hash"]]

    def page_png(self, n: int, width: int, h: str) -> Path:
        import pymupdf as fitz
        cache = self.root / ".lde" / "pages"
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / f"{h}-{width}.png"
        if out.exists():
            return out
        with fitz.open(self.pdf_path()) as doc:
            pg = doc[n - 1]
            zoom = width / pg.rect.width
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            tmp = out.with_suffix(".tmp")
            pix.save(str(tmp), output="png")
            tmp.replace(out)
        return out

    # ------------------------------------------------------------- locate ----
    def locate(self, page: int, x: float, y: float) -> dict | None:
        """Клик (страница, x, y от верхнего левого угла, в pt) → элемент и его область."""
        from lde.pagemap import locate
        return locate(self.marks, self.pages, page, x, y)

    def rects_of(self, uid: str) -> list[dict]:
        from lde.pagemap import element_rects
        return element_rects(self.marks, self.pages, uid) if self.pages else []

    # ------------------------------------------------------------ watcher ----
    def _scan(self) -> dict:
        snap = {}
        if not self.root:
            return snap
        for dp, dns, fns in os.walk(self.root):
            rel = os.path.relpath(dp, self.root)
            parts = rel.split(os.sep)
            dns[:] = [d for d in dns if d not in WATCH_EXCLUDE and not (len(parts) >= 2 and parts[0] == "calc" and d == "out")
                      and not (parts == ["calc", parts[-1]] and d == "out")]
            if "out" in parts and "calc" in parts:
                continue
            for f in fns:
                if f.endswith((".lde-tmp", ".tmp", ".pyc")):
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    snap[os.path.relpath(p, self.root)] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
        # выходы расчётов — по времени .run.json
        for rj in (self.root / "calc").glob("*/out/.run.json") if self.root else []:
            snap[str(rj.relative_to(self.root))] = (rj.stat().st_mtime_ns, rj.stat().st_size)
        return snap

    def mark_self_write(self):
        self.snapshot = self._scan()

    def _watcher(self):
        while not self.stop:
            time.sleep(0.6)
            if not self.root:
                continue
            try:
                snap = self._scan()
            except Exception:
                continue
            if snap != self.snapshot:
                changed = sorted(set(k for k in snap if snap.get(k) != self.snapshot.get(k)) |
                                 set(k for k in self.snapshot if k not in snap))
                self.snapshot = snap
                self.reload()
                self.emit("external", files=changed[:20])
                self.need_build.set()

    # -------------------------------------------------------------- state ----
    def state(self) -> dict:
        p = self.project()
        if p is None:
            return {"project": None, "recent": recent_projects(), "prefs": load_prefs()}
        st = p.calc_statuses()
        stale = stale_summary(p, st)
        issues = run_check(p, st)
        ne = sum(i.severity == "error" for i in issues)
        nw = sum(i.severity == "warning" for i in issues)
        outline, objects = [], []
        for b in p.blocks:
            if b.kind == "heading":
                outline.append({"uid": b.uid, "level": b.level, "number": p.numbers.get(id(b), ""), "text": strip_markup(b.text)})
            elif b.kind in ("figure", "table") or (b.kind == "formula" and p.numbers.get(id(b))):
                objects.append({"uid": b.uid, "kind": b.kind, "title": p.title_of(b),
                                "text": strip_markup(b.text)[:90] if b.kind != "formula" else "",
                                "latex": b.math if b.kind == "formula" else None})
        calcs = []
        for k, c in p.calcs.items():
            s = st[k]
            info = c.run_info() or {}
            calcs.append({"key": k, "title": c.name, "state": s.state, "reasons": s.reasons,
                          "failure": (s.failure or {}).get("error"), "when": human_time(info["time"]) if info.get("time") else "",
                          "duration": info.get("duration"),
                          "params": [{"name": n, "value": v, **c.params_meta.get(n, {})} for n, v in c.params.items()],
                          "running": bool(self.calc_running and self.calc_running.get("key") == k)})
        qs = [{"key": q.key, "latex": q.tex, "meaning": q.meaning, "unit": q.unit} for q in p.quantities.values()]
        srcs = []
        from lde.bib import format_entry
        for k, e in p.bib.items():
            srcs.append({"key": k, "n": p.cite_num.get(k), "text": format_entry(e), "file": e.get("file")})
        try:
            ch = model.changes(self.root, self.doc)
            nchanges = ch["count"]
        except Exception:
            nchanges = 0
        return {
            "project": {"root": str(self.root), "title": p.title, "name": p.name, "profile": p.doc_cfg.get("profile"),
                        "doc": self.doc, "documents": list_documents(self.root), "folder": self.root.name},
            "recent": recent_projects(), "view": load_view(self.root).get("docs", {}).get(self.doc, {}),
            "prefs": load_prefs(),
            "outline": outline, "objects": objects, "calcs": calcs, "quantities": qs, "sources": srcs,
            "abbreviations": [{"key": k, "text": v} for k, v in p.abbreviations.items()],
            "status": {"stale": stale, "errors": ne, "warnings": nw},
            "review": sum(1 for i in __import__("lde.review", fromlist=["x"]).load(self.root)
                          if not i["done"] and i.get("doc", "main") == self.doc),
            "build": self.build_state, "pages": self.pages, "changes": nchanges,
            "history": [{"subject": h["subject"], "author": h["author"], "when": human_time(h["time"])}
                        for h in gitops.log(self.root, 12)],
        }


def strip_markup(s: str) -> str:
    s = re.sub(r"\{=([^}\s]+)[^}]*\}", r"[\1]", s)
    s = re.sub(r"\{([^}]+)\}", r"\1", s)
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    return s.replace("\\", "")


# ----------------------------------------------------------- recent list ----
def _cfg_dir() -> Path:
    d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lde"
    return d


def recent_projects() -> list[dict]:
    p = _cfg_dir() / "recent.json"
    try:
        lst = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for x in lst:
        if (Path(x) / "lde.toml").exists():
            out.append({"path": x, "name": Path(x).name})
    return out[:10]


def remember_recent(root: Path):
    if os.environ.get("LDE_NO_RECENT"):
        return
    d = _cfg_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        lst = [str(root)] + [x["path"] for x in recent_projects() if x["path"] != str(root)]
        (d / "recent.json").write_text(json.dumps(lst[:10], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------- настройки и позиция ----
PREF_KEYS = {"theme": ("light", "dark"), "zoom": None}


def load_prefs() -> dict:
    """Настройки интерфейса (тема, масштаб) — общие для всех запусков: Studio каждый раз на новом
    порту, хранилище браузера теряется, поэтому они лежат в ~/.config/lde/prefs.json."""
    try:
        d = json.loads((_cfg_dir() / "prefs.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_prefs(d: dict) -> dict:
    cur = load_prefs()
    for k, v in d.items():
        if k == "theme" and v in ("light", "dark"):
            cur[k] = v
        elif k == "zoom" and isinstance(v, (int, float)) and 0.3 <= v <= 4:
            cur[k] = round(float(v), 3)
    try:
        _cfg_dir().mkdir(parents=True, exist_ok=True)
        tmp = _cfg_dir() / "prefs.json.tmp"
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_cfg_dir() / "prefs.json")
    except OSError:
        pass
    return {"ok": True, "prefs": cur}


def _view_path(root: Path) -> Path:
    return root / ".lde" / "studio-view.json"


def load_view(root: Path) -> dict:
    """Где закрыли Studio в этом проекте: документ, страница, доля прокрутки страницы."""
    try:
        d = json.loads(_view_path(root).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_view(root: Path, upd: dict):
    cur = load_view(root)
    if "doc" in upd:
        cur["doc"] = upd["doc"]
    for k, v in (upd.get("docs") or {}).items():
        cur.setdefault("docs", {}).setdefault(k, {}).update(v)
    try:
        _view_path(root).parent.mkdir(exist_ok=True)
        tmp = _view_path(root).with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_view_path(root))
    except OSError:
        pass


def default_projects_dir() -> Path:
    try:
        d = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        d = ""
    return Path(d or Path.home() / "Documents") / "LDE"


# ----------------------------------------------------------------- HTTP ----
class Handler(BaseHTTPRequestHandler):
    studio: Studio = None
    server_version = "LDEStudio/2"

    def log_message(self, fmt, *args):
        if os.environ.get("LDE_STUDIO_LOG"):
            super().log_message(fmt, *args)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, p: Path, cache=False):
        if not p.exists() or not p.is_file():
            self.send_error(404)
            return
        data = p.read_bytes()
        ct = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if p.suffix == ".js":
            ct = "text/javascript; charset=utf-8"
        elif p.suffix in (".css", ".html"):
            ct += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(raw or b"{}")
        return raw

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        s = self.studio
        try:
            if u.path in ("/", "/index.html"):
                return self._file(STATIC / "index.html")
            if u.path.startswith("/static/"):
                rel = unquote(u.path[len("/static/"):])
                p = (STATIC / rel).resolve()
                if STATIC.resolve() not in p.parents:
                    return self.send_error(403)
                return self._file(p, cache="vendor/" in rel)
            if u.path == "/api/state":
                return self._json(s.state())
            if u.path.startswith("/api/page/"):
                n = int(re.match(r"/api/page/(\d+)", u.path).group(1))
                if not s.pages or n > len(s.pages):
                    return self.send_error(404)
                h = s.pages[n - 1]["hash"]
                return self._file(s.page_png(n, int(q.get("w", 900)), h), cache=True)
            if u.path == "/api/element":
                p = s.project()
                b = p.block_by_uid(q["uid"])
                if b is None:
                    return self._json({"error": "элемент не найден (возможно, его удалили)"}, 404)
                d = model.describe(p, b)
                d["rects"] = s.rects_of(b.uid)
                return self._json(d)
            if u.path == "/api/locate":
                r = s.locate(int(q["page"]), float(q["x"]), float(q["y"]))
                return self._json(r or {})
            if u.path == "/api/search":
                return self._json(search(s.project(), q.get("q", ""), q.get("kind", "")))
            if u.path == "/api/issues":
                p = s.project()
                iss = run_check(p)
                return self._json([dict(i.to_dict(), human=i.human(), title=(p.title_of(p.block_by_uid(i.element)) if i.element and p.block_by_uid(i.element) else ""))
                                   for i in iss if i.severity != "info"])
            if u.path == "/api/changes":
                return self._json(model.changes(s.root, s.doc))
            if u.path == "/api/compare":
                from lde import versions
                base = versions.resolve(s.root, q["base"])
                target = versions.resolve(s.root, q["target"]) if q.get("target") else None
                return self._json(model.changes(s.root, s.doc, base, target))
            if u.path == "/api/history":
                from lde import versions
                return self._json({"sessions": [dict(x, when=human_time(x["end"]), since=human_time(x["start"]))
                                                for x in versions.history(s.root)],
                                   "versions": [dict(v, when=human_time(v["time"])) for v in versions.list_versions(s.root)],
                                   "can_redo": versions.can_redo(s.root), "head": gitops.head(s.root)})
            if u.path == "/api/settings":
                return self._json(settings_get(s.project()))
            if u.path == "/api/review":
                from lde import review
                return self._json([i for i in review.load(s.root) if i.get("doc", "main") == s.doc])
            if u.path == "/api/quantity":
                return self._json(quantity_get(s.project(), q["key"]))
            if u.path == "/api/source":
                return self._json(source_get(s.project(), q["key"]))
            if u.path == "/api/calc":
                return self._json(calc_get(s, q["key"]))
            if u.path == "/api/file":
                p = (s.root / q["path"]).resolve()
                if s.root not in p.parents:
                    return self.send_error(403)
                return self._file(p)
            if u.path == "/api/pdf":
                # PDF открывается самим Studio (просмотрщик Chrome): не зависит от внешних программ
                # и их ограничений доступа к файлам
                pdf = s.pdf_path()
                if not pdf.exists():
                    return self._json({"error": "PDF ещё не собран"}, 404)
                data = pdf.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{pdf.name}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if u.path == "/api/remote":
                from lde import sync
                return self._json(sync.remote_info(s.root) or {})
            if u.path == "/api/mcp":
                from lde import mcp_server as ms
                return self._json(ms.studio_info(s))
            if u.path == "/api/feedback":
                from lde import feedback as fb
                if q.get("id"):
                    return self._json({"text": fb.show(q["id"], s.root)})
                return self._json(fb.list_reports(True, s.root))
            if u.path == "/api/rects":
                return self._json({"rects": s.rects_of(q["uid"])})
            if u.path == "/api/review/state":
                rv = s.review or {}
                return self._json({k: rv.get(k) for k in ("id", "building", "error", "pages")})
            if u.path == "/api/review/rects":
                from lde.pagemap import element_rects
                rv = s.review or {}
                return self._json({"rects": element_rects(rv.get("marks") or [], rv.get("pages") or [], q["uid"])})
            if u.path.startswith("/api/review/page/"):
                n = int(re.match(r"/api/review/page/(\d+)", u.path).group(1))
                rv = s.review or {}
                if not rv.get("pages") or n > len(rv["pages"]):
                    return self.send_error(404)
                return self._file(s.review_png(n, int(q.get("w", 900))), cache=True)
            if u.path == "/api/review/tokens":
                from lde.textfmt import parse_text
                bl = [b for b in parse_text(q.get("src", "")) if b.kind != "comment"]
                p = s.project()
                return self._json({"tokens": model.tokens(p, bl[0].src, bl[0].inl, 0) if bl and bl[0].inl else
                                   [{"t": "text", "v": q.get("src", ""), "src": q.get("src", "")}]})
            if u.path == "/api/server/status":
                from lde import server_client as sc
                return self._json(sc.status())
            if u.path == "/api/shares":
                from lde import share
                from lde.server_client import ServerError
                try:
                    return self._json(share.list_shares(s.root, doc=s.doc))
                except ServerError as e:
                    return self._json({"error": str(e)}, 400)
            if u.path == "/api/share/pull":
                from lde import share
                from lde.server_client import ServerError
                try:
                    r = share.pull(s.root, q["id"])
                except ServerError as e:
                    return self._json({"error": str(e)}, 400)
                s.mark_self_write()
                return self._json(r)
            if u.path == "/api/prefs":
                return self._json(load_prefs())
            if u.path == "/api/timings":
                return self._json(s.timings[-50:])
            if u.path == "/api/events":
                return self._events()
            self.send_error(404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"внутренняя ошибка: {e}"}, 500)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        qu: queue.Queue = queue.Queue(maxsize=200)
        self.studio.clients.append(qu)
        try:
            self.wfile.write(b": hello\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = qu.get(timeout=5)
                    self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if qu in self.studio.clients:
                self.studio.clients.remove(qu)

    def do_POST(self):
        u = urlparse(self.path)
        s = self.studio
        try:
            if u.path == "/api/upload":
                return self._upload(parse_qs(u.query))
            d = self._body()
            r = self.post(u.path, d)
            if r is None:
                return self.send_error(404)
            code = r.pop("_code", 200) if isinstance(r, dict) else 200
            return self._json(r, code)
        except model.ConflictError as e:
            return self._json({"error": str(e), "conflict": True, "current": e.current}, 409)
        except (ValueError, KeyError, RuntimeError) as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"внутренняя ошибка: {e}"}, 500)

    def post(self, path, d):
        s = self.studio
        if path == "/api/open-project":
            p = Path(d["path"]).expanduser()
            if not (p / "lde.toml").exists():
                return {"error": "в этой папке нет проекта LDE (lde.toml)", "_code": 400}
            s.open(p)
            return {"ok": True}
        if path == "/api/prefs":
            return save_prefs(d)
        if path == "/api/close-project":
            s.close()
            return {"ok": True}
        if path == "/api/new-project":
            from lde.cli import ROOT
            name = re.sub(r"[/\\]", "-", d.get("name") or "Новый отчёт").strip()
            dst = default_projects_dir() / name
            if dst.exists() and any(dst.iterdir()):
                return {"error": f"Папка {dst} уже занята — выберите другое название", "_code": 400}
            shutil.copytree(ROOT / "template" / "example", dst, dirs_exist_ok=True)
            gitops.ensure_repo(dst)
            gitops.commit(dst, "Новый отчёт (LDE Studio)")
            s.open(dst)
            threading.Thread(target=lambda: run_calcs(s, None), daemon=True).start()
            return {"ok": True, "path": str(dst)}
        if path in ("/api/mcp/start", "/api/mcp/stop", "/api/mcp/new-key"):
            from lde import mcp_server as ms
            if path.endswith("start"):
                return ms.studio_start(s, int(d.get("port") or 0) or None)
            if path.endswith("new-key"):
                ms.rotate_token()
                ms.studio_stop(s)
                return ms.studio_start(s)
            return ms.studio_stop(s)
        if path == "/api/feedback/status":
            from lde import feedback as fb
            fb.set_status(d["id"], d["status"], s.root)
            return {"ok": True}
        if path == "/api/server/login":
            from lde import server_client as sc
            try:
                return {"ok": True, **sc.login(d["url"], d["username"], d["password"])}
            except sc.ServerError as e:
                return {"error": str(e), "_code": 400}
        if path == "/api/server/logout":
            from lde import server_client as sc
            sc.logout()
            return {"ok": True}
        if s.root is None:
            return {"error": "проект не открыт", "_code": 400}
        if path.startswith(("/api/share/", "/api/review/")) or path in ("/api/sync", "/api/publish"):
            return share_post(s, path, d)
        if path == "/api/view":
            save_view(s.root, {"doc": s.doc, "docs": {s.doc: {k: d[k] for k in ("page", "frac", "zoom") if k in d}}})
            return {"ok": True}
        if path == "/api/document":
            s.switch_doc(d["key"])
            return {"ok": True}
        if path == "/api/new-document":
            from lde.cli_more import new_document
            r = new_document(s.root, d.get("key") or "", d.get("title") or "")
            if not r.get("ok"):
                return {"error": r["error"], "_code": 400}
            s.mark_self_write()
            s.switch_doc(r["key"])
            return r
        if path == "/api/element/save":
            p = s.project()
            b = model.find_block(p, d["uid"], d["base"])
            if "tokens" in d:
                old = model.describe(p, b)
                old_tokens = old.get("tokens", [])
                body = model.detokenize(d["tokens"], old_tokens)
                a, z = model.block_text_span(b)
                new_src = b.src[:a] + body + b.src[z:]
            else:
                new_src = d["src"]
            if "\\htmlData{lde=" in new_src:          # связанные вставки из MathLive → \q{…}/\val{…}
                from lde import mathins
                new_src = mathins.from_ml(new_src)
            r = model.save_block(s.root, b.uid, d["base"], new_src, doc=s.doc)
            s.mark_self_write()
            s.reload()
            if r.get("changed"):
                s.need_build.set()
            nb = s.project().block_by_uid(d["uid"]) if r.get("changed") and not d["uid"].count("#") else None
            r["uid"] = nb.uid if nb else d["uid"]
            return r
        if path == "/api/element/style":
            r = model.set_style(s.root, d["uid"], d["base"], d.get("style"), doc=s.doc)
            s.mark_self_write()
            s.reload()
            if r.get("changed"):
                s.need_build.set()
            return r
        if path == "/api/element/insert":
            from lde import mathins
            r = model.insert_block(s.root, d.get("after"), mathins.from_ml(d["src"]), doc=s.doc)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/fix":
            from lde.fixer import apply_fixes, plan_fixes
            p = s.project()
            gitops.checkpoint_external(s.root)
            only = {(x["file"], x["start"]) for x in d.get("only", [])} or None
            plan = plan_fixes(p, None, only)
            done = apply_fixes(p, plan)
            if done:
                gitops.commit(s.root, f"Studio: исправлено по замечаниям ({len(done)})", author=gitops.STUDIO_AUTHOR)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return {"fixed": len(done)}
        if path == "/api/param":
            r = set_param(s.root, d["calc"], d["name"], d["value"])
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/quantity":
            r = quantity_set(s.root, d)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/source":
            r = source_set(s.root, d)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/review/done":
            from lde import review
            review.set_done(s.root, int(d["n"]), bool(d.get("done", True)))
            gitops.commit(s.root, f"Studio: примечание {d['n']} {'выполнено' if d.get('done', True) else 'снова открыто'}",
                          author=gitops.STUDIO_AUTHOR, paths=["review"])
            s.mark_self_write()
            return {"ok": True}
        if path == "/api/abbreviations":
            gitops.checkpoint_external(s.root)
            lines = ["# Сокращения: попадают в перечень сокращений"]
            for it in d.get("items", []):
                lines.append(f"{json.dumps(it['key'], ensure_ascii=False)} = {json.dumps(it['text'], ensure_ascii=False)}")
            model.write_file(s.root, "abbreviations.toml", "\n".join(lines) + "\n")
            gitops.commit(s.root, "Studio: перечень сокращений", author=gitops.STUDIO_AUTHOR, paths=["abbreviations.toml"])
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return {"ok": True}
        if path == "/api/settings":
            r = settings_set(s.root, d, s.project().config_file)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/recalc":
            if s.calc_running:
                return {"error": "расчёт уже идёт", "_code": 409}
            threading.Thread(target=lambda: run_calcs(s, d.get("calcs")), daemon=True).start()
            return {"ok": True}
        if path in ("/api/undo", "/api/redo", "/api/restore", "/api/version"):
            from lde import versions
            if path == "/api/undo":
                r = versions.undo(s.root)
                r = {"ok": True, "undone": r["subject"], "can_redo": True}
            elif path == "/api/redo":
                r = versions.redo(s.root)
                r = {"ok": True, "redone": r["subject"], "can_redo": r["can_redo"]}
            elif path == "/api/restore":
                r = versions.restore(s.root, d["ref"], d.get("only"))
            else:
                return versions.create(s.root, d.get("name", ""), d.get("note", ""), d.get("kind", "manual"))
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/changes/seen":
            gitops.checkpoint_external(s.root)
            gitops.set_seen(s.root)
            s.mark_self_write()
            return {"ok": True}
        if path == "/api/changes/revert":
            r = model.revert_change(s.root, d["item"], doc=s.doc)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/submit":
            if s.calc_running:
                return {"error": "идёт расчёт — дождитесь окончания", "_code": 409}

            def work():
                from lde.submit import submit
                s.calc_running = {"key": "_submit", "title": "Сдача"}
                try:
                    r = submit(s.root, log=lambda *a: None, doc=s.doc,
                               progress=lambda i, n, t: s.emit("submit", state="running", step=i, total=n, text=t))
                    s.emit("submit", state="done", **r)
                except Exception as e:  # noqa: BLE001
                    s.emit("submit", state="done", ok=False, blockers=[f"сбой: {e}"], notes=[])
                finally:
                    s.calc_running = None
                    s.mark_self_write()
                    s.reload()
                    s.need_build.set()
            threading.Thread(target=work, daemon=True).start()
            return {"ok": True}
        if path == "/api/export":
            return export(s, d.get("format", "pdf"))
        if path == "/api/open":
            return open_external(s, d.get("what"), d.get("path"))
        if path == "/api/rebuild":
            s.need_build.set()
            return {"ok": True}
        return None

    def _upload(self, q):
        """Файл из браузера: рисунок (в figures/) или DOCX для импорта."""
        s = self.studio
        name = Path(unquote(q.get("name", ["file"])[0])).name
        data = self._body()
        kind = q.get("kind", ["figure"])[0]
        if kind == "review" and s.root is not None:
            from lde import review
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / name
                src.write_bytes(data)
                items = review.import_review(s.root, src, doc=s.doc)
            gitops.commit(s.root, f"Примечания рецензента из {name}", author=gitops.STUDIO_AUTHOR, paths=["review"])
            s.mark_self_write()
            return self._json({"ok": True, "count": len(items)})
        if kind == "docx":
            from lde.importers.docx_import import import_docx
            title = Path(name).stem
            dst = default_projects_dir() / title
            if dst.exists() and any(dst.iterdir()):
                dst = default_projects_dir() / f"{title}-{time.strftime('%Y%m%d-%H%M')}"
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / name
                src.write_bytes(data)
                rep = import_docx(src, dst)
            gitops.ensure_repo(dst)
            gitops.commit(dst, f"Импорт из {name} (LDE Studio)")
            s.open(dst)
            return self._json({"ok": True, "path": str(dst), "report": "import/report.md",
                               "counts": rep.counts, "approx": len(rep.approx), "lost": len(rep.lost)})
        if s.root is None:
            return self._json({"error": "проект не открыт"}, 400)
        figs = s.root / "figures"
        figs.mkdir(exist_ok=True)
        stem = re.sub(r"[^\w\-.]+", "-", Path(name).stem)[:40] or "image"
        ext = Path(name).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".pdf"):
            return self._json({"error": "нужен рисунок PNG, JPG или PDF"}, 400)
        p = figs / f"{stem}{ext}"
        k = 2
        while p.exists():
            p = figs / f"{stem}-{k}{ext}"
            k += 1
        p.write_bytes(data)
        return self._json({"ok": True, "path": str(p.relative_to(s.root))})


# --------------------------------------------------------------- helpers ----
def search(p: Project, q: str, kind: str) -> list[dict]:
    ql = q.lower().strip()
    res = []

    def ok(*fields):
        return not ql or any(ql in (f or "").lower() for f in fields)

    if kind in ("", "q"):
        for k, x in p.quantities.items():
            if ok(k, x.meaning):
                res.append({"kind": "q", "src": "{" + k + "}", "latex": x.tex, "text": k,
                            "title": ", ".join(t for t in (x.meaning or "смысл не указан", x.unit) if t)})
    if kind in ("", "val"):
        from lde.fmt import format_value
        for ck, c in p.calcs.items():
            for path, v in calcmod.all_value_paths(c):
                meta = calcmod.lookup_value(c, path) or {}
                if ok(path, meta.get("title"), c.name):
                    try:
                        t = format_value(v, meta.get("unit", ""), meta.get("digits"))["text"]
                    except Exception:
                        t = str(v)
                    res.append({"kind": "val", "src": "{=" + f"{ck}.{path}" + "}", "text": t,
                                "title": f"{meta.get('title') or path} — «{c.name}»"})
    if kind in ("", "ref"):
        for lab, t in p.labels.items():
            num = f"({t.number})" if t.kind == "eq" else t.number
            what = {"fig": "Рисунок", "tab": "Таблица", "eq": "Формула", "sec": "Раздел", "app": "Приложение"}[t.kind]
            title = strip_markup(t.title)[:80]
            if ok(lab, title, f"{what} {num}"):
                res.append({"kind": "ref", "src": "@" + lab, "text": num, "title": f"{what} {num}: {title}"})
    if kind in ("", "cite"):
        from lde.bib import format_entry
        for k, e in p.bib.items():
            t = format_entry(e)
            if ok(k, t):
                res.append({"kind": "cite", "src": f"[@{k}]", "text": f"[{p.cite_num.get(k, '?')}]", "title": t[:120]})
    return res[:200]


def _toml(path: Path):
    import tomlkit
    return tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()


def _save_toml(root: Path, rel: str, doc, msg: str):
    import tomlkit
    model.write_file(root, rel, tomlkit.dumps(doc))
    gitops.commit(root, msg, author=gitops.STUDIO_AUTHOR, paths=[rel])


def _num(v):
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", ".").replace(" ", "").replace(" ", "")
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"«{v}» — не число")


def set_param(root: Path, calc: str, name: str, value):
    import tomlkit
    gitops.checkpoint_external(root)
    rel = f"calc/{calc}/calc.toml"
    doc = _toml(root / rel)
    params = doc.get("params")
    if params is None or name not in params:
        raise ValueError(f"в расчёте {calc} нет параметра {name}")
    cur = params[name]
    old_v = cur["value"] if isinstance(cur, dict) else cur
    nv = _num(value)
    if isinstance(old_v, float) and isinstance(nv, int):
        nv = float(nv)
    if isinstance(cur, dict):
        cur["value"] = nv
    else:
        params[name] = nv
    _save_toml(root, rel, doc, f"Studio: параметр {name} расчёта «{calc}»: {old_v} → {nv}")
    return {"ok": True}


def calc_get(s: Studio, key: str) -> dict:
    p = s.project()
    c = p.calcs[key]
    st = calcmod.status(p.root, c, p.calcs)
    info = c.run_info() or {}
    log = ""
    lp = c.out_dir / ".log.txt"
    if st.failure:
        fl = p.root / st.failure.get("log", "")
        if fl.exists():
            log = fl.read_text(encoding="utf-8", errors="replace")[-6000:]
    elif lp.exists():
        log = lp.read_text(encoding="utf-8", errors="replace")[-6000:]
    figs = [b for b in p.blocks if b.kind == "figure" and key in p.block_calcs(b)]
    return {"key": key, "title": c.name, "script": f"calc/{key}/{c.script}", "state": st.state, "reasons": st.reasons,
            "failure": st.failure, "inputs": c.inputs, "outputs": c.outputs,
            "params": [{"name": n, "value": v, "pretty": _pretty(v, c.params_meta.get(n, {}).get("unit", "")),
                        **c.params_meta.get(n, {})} for n, v in c.params.items()],
            "when": human_time(info["time"]) if info.get("time") else "", "duration": info.get("duration"),
            "isolated": info.get("isolated_network"), "log": log,
            "figures": [{"uid": b.uid, "title": p.title_of(b)} for b in figs],
            "values": [{"path": pth, "value": v} for pth, v in calcmod.all_value_paths(c)][:80]}


def _pretty(v, unit):
    from lde.fmt import format_value
    try:
        return format_value(v, unit)["text"] if isinstance(v, (int, float)) else str(v)
    except Exception:
        return str(v)


def run_calcs(s: Studio, keys):
    p = s.project()
    order = calcmod.topo_order(p.calcs, list(p.calcs))
    try:
        for k in order:
            c = p.calcs[k]
            st = calcmod.status(p.root, c, p.calcs)
            if keys and k not in keys:
                continue
            if not keys and st.state == "ok":
                continue
            s.calc_running = {"key": k, "title": c.name, "start": time.time()}
            s.emit("calc", state="running", key=k, title=c.name)
            r = calcmod.run_calc(p.root, c, p.calcs)
            s.emit("calc", state="ok" if r["ok"] else "error", key=k, title=c.name, error=r.get("error"),
                   duration=round(r.get("duration", 0), 1))
    finally:
        s.calc_running = None
        s.mark_self_write()
        s.reload()
        s.need_build.set()
        s.emit("status")


def quantity_get(p: Project, key: str) -> dict:
    q = p.quantities.get(key)
    if not q:
        return {"key": key, "new": True, "meaning": "", "unit": "", "latex": "", "auto_latex": __import__("lde.quantities", fromlist=["x"]).latex_from_key(key)}
    uses = sum(1 for b in p.blocks for nd in p.inline_nodes(b) if nd.k == "q" and nd.v == key)
    from lde.quantities import latex_from_key
    return {"key": key, "meaning": q.meaning, "unit": q.unit, "latex": q.latex, "auto_latex": latex_from_key(key),
            "value": q.value, "uses": uses}


def quantity_set(root: Path, d: dict):
    import tomlkit
    gitops.checkpoint_external(root)
    key = d["key"].strip()
    if not key or re.search(r"[\s{}=]", key):
        raise ValueError("обозначение без пробелов и скобок, например R_ц или f_0")
    doc = _toml(root / "quantities.toml")
    new = key not in doc
    t = doc[key] if not new else tomlkit.table()
    for f in ("meaning", "unit", "latex"):
        if f in d:
            if d[f] or f != "latex":
                t[f] = d[f]
            elif "latex" in t:
                del t["latex"]
    if new:
        doc[key] = t
    _save_toml(root, "quantities.toml", doc, f"Studio: {'новая величина' if new else 'величина'} {key}")
    return {"ok": True, "src": "{" + key + "}"}


def source_get(p: Project, key: str) -> dict:
    from lde.bib import format_entry
    e = p.bib.get(key)
    if not e:
        return {"key": key, "new": True, "fields": {}}
    return {"key": key, "type": e.type, "fields": e.fields, "text": format_entry(e), "n": p.cite_num.get(key)}


def source_set(root: Path, d: dict):
    """Создать/заменить запись refs.bib (точечно — только эту запись)."""
    from lde.bib import parse_bib
    gitops.checkpoint_external(root)
    path = root / "refs.bib"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    key = d["key"].strip()
    if not re.fullmatch(r"[\w\-:.]+", key):
        raise ValueError("ключ источника — латиницей, без пробелов (например cumming2005)")
    from lde.bib import replace_entry
    text = replace_entry(text, key, d.get("type", "book"), d.get("fields", {}))
    model.write_file(root, "refs.bib", text)
    gitops.commit(root, f"Studio: источник {key}", author=gitops.STUDIO_AUTHOR, paths=["refs.bib"])
    return {"ok": True, "src": f"[@{key}]"}


def share_post(s: "Studio", path: str, d: dict) -> dict:
    """Ссылки на документ: создать, решить по правке, ответить, отозвать (сервер LDE, см. lde/share.py)."""
    from lde import share
    from lde.server_client import ServerError
    try:
        if path == "/api/share/new":
            r = share.create(s.root, s.doc, d.get("label", ""), "comment" if d.get("perms") == "comment" else "edit",
                             float(d.get("days") or 30), d.get("pin") or "", bool(d.get("pdf", True)))
            s.mark_self_write()
            return r
        if path == "/api/share/decide":
            r = share.decide(s.root, d["id"], d["block"], d["how"], d.get("text"))
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/share/reply":
            return share.reply(s.root, d["id"], d["comment"], d.get("text", ""), d.get("status"))
        if path in ("/api/sync", "/api/publish"):
            from lde import sync
            r = sync.publish(s.root, d.get("title") or None) if path == "/api/publish" else sync.sync(s.root)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/review/start":
            return s.start_review(d["id"])
        if path == "/api/review/stop":
            s.review = None
            return {"ok": True}
        if path == "/api/review/own":
            text = d.get("src")
            if text is None:
                text = share.own_from_tokens(s.root, d["template"], d.get("tokens") or [], s.doc)
            elif "\\htmlData{lde=" in text:
                from lde import mathins
                text = mathins.from_ml(text)
            r = share.decide(s.root, d["id"], d["block"], "own", text)
            s.mark_self_write()
            s.reload()
            s.need_build.set()
            return r
        if path == "/api/share/finish":
            r = share.finish(s.root, d["id"])
            s.review = None
            s.mark_self_write()
            return r
        if path == "/api/share/revoke":
            share.revoke(d["id"], bool(d.get("revoked", True)))
            return {"ok": True}
    except ServerError as e:
        return {"error": str(e), "_code": 400}
    return {"error": "нет такого действия", "_code": 404}


def settings_get(p: Project) -> dict:
    from lde.profiles import get_profile, list_profiles
    prof = get_profile(p.doc_cfg.get("profile", "gost-7.32"))
    return {"document": {k: p.doc_cfg.get(k) for k in ("profile", "numbering", "block_spacing", "hyphenation", "toc", "abbreviations", "symbols", "bibliography", "name")},
            "title": {k: str(v) for k, v in p.config.get("title", {}).items() if not isinstance(v, list)},
            "fields": [{"key": k, "label": lbl} for k, lbl in prof.fields],
            "profiles": [{"key": k, "title": t} for k, t in list_profiles()],
            "title_lines": len(p.config.get("title", {}).get("lines", []) or [])}


def settings_set(root: Path, d: dict, cfg_file: str = "lde.toml"):
    import tomlkit
    gitops.checkpoint_external(root)
    doc = _toml(root / cfg_file)
    if "document" not in doc:
        doc["document"] = tomlkit.table()
    for k, v in (d.get("document") or {}).items():
        if k in ("toc", "abbreviations", "symbols", "bibliography", "hyphenation"):
            v = bool(v)
        doc["document"][k] = v
    if d.get("title") is not None:
        if "title" not in doc:
            doc["title"] = tomlkit.table()
        for k, v in d["title"].items():
            doc["title"][k] = v
    _save_toml(root, cfg_file, doc, "Studio: настройки документа")
    return {"ok": True}


def export(s: Studio, fmt: str) -> dict:
    p = s.project()
    if fmt == "pdf":
        pdf = s.pdf_path()
        if not pdf.exists():
            return {"error": "PDF ещё не собран", "_code": 400}
        return {"ok": True, "path": str(pdf)}
    if fmt == "docx":
        try:
            from lde.docx_export import export_docx
        except ImportError:
            return {"error": "экспорт DOCX не реализован в этой версии", "_code": 501}
        s.emit("export", state="running", format="docx")
        r = export_docx(p)
        s.emit("export", state="ok" if r.get("ok") else "error", format="docx", **{k: v for k, v in r.items() if k != "ok"})
        return r
    return {"error": f"неизвестный формат {fmt}", "_code": 400}


def open_external(s: Studio, what: str, path: str | None = None) -> dict:
    target = {"folder": str(s.root), "pdf": str(s.pdf_path()), "docx": str(s.root / "out" / f"{s.project().name}.docx"),
              "file": str((s.root / path).resolve()) if path else ""}.get(what, "")
    if what == "vscode":
        cmd = ["code", str(s.root)]
    else:
        if not target:
            return {"error": "нечего открыть", "_code": 400}
        cmd = ["xdg-open", target]
    if os.environ.get("LDE_NO_DESKTOP"):
        return {"ok": True, "skipped": True, "command": cmd}
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return {"error": f"не найдена программа {cmd[0]}", "_code": 400}
    return {"ok": True}


def serve(root: Path | None, port: int = 0, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, Studio]:
    st = Studio(root)
    Handler.studio = st
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    if not os.environ.get("LDE_NO_MCP"):
        from lde import mcp_server
        mcp_server.studio_autostart(st)
    return httpd, st
