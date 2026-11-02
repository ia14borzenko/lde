"""Ссылка на документ для рецензента: снимок, личная копия, приём правок по блокам.

Поток:
  create()  — версия-тег lde/share/…, снимок документа «просто текстом» (вставки — непрозрачными
              номерами), шифрование ключом ссылки, загрузка на сервер, ссылка /s/<токен>#<ключ>;
  pull()    — копия рецензента → служебная ветка review/<id> от тега (правки записаны в исходник) и
              список правок по блокам: было / у меня сейчас / у рецензента, со слиянием;
  accept()/reject() — решение по блоку в текущую версию автора (трёхстороннее слияние, конфликт —
              окно разрешения); reply()/set_comment() — ответы на замечания. Решения и ответы
              шифруются и видны рецензенту по той же ссылке.
Сервер видит только шифротекст. Соответствие номеров вставок исходнику и исходники блоков лежат в
зашифрованных метаданных ссылки, которые сервер отдаёт только автору.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from lde import crypto, gitops, versions
from lde.project import Project
from lde.server_client import Server, ServerError


def _local_dir(root: Path) -> Path:
    d = root / ".lde" / "shares"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_payload(path: Path) -> dict | None:
    if not path.exists():
        return None
    suf = path.suffix.lower()
    if suf in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        mt = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(suf, f"image/{suf[1:]}")
        return {"type": mt, "data": base64.b64encode(path.read_bytes()).decode()}
    if suf == ".pdf":
        import pymupdf as fitz
        with fitz.open(path) as d:
            pix = d[0].get_pixmap(dpi=150, alpha=False)
            return {"type": "image/png", "data": base64.b64encode(pix.tobytes("png")).decode()}
    return None


# ------------------------------------------------------------------ снимок ----
def build_snapshot(p: Project, version_name: str, include_pdf: bool) -> tuple[dict, dict]:
    """Документ для рецензента (без разметки LDE) и закрытая часть для автора (номера вставок → исходник)."""
    from lde import mathins
    from lde.emit_latex import LatexEmitter
    from lde.studio import model
    chips, chip_src, blocks_pub, blocks_priv, images = {}, {}, [], {}, {}
    roles = p.explication_roles()
    stale = [k for k, s in p.calc_statuses().items() if s.state != "ok"]

    def toks(src, nodes):
        out, raw = [], model.tokens(p, src, nodes, 0)
        for t in raw:
            if t["t"] == "text":
                out.append({"t": "text", "v": t["v"]})
            else:
                cid = f"v{len(chip_src) + 1}"
                chip_src[cid] = t["src"]
                info = {"kind": t.get("kind", "x"), "text": t.get("text") or "", "title": t.get("title", "")}
                if t.get("latex") is not None and t.get("kind") in ("q", "math"):
                    lx = t["latex"]
                    if t.get("kind") == "math":
                        lx = mathins.expand(t["src"][1:-1], p, "latex", stale) if mathins.find(t["src"]) else t["src"][1:-1]
                    info["latex"] = lx
                chips[cid] = info
                out.append({"t": "chip", "id": cid})
        return out, raw

    def opaque_ml(ml: str) -> str:
        """\\htmlData{lde=q:R_ц} → \\htmlData{lde=q:@v12}: внутренние имена — только у автора."""
        def sub(m):
            cid = f"v{len(chip_src) + 1}"
            chip_src[cid] = ("{" + m.group(2) + "}") if m.group(1) == "q" else ("{=" + m.group(2) + "}")
            return f"\\htmlData{{lde={m.group(1)}:@{cid}}}"
        return re.sub(r"\\htmlData\{lde=(q|v):([^{}]*)\}", sub, ml)

    em = LatexEmitter(p, mark=False)
    uid_map: dict = {}
    gen_priv: dict = {}
    pre, bib = _generated(p, gen_priv)
    blocks_pub += pre
    first_app = next((i for i, b in enumerate(p.blocks) if b.kind == "heading" and "appendix" in b.classes), None)
    for n, b in enumerate(p.blocks):
        if n == first_app:
            blocks_pub += bib
            bib = []
        if b.kind in ("comment", "pagebreak", "conflict"):
            continue
        bid = f"b{n + 1}"
        uid_map[b.uid] = bid
        pub = {"id": bid, "kind": b.kind, "where": p.title_of(b), "number": p.numbers.get(id(b), ""), "editable": False}
        span = None
        if b.kind in ("heading", "para", "figure", "table"):
            tk, raw = toks(b.src, b.inl)
            pub["tokens"] = tk
            pub["editable"] = True
            span = list(model.block_text_span(b))
            blocks_priv[bid] = {"file": b.file, "uid": b.uid, "line": b.line, "src": b.src, "span": span, "toks": raw}
        if b.kind == "heading":
            pub["level"] = b.level
            from lde.profiles import get_profile
            prof_ = get_profile(p.doc_cfg.get("profile", "gost-7.32"))
            if b.level == 1 and (b.unnumbered or "appendix" in b.classes):
                pub["structural"] = True
                pub["upper"] = bool(prof_.structural_upper) and "appendix" not in b.classes
        if b.kind == "para":
            pub["role"] = roles.get(id(b), "")
        if b.kind == "formula":
            pub["latex"] = mathins.expand(b.math, p, "latex", stale)
            pub["ml"] = opaque_ml(mathins.expand(b.math, p, "ml", stale) if mathins.find(b.math) else b.math)
            pub["numbered"] = bool(b.label) and not b.unnumbered
            pub["editable"] = True
            blocks_priv[bid] = {"file": b.file, "uid": b.uid, "line": b.line, "src": b.src, "formula": True}
        if b.kind == "figure":
            paths = [b.path] if b.path else [sf[1] for sf in b.subfigs]
            if paths:
                img = _image_payload(p.root / paths[0])
                if img:
                    images[f"i{n + 1}"] = img
                    pub["img"] = f"i{n + 1}"
        if b.kind == "table":
            data, err = em.table_cells(b)
            if data:
                header, rows, _ = data
                pub["header"] = [toks("", c[2])[0] for c in header] if header else None
                pub["rows"] = [[toks("", c[2])[0] for c in r] for r in rows]
        if b.kind == "list":
            pub["items"] = [{"level": it[0], "mark": it[1], "tokens": toks(b.src, it[4])[0]} for it in b.items]
        if b.kind == "code":
            pub["text"] = b.text
        blocks_pub.append(pub)
    blocks_pub += bib
    catalog = _catalog(p, chip_src)
    snap = {"v": 1, "catalog": catalog, "title": p.title, "doc": p.doc_key, "version_name": version_name, "created": time.time(),
            "blocks": blocks_pub, "chips": chips, "images": images, "pdf": None}
    pdf = p.root / "out" / f"{p.name}.pdf"
    if include_pdf and pdf.exists():
        snap["pdf"] = base64.b64encode(pdf.read_bytes()).decode()
    snap["pages"], snap["marks"] = _layout(p, pdf, uid_map, snap["blocks"])
    return snap, {"chips": chip_src, "blocks": blocks_priv, "gen": gen_priv}


def _layout(p: Project, pdf: Path, uid_map: dict, blocks: list[dict]) -> tuple[list, list]:
    """Страницы документа (как у автора в Studio) и карта: где на странице какой элемент снимка."""
    import json as _json
    import pymupdf as fitz
    mp = p.build_dir / "map.json"
    if not pdf.exists() or not mp.exists():
        return [], []
    pages = []
    with fitz.open(pdf) as d:
        for pg in d:
            pix = pg.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pages.append({"w": pg.rect.width, "h": pg.rect.height,
                          "img": base64.b64encode(pix.tobytes("png")).decode()})
    ids = {b["id"] for b in blocks}
    bib_ids = {b["where"].split("(")[-1].rstrip(")"): b["id"] for b in blocks if b["kind"] == "bib"}
    group = {"gen:title": "@title", "gen:toc": "toc", "gen:abbr": "@abbr", "gen:symbols": "@abbr", "gen:bib": "@bib"}
    marks = []
    for m in _json.loads(mp.read_text(encoding="utf-8")).get("marks", []):
        uid = m.get("uid", "")
        mid = uid_map.get(uid) or group.get(uid) or (bib_ids.get(uid[8:]) if uid.startswith("gen:bib:") else None)
        if mid and (mid in ids or mid.startswith("@")):
            marks.append({"id": mid, "page": m["page"], "y": m["y"], "kind": m.get("kind", ""),
                          "float": m.get("float", False), "part": m.get("part", "")})
    return pages, marks


def _catalog(p: Project, chip_src: dict) -> list[dict]:
    """Что рецензент может вставить (+ Величина / + Число / + Ссылка / + Цитата): готовый вид и
    непрозрачный номер; что за ним стоит — знает только автор."""
    from lde import bib as bibmod
    from lde import calcs as calcmod
    from lde.fmt import format_value
    out = []

    def add(kind, real, **kw):
        cid = f"v{len(chip_src) + 1}"
        chip_src[cid] = real
        out.append({"kind": kind, "id": cid, **kw})
    for k, x in p.quantities.items():
        add("q", "{" + k + "}", latex=x.tex, text=k, title=", ".join(t for t in (x.meaning or "смысл не указан", x.unit) if t))
    for ck, c in p.calcs.items():
        for path, v in calcmod.all_value_paths(c):
            meta = calcmod.lookup_value(c, path) or {}
            try:
                t = format_value(v, meta.get("unit", ""), meta.get("digits"))["text"]
            except Exception:
                t = str(v)
            add("val", "{=" + f"{ck}.{path}" + "}", text=t, title=meta.get("title") or "число из расчёта")
    for lab, t in p.labels.items():
        num = f"({t.number})" if t.kind == "eq" else t.number
        what = {"fig": "Рисунок", "tab": "Таблица", "eq": "Формула", "sec": "Раздел", "app": "Приложение"}[t.kind]
        add("ref", "@" + lab, text=num, title=f"{what} {num}")
    for k, e in p.bib.items():
        add("cite", f"[@{k}]", text=f"[{p.cite_num.get(k, '?')}]", title=bibmod.format_entry(e)[:120])
    return out


# ------------------------------------- ввод рецензента → исходник автора ----
_FORBIDDEN_TEX = {"input", "include", "includeonly", "write", "openout", "openin", "read", "immediate", "closeout",
                  "def", "edef", "gdef", "xdef", "let", "futurelet", "catcode", "csname", "endcsname", "expandafter",
                  "usepackage", "RequirePackage", "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
                  "special", "directlua", "luaexec", "includegraphics", "href", "url", "jobname", "scantokens",
                  "lowercase", "uppercase", "afterassignment", "everypar", "everymath", "write18", "ShellEscape",
                  "loop", "verbatiminput", "lstinputlisting", "IfFileExists", "InputIfFileExists", "openin",
                  "pdfshellescape", "documentclass", "begin", "end", "makeatletter", "makeatother", "relax", "global",
                  "long", "outer", "chardef", "mathchardef", "countdef", "toks", "pdfliteral", "XeTeXglyph",
                  "setmathfont", "setmainfont", "fontspec", "newenvironment", "renewenvironment"}
_ALLOWED_ENVS = {"matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "cases", "aligned", "gathered", "array", "split"}


def safe_tex(tex: str) -> bool:
    """Чужой LaTeX формулы: без команд, которые читают/пишут файлы, переопределяют макросы и т. п."""
    if "@" in re.sub(r"lde=(q|v):@v\d+", "", tex) or "%" in tex:
        return False
    for m in re.finditer(r"\\([A-Za-z]+)", tex):
        name = m.group(1)
        if name in ("begin", "end"):
            env = re.match(r"\s*\{([A-Za-z*]+)\}", tex[m.end():])
            if not env or env.group(1).rstrip("*") not in _ALLOWED_ENVS:
                return False
            continue
        if name in _FORBIDDEN_TEX:
            return False
    return True


def from_reviewer_tex(tex: str, chip_src: dict) -> str | None:
    """Формула рецензента (MathLive) → исходник автора: номера вставок → \\q{…}/\\val{…}; None — опасно."""
    from lde import mathins
    if not safe_tex(tex):
        return None
    bad = []

    def sub(m):
        real = chip_src.get(m.group(2))
        if not real:
            bad.append(m.group(2))
            return ""
        key = real[2:-1] if real.startswith("{=") else real[1:-1]
        return f"\\htmlData{{lde={m.group(1)}:{key}}}"
    out = re.sub(r"\\htmlData\{lde=(q|v):@(v\d+)\}", sub, tex)
    if bad:
        return None
    return mathins.from_ml(out) if "\\htmlData{lde=" in out else out


def from_reviewer_chip(src: str, chip_src: dict) -> str | None:
    """Вставка из копии рецензента → исходник автора; None — подделка или опасное содержимое."""
    m = re.fullmatch(r"\{@(v\d+)\}|\{=@(v\d+)\}|@@(v\d+)|\$@(v\d+)\$", src or "")
    if m:
        return chip_src.get(next(g for g in m.groups() if g))
    m = re.fullmatch(r"\[@@(v\d+)(,[^\]\[{}$\\]*)?\]", src or "")
    if m:
        real = chip_src.get(m.group(1))
        if not real or not real.startswith("[@"):
            return None
        return real[:-1] + (m.group(2) or "") + "]"
    m = re.fullmatch(r"\$(.+)\$", src or "", re.S)
    if m:
        t = from_reviewer_tex(m.group(1), chip_src)
        return None if t is None else "$" + t + "$"
    m = re.fullmatch(r"(\*\*?)([^*{}$\\\n]+)\1", src or "")
    if m:
        return src
    return None


def _txt(v: str) -> list[dict]:
    return [{"t": "text", "v": v}]


def _generated(p: Project, priv: dict) -> tuple[list, list]:
    """Сгенерированные части документа — как в PDF: титул, содержание, перечни (до текста) и список
    литературы (перед приложениями). Правки рецензента в них ведут в реквизиты титула (lde.toml),
    abbreviations.toml и refs.bib; на всё можно оставить замечание."""
    from lde import bib as bibmod
    from lde.profiles import get_profile
    from lde.textfmt import plain_text
    prof = get_profile(p.doc_cfg.get("profile", "gost-7.32"))
    cfg = p.doc_cfg
    title = p.config.get("title", {}) or {}
    pre, bib = [], []
    if cfg.get("title_page", True):
        custom = bool(title.get("lines"))
        for i, ln in enumerate(prof.titlepage_lines(p)):
            gid = f"tl{i}"
            pub = {"id": gid, "kind": "title_line", "text": ln["text"], "align": ln["align"], "bold": ln["bold"],
                   "space": ln.get("space", 0), "where": "титульный лист", "editable": custom}
            priv[gid] = {"gen": "title_line", "index": i, "src": ln["text"], "where": "титульный лист"}
            if custom:
                pub["tokens"] = _txt(ln["text"])
            pre.append(pub)
        if not custom:
            for k, label in prof.fields:
                v = str(title.get(k, "") or "")
                if not v:
                    continue
                gid = f"tf_{k}"
                pre.append({"id": gid, "kind": "title_field", "label": label, "tokens": _txt(v), "editable": True,
                            "where": f"титул: {label}"})
                priv[gid] = {"gen": "title_field", "field": k, "src": v, "where": f"титул: {label}"}
    if cfg.get("toc", True):
        items = []
        for b in p.blocks:
            if b.kind == "heading" and not (b.unnumbered and b.level > 1):
                num = p.numbers.get(id(b), "")
                text = plain_text(b.inl).strip()
                if "appendix" in b.classes and b.level == 1:
                    text = f"Приложение {num} {text}"
                    num = ""
                elif b.unnumbered and prof.structural_upper:
                    text = text.upper()
                items.append({"number": num, "text": text, "level": b.level})
        if cfg.get("bibliography", True) and p.cite_order:
            items.append({"number": "", "text": prof.heading("bibliography"), "level": 1})
        pre.append({"id": "toc", "kind": "toc", "heading": prof.heading("toc"), "items": items, "where": "содержание"})
        priv["toc"] = {"gen": "toc", "where": "содержание"}
    abbr = cfg.get("abbreviations", True) and p.abbreviations
    if abbr:
        pre.append({"id": "h_abbr", "kind": "gen_heading", "text": prof.heading("abbreviations"), "where": "перечень сокращений"})
        items = list(p.abbreviations.items())
        if prof.abbr_sort:
            items.sort(key=lambda kv: kv[0].lower())
        for k, v in items:
            gid = f"ab_{len(priv)}"
            pre.append({"id": gid, "kind": "abbr", "term": k, "tokens": _txt(v), "editable": True, "where": f"сокращение «{k}»"})
            priv[gid] = {"gen": "abbr", "key": k, "src": v, "where": f"сокращение «{k}»"}
    if cfg.get("symbols", False) and p.quantities:
        if not abbr:
            pre.append({"id": "h_sym", "kind": "gen_heading", "text": prof.heading("symbols"), "where": "перечень обозначений"})
        for q in p.quantities.values():
            gid = f"sy_{len(priv)}"
            pre.append({"id": gid, "kind": "symbol", "latex": q.tex, "text": q.meaning + (f", {q.unit}" if q.unit else ""),
                        "where": f"обозначение {q.key}"})
            priv[gid] = {"gen": "symbol", "key": q.key, "where": f"обозначение {q.key}"}
    if cfg.get("bibliography", True) and p.cite_order:
        bib.append({"id": "h_bib", "kind": "gen_heading", "text": prof.heading("bibliography"), "where": "список источников"})
        for i, key in enumerate(p.cite_order, 1):
            e = p.bib.get(key)
            text = bibmod.format_entry(e) if e else f"[нет записи «{key}» в refs.bib]"
            gid = f"bi_{i}"
            where = f"источник [{i}] ({key})"
            bib.append({"id": gid, "kind": "bib", "number": f"{i}{'.' if prof.bib_number_dot else ''}", "tokens": _txt(text),
                        "editable": bool(e), "where": where})
            priv[gid] = {"gen": "bib", "key": key, "src": text, "where": where}
    return pre, bib


def _gen_current(p: Project, g: dict) -> str | None:
    """Текущее значение сгенерированной строки у автора (для слияния с правкой рецензента)."""
    from lde import bib as bibmod
    from lde.profiles import get_profile
    if g["gen"] == "title_field":
        v = (p.config.get("title", {}) or {}).get(g["field"])
        return None if v is None else str(v)
    if g["gen"] == "title_line":
        lines = (p.config.get("title", {}) or {}).get("lines") or []
        return str(lines[g["index"]].get("text", "")) if g["index"] < len(lines) else None
    if g["gen"] == "abbr":
        return p.abbreviations.get(g["key"])
    if g["gen"] == "bib":
        e = p.bib.get(g["key"])
        return bibmod.format_entry(e) if e else None
    return None


def _gen_apply(root: Path, p: Project, g: dict, text: str, message: str):
    """Записать принятую правку сгенерированной части в её источник (одной точкой сохранения)."""
    import tomlkit
    from lde import bib as bibmod
    from lde.studio import model
    gitops.checkpoint_external(root)
    if g["gen"] in ("title_field", "title_line"):
        rel = p.config_file
        doc = tomlkit.parse((root / rel).read_text(encoding="utf-8")) if (root / rel).exists() else tomlkit.document()
        if "title" not in doc:
            doc["title"] = tomlkit.table()
        if g["gen"] == "title_field":
            doc["title"][g["field"]] = text
        else:
            doc["title"]["lines"][g["index"]]["text"] = text
        model.write_file(root, rel, tomlkit.dumps(doc))
    elif g["gen"] == "abbr":
        rel = "abbreviations.toml"
        doc = tomlkit.parse((root / rel).read_text(encoding="utf-8")) if (root / rel).exists() else tomlkit.document()
        doc[g["key"]] = text
        model.write_file(root, rel, tomlkit.dumps(doc))
    elif g["gen"] == "bib":
        rel = "refs.bib"
        e = p.bib[g["key"]]
        fields = dict(e.fields)
        fields["formatted"] = text
        bt = (root / rel).read_text(encoding="utf-8")
        model.write_file(root, rel, bibmod.replace_entry(bt, g["key"], e.type, fields))
    else:
        raise ValueError("эту часть документа правкой не изменить — только замечание")
    gitops.commit(root, message, author=gitops.STUDIO_AUTHOR, paths=[rel])


# ------------------------------------------------------------- создание ----
def create(root: Path, doc: str | None = None, label: str = "", perms: str = "edit", days: float = 30,
           pin: str = "", include_pdf: bool = True, server: Server | None = None, build=True) -> dict:
    root = Path(root).resolve()
    srv = server or Server()
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    p = Project(root, doc)
    label = label.strip() or "Рецензент"
    if build:
        from lde.build import build_pdf
        r = build_pdf(p)
        if not r.get("ok"):
            raise ValueError("документ не собирается — исправьте ошибки сборки и повторите: "
                             + "; ".join(e.get("message", "") for e in r.get("errors", [])[:3]))
    vname = f"Ссылка: {label}, {time.strftime('%d.%m.%Y %H:%M')}"
    ver = versions.create(root, vname, kind="share")
    snap, priv = build_snapshot(p, vname, include_pdf)
    key, token = crypto.new_key(), secrets.token_urlsafe(32)
    meta = {"label": label, "doc": p.doc_key, "doc_title": p.title, "version_name": vname, "tag": ver["tag"],
            "commit": ver["commit"], "token": token, **priv}
    d = srv.json("POST", "/api/shares", {
        "token_hash": crypto.b64e(crypto.token_hash(token)), "perms": perms, "days": days, "pin": pin or None,
        "meta_enc": crypto.b64e(crypto.seal(key, json.dumps(meta, ensure_ascii=False).encode(), "share-meta")),
        "key_wrap": crypto.b64e(crypto.wrap_key(srv.pub, key, "share"))})
    sid = d["id"]
    srv.call("PUT", f"/api/shares/{sid}/snapshot",
             raw=crypto.seal(key, json.dumps(snap, ensure_ascii=False).encode(), "share-snapshot"))
    link = f"{d.get('base') or srv.url}/s/{token}#{crypto.b64url(key)}"
    rec = {"id": sid, "server": srv.url, "key": crypto.b64url(key), "link": link, "perms": perms, "pin": bool(pin),
           **{k: meta[k] for k in ("label", "doc", "doc_title", "version_name", "tag", "commit")}}
    (_local_dir(root) / f"{sid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


# --------------------------------------------------------------- список ----
def _meta(srv: Server, row: dict) -> tuple[bytes, dict]:
    key = crypto.unwrap_key(srv.priv, crypto.b64d(row["key_wrap"]), "share")
    return key, json.loads(crypto.open_(key, crypto.b64d(row["meta_enc"]), "share-meta"))


def list_shares(root: Path | None = None, server: Server | None = None, doc: str | None = None) -> list[dict]:
    srv = server or Server()
    out = []
    for row in srv.json("GET", "/api/shares"):
        try:
            key, meta = _meta(srv, row)
        except Exception:
            continue
        if root is not None:
            # только ссылки этого проекта: тег должен быть в его истории
            if not gitops.git(Path(root), "rev-parse", "-q", "--verify", f"refs/tags/{meta.get('tag', '')}", check=False).strip():
                continue
        if doc and meta.get("doc") != doc:
            continue
        out.append({"id": row["id"], "label": meta.get("label"), "doc": meta.get("doc"), "doc_title": meta.get("doc_title"),
                    "version_name": meta.get("version_name"), "perms": row["perms"], "created": row["created"],
                    "expires": row["expires"], "revoked": row["revoked"], "pin": row["pin"], "opens": row["opens"],
                    "opened": row["opened"], "copy_ver": row["copy_ver"], "copy_updated": row["copy_updated"],
                    "link": f"{srv.url}/s/{meta['token']}#{crypto.b64url(key)}"})
    return out


def _owner_doc(srv: Server, sid: str, key: bytes) -> dict:
    try:
        _, data, _ = srv.call("GET", f"/api/shares/{sid}/owner")
        return json.loads(crypto.open_(key, data, "share-owner"))
    except ServerError as e:
        if e.code == 404:
            return {"decisions": {}, "replies": {}, "status": {}}
        raise


def _put_owner(srv: Server, sid: str, key: bytes, doc: dict):
    doc["updated"] = time.time()
    srv.call("PUT", f"/api/shares/{sid}/owner", raw=crypto.seal(key, json.dumps(doc, ensure_ascii=False).encode(), "share-owner"))


def _ctx(root: Path, sid: str, server: Server | None):
    srv = server or Server()
    row = srv.json("GET", f"/api/shares/{sid}")
    key, meta = _meta(srv, row)
    return srv, key, meta


# ------------------------------------------------------- забрать правки ----
def _theirs_src(meta: dict, bid: str, tokens) -> str | None:
    from lde.studio.model import detokenize
    info = meta["blocks"][bid]
    if info.get("formula"):
        tex = tokens.get("latex") if isinstance(tokens, dict) else None
        if not isinstance(tex, str):
            return None
        t = from_reviewer_tex(tex.strip(), meta["chips"])
        if t is None:
            return None
        m = re.match(r"(?s)(.*?\$\$)(.*?)(\$\$.*)$", info["src"])
        return m.group(1) + " " + t + " " + m.group(3) if m else None
    if isinstance(tokens, dict):
        return None
    new = []
    for t in tokens:
        if t.get("t") == "chip":
            src = meta["chips"].get(t.get("id")) if t.get("id") else from_reviewer_chip(t.get("src", ""), meta["chips"])
            if src:                                   # неизвестный или опасный — вставку не выдумываем
                new.append({"t": "chip", "src": src})
        else:
            # недоверенный ввод: без переводов строк и управляющих знаков (иначе — новые блоки)
            v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(t.get("v", "")))
            new.append({"t": "text", "v": re.sub(r"\s*\n\s*", " ", v)})
    a, z = info["span"]
    body = detokenize(new, info["toks"])
    return info["src"][:a] + body + info["src"][z:]


def _safe(base_src: str, new_src: str) -> bool:
    """Правка рецензента не должна менять вид блока или превращать его в несколько блоков."""
    from lde.textfmt import parse_text
    a, b = parse_text(base_src), parse_text(new_src)
    return len(b) == len(a) == 1 and a[0].kind == b[0].kind and a[0].label == b[0].label and not b[0].errors


def _review_branch(root: Path, sid: str, meta: dict, theirs: dict[str, str], name: str) -> str | None:
    """Служебная ветка review/<id> от тега снимка: правки рецензента записаны прямо в исходник."""
    from lde.textfmt import parse_text, replace_block
    if not theirs:
        return None
    td = tempfile.mkdtemp(prefix="lde-review-")
    wt = Path(td) / "wt"
    try:
        gitops.git(root, "worktree", "add", "-q", "--detach", str(wt), meta["tag"])
        by_file: dict[str, list] = {}
        for bid, src in theirs.items():
            by_file.setdefault(meta["blocks"][bid]["file"], []).append((meta["blocks"][bid], src))
        for f, lst in by_file.items():
            text = (wt / f).read_text(encoding="utf-8")
            blocks = parse_text(text, f)
            # снизу вверх, чтобы номера строк выше не сдвигались
            for info, src in sorted(lst, key=lambda x: -x[0]["line"]):
                b = next((x for x in blocks if x.line == info["line"] and x.src == info["src"]), None)
                if b is None:
                    continue
                text = replace_block(text, b, src)
            (wt / f).write_text(text, encoding="utf-8")
        gitops.git(wt, "add", "-A")
        gitops.git(wt, "commit", "-q", "-m", f"Правки рецензента: {name}", author=(name or "Рецензент", "reviewer@lde.local"))
        h = gitops.git(wt, "rev-parse", "HEAD").strip()
        gitops.git(root, "update-ref", f"refs/heads/review/{sid}", h)
        return h
    finally:
        gitops.git(root, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(td, ignore_errors=True)


def _find_current(p: Project, info: dict):
    """Блок в текущей версии автора: по метке/uid, иначе по тексту в момент выдачи ссылки."""
    b = p.block_by_uid(info["uid"])
    if b is not None and (b.src == info["src"] or not info["uid"].count("#")):
        return b
    same = [x for x in p.blocks if x.src == info["src"]]
    if len(same) == 1:
        return same[0]
    return b if b is not None and b.file == info["file"] else None


def pull(root: Path, sid: str, server: Server | None = None, doc: str | None = None) -> dict:
    from lde.merge import merge_block
    from lde.studio.model import word_diff
    root = Path(root).resolve()
    srv, key, meta = _ctx(root, sid, server)
    try:
        _, data, hdr = srv.call("GET", f"/api/shares/{sid}/copy")
        copy = json.loads(crypto.open_(key, data, "share-copy"))
    except ServerError as e:
        if e.code != 404:
            raise
        copy = {"name": "", "edits": {}, "comments": []}
    owner = _owner_doc(srv, sid, key)
    name = copy.get("name") or "Рецензент"
    theirs = {bid: _theirs_src(meta, bid, t) for bid, t in (copy.get("edits") or {}).items() if bid in meta["blocks"]}
    unsafe = [k for k, v in theirs.items() if v is None]
    theirs = {k: v for k, v in theirs.items() if v is not None and v != meta["blocks"][k]["src"]}
    unsafe += [k for k, v in theirs.items() if not _safe(meta["blocks"][k]["src"], v)]
    for k in unsafe:
        theirs.pop(k, None)
    branch = _review_branch(root, sid, meta, theirs, name)
    p = Project(root, meta.get("doc"))
    items = []
    for bid, t in theirs.items():
        info = meta["blocks"][bid]
        cur = _find_current(p, info)
        ours = cur.src if cur is not None else None
        st = "deleted" if ours is None else "same" if ours == t else "clean" if ours == info["src"] else None
        merged = None
        if st is None:
            m = merge_block(info["src"], ours, t)
            st, merged = ("merge", m["text"]) if m["ok"] else ("conflict", None)
        snip = re.sub(r"\s+", " ", "".join(x.get("v", "") if x["t"] == "text" else x.get("text", "")
                                           for x in _display_items(p, info["src"]))).strip()
        items.append({"block": bid, "uid_tag": info["uid"], "render": display_diff(p, info["src"], t),
                      "snippet": snip[:60] + ("…" if len(snip) > 60 else ""),
                      "where": p.title_of(cur) if cur is not None else info["file"], "uid": cur.uid if cur is not None else None,
                      "base": info["src"], "ours": ours, "theirs": t, "merged": merged if st == "merge" else (t if st == "clean" else None),
                      "status": st, "decision": owner.get("decisions", {}).get(bid),
                      "words": word_diff(info["src"], t)})
    # сгенерированные части: реквизиты титула, сокращения, описания источников
    gens = meta.get("gen", {})
    for gid, toks in (copy.get("edits") or {}).items():
        g = gens.get(gid)
        if not g or "src" not in g:
            continue
        t = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", "".join(
            str(x.get("v", "")) for x in toks if x.get("t") == "text"))).strip()
        if not t or t == g["src"]:
            continue
        ours = _gen_current(p, g)
        st = "deleted" if ours is None else "same" if ours == t else "clean" if ours == g["src"] else None
        merged = None
        if st is None:
            m = merge_block(g["src"], ours, t)
            st, merged = ("merge", m["text"]) if m["ok"] else ("conflict", None)
        gen_uid = {"bib": f"gen:bib:{g.get('key')}", "title_field": "gen:title", "title_line": "gen:title", "abbr": "gen:abbr"}.get(g["gen"])
        items.append({"block": gid, "gen": g["gen"], "where": g["where"], "uid": gen_uid, "uid_tag": gen_uid,
                      "render": display_diff(p, g["src"], t, gen=True), "base": g["src"], "ours": ours,
                      "theirs": t, "merged": merged if st == "merge" else (t if st == "clean" else None), "status": st,
                      "decision": owner.get("decisions", {}).get(gid), "words": word_diff(g["src"], t)})
    def _order(bid: str) -> float:      # порядок документа: титул, перечни, текст, источники
        m = re.match(r"([a-z]+)_?(\d*)", bid)
        pre, n = (m.group(1), int(m.group(2) or 0)) if m else ("", 0)
        return {"tl": -3000, "tf": -2000, "ab": -1000, "b": 0, "bi": 1_000_000}.get(pre, 0) + n
    items.sort(key=lambda x: _order(x["block"]))
    comments = []
    for c in copy.get("comments") or []:
        info = meta["blocks"].get(c.get("block") or "")
        cur = _find_current(p, info) if info else None
        g = gens.get(c.get("block") or "")
        comments.append({**c, "where": p.title_of(cur) if cur is not None else g["where"] if g else ("общее" if not c.get("block") else ""),
                         "uid": cur.uid if cur is not None else None, "status": owner.get("status", {}).get(c["id"]),
                         "replies": owner.get("replies", {}).get(c["id"], [])})
    return {"id": sid, "name": name, "label": meta.get("label"), "version_name": meta.get("version_name"),
            "branch": f"review/{sid}" if branch else None, "items": items, "comments": comments,
            "updated": copy.get("updated"), "rejected_unsafe": unsafe}


# ------------------------------------------------- режим разбора правок ----
def _display_items(p: Project, src: str) -> list[dict]:
    """Блок → слова и вставки в готовом виде (для отрисованной разницы, без разметки LDE)."""
    from lde.studio import model
    from lde.textfmt import parse_text
    bl = [b for b in parse_text(src) if b.kind != "comment"]
    if not bl:
        return []
    b = bl[0]
    toks = model.tokens(p, b.src, b.inl, 0) if b.inl else [{"t": "text", "v": b.text or src}]
    out = []
    for t in toks:
        if t["t"] == "text":
            out += [{"t": "text", "v": w} for w in re.findall(r"\s+|[^\s]+", t["v"])]
        else:
            out.append({"t": "chip", "kind": t.get("kind"), "text": t.get("text") or "", "latex": t.get("latex"),
                        "title": t.get("title", ""), "key": t["src"]})
    return out


def display_diff(p: Project, base: str, theirs: str, gen: bool = False) -> dict:
    """Разница «было → у рецензента» для показа: слова и вставки (формулы — отрисованными целиком)."""
    import difflib
    from lde import mathins
    from lde.textfmt import parse_text
    if not gen:
        a_bl, b_bl = parse_text(base), parse_text(theirs)
        if a_bl and a_bl[0].kind == "formula":
            stale = []
            f = lambda bl: mathins.expand(bl[0].math, p, "latex", stale) if bl and bl[0].kind == "formula" else ""
            return {"formula": {"before": f(a_bl), "after": f(b_bl)}}
    if gen:
        a = [{"t": "text", "v": w} for w in re.findall(r"\s+|[^\s]+", base or "")]
        b = [{"t": "text", "v": w} for w in re.findall(r"\s+|[^\s]+", theirs or "")]
    else:
        a, b = _display_items(p, base), _display_items(p, theirs)
    k = lambda x: x.get("key") or x["v"]
    ops = []
    sm = difflib.SequenceMatcher(a=[k(x) for x in a], b=[k(x) for x in b], autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            ops.append({"op": "=", "items": a[a1:a2]})
        else:
            if a2 > a1:
                ops.append({"op": "-", "items": a[a1:a2]})
            if b2 > b1:
                ops.append({"op": "+", "items": b[b1:b2]})
    return {"ops": ops}


def build_review(root: Path, sid: str, doc: str | None = None) -> dict:
    """Собрать документ «как у рецензента» из служебной ветки review/<id> (в отдельной рабочей копии;
    выходы расчётов — из текущего проекта). Результат — .lde/review/<id>/review.pdf + map.json."""
    from lde.build import build_pdf
    root = Path(root).resolve()
    out = root / ".lde" / "review" / sid
    out.mkdir(parents=True, exist_ok=True)
    ref = f"refs/heads/review/{sid}"
    if not gitops.git(root, "rev-parse", "-q", "--verify", ref, check=False).strip():
        return {"ok": False, "error": "у рецензента нет исправлений текста"}
    td = tempfile.mkdtemp(prefix="lde-review-build-")
    wt = Path(td) / "wt"
    try:
        gitops.git(root, "worktree", "add", "-q", "--detach", str(wt), ref)
        for d in (root / "calc").glob("*/out"):
            shutil.copytree(d, wt / d.relative_to(root), dirs_exist_ok=True)
        p = Project(wt, doc)
        r = build_pdf(p)
        if not r.get("ok"):
            return {"ok": False, "error": "; ".join(e.get("message", "") for e in r.get("errors", [])[:3])}
        shutil.copy2(wt / "out" / f"{p.name}.pdf", out / "review.pdf")
        shutil.copy2(p.build_dir / "map.json", out / "map.json")
        return {"ok": True, "pdf": str(out / "review.pdf"), "map": str(out / "map.json")}
    finally:
        gitops.git(root, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(td, ignore_errors=True)


def own_from_tokens(root: Path, template: str, tokens: list[dict], doc: str | None = None) -> str:
    """«Совместить»: автор собрал свой вариант в редакторе со вставками → исходник блока."""
    from lde.studio import model
    from lde.textfmt import parse_text
    p = Project(root, doc)
    bl = [b for b in parse_text(template) if b.kind != "comment"]
    if not bl or not bl[0].inl and bl[0].kind not in ("para", "heading"):
        return "".join(t.get("v", "") if t.get("t") == "text" else t.get("src", "") for t in tokens)
    b = bl[0]
    old = model.tokens(p, b.src, b.inl, 0)
    body = model.detokenize(tokens, old)
    a, z = model.block_text_span(b)
    return b.src[:a] + body + b.src[z:]


def finish(root: Path, sid: str, server: Server | None = None) -> dict:
    """Разбор правок завершён: решения по всем правкам приняты → версия «Правки … учтены»."""
    rv = pull(root, sid, server)
    left = [it for it in rv["items"] if not it.get("decision")]
    if left:
        raise ValueError(f"не решено правок: {len(left)}")
    v = versions.create(root, f"Правки: {rv['name']} — учтены", kind="review")
    srv, key, meta = _ctx(root, sid, server)
    owner = _owner_doc(srv, sid, key)
    owner["finished"] = time.time()
    _put_owner(srv, sid, key, owner)
    return {"ok": True, "version": v["name"]}


def decide(root: Path, sid: str, block: str, how: str, text: str | None = None, server: Server | None = None) -> dict:
    """how: accept (взять правку; при изменённом у автора блоке — слияние), reject, own (свой текст)."""
    from lde.merge import merge_block
    from lde.studio import model
    root = Path(root).resolve()
    srv, key, meta = _ctx(root, sid, server)
    owner = _owner_doc(srv, sid, key)
    res = {"ok": True}
    if how in ("accept", "own"):
        rv = pull(root, sid, srv)
        it = next((x for x in rv["items"] if x["block"] == block), None)
        if not it:
            raise ValueError("нет такой правки (обновите список)")
        if it["ours"] is None:
            raise ValueError("этого фрагмента у вас больше нет — вставьте текст вручную")
        if how == "own":
            new = text or ""
        elif it["status"] in ("clean", "same"):
            new = it["theirs"]
        elif it["status"] == "merge":
            new = it["merged"]
        else:
            return {"ok": False, "conflict": True, "base": it["base"], "ours": it["ours"], "theirs": it["theirs"]}
        if new != it["ours"] and it.get("gen"):
            p = Project(root, meta.get("doc"))
            _gen_apply(root, p, meta["gen"][block], new, f"Принято от {rv['name']}: {it['where']}")
        elif new != it["ours"]:
            p = Project(root, meta.get("doc"))
            cur = _find_current(p, meta["blocks"][block])
            model.save_block(root, cur.uid, cur.src, new, doc=meta.get("doc"),
                             message=f"Принято от {rv['name']}: {p.title_of(cur)}")
        owner.setdefault("decisions", {})[block] = "accepted" if how == "accept" else "partial"
    elif how == "reject":
        owner.setdefault("decisions", {})[block] = "rejected"
    else:
        raise ValueError("решение: accept | reject | own")
    _put_owner(srv, sid, key, owner)
    return res


def reply(root: Path, sid: str, comment: str, text: str = "", status: str | None = None, server: Server | None = None):
    srv, key, meta = _ctx(root, sid, server)
    owner = _owner_doc(srv, sid, key)
    if text:
        owner.setdefault("replies", {}).setdefault(comment, []).append({"text": text, "time": time.time()})
    if status is not None:
        if status:
            owner.setdefault("status", {})[comment] = status
        else:
            owner.setdefault("status", {}).pop(comment, None)
    _put_owner(srv, sid, key, owner)
    return {"ok": True}


def revoke(sid: str, revoked=True, server: Server | None = None):
    (server or Server()).json("POST", f"/api/shares/{sid}", {"revoked": revoked})
