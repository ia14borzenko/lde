"""Примечания рецензента из Word (§5.8).

Руководитель открыл DOCX, оставил примечания и прислал файл обратно. LDE читает примечания,
привязывает каждое к элементу документа (по тексту, к которому оно прикреплено) и хранит в
review/примечания.json. Текст документа из Word не импортируется: исходник остаётся источником истины.

    lde review import ответ-руководителя.docx    прочитать примечания
    lde review                                  список (для автора и агента): место, текст, статус
    lde review done <номер>                     отметить выполненным
"""
from __future__ import annotations

import difflib
import json
import re
import time
import zipfile
from pathlib import Path

from lxml import etree

from lde.project import Project
from lde.textfmt import plain_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(t):
    return f"{{{W}}}{t}"


def read_comments(docx: Path) -> list[dict]:
    z = zipfile.ZipFile(docx)
    try:
        cx = etree.fromstring(z.read("word/comments.xml"))
    except KeyError:
        return []
    comments = {}
    for c in cx.findall(_w("comment")):
        cid = c.get(_w("id"))
        text = "\n".join("".join(t.text or "" for t in p.iter(_w("t"))) for p in c.findall(_w("p"))).strip()
        comments[cid] = {"id": cid, "author": c.get(_w("author")) or "", "date": c.get(_w("date")) or "", "text": text}
    doc = etree.fromstring(z.read("word/document.xml"))
    # текст, к которому прикреплено примечание (от commentRangeStart до End), и абзац целиком
    open_ids, anchor = set(), {}
    for p in doc.iter(_w("p")):
        para_text = "".join(t.text or "" for t in p.iter(_w("t")))
        for el in p.iter():
            tag = etree.QName(el).localname
            if tag == "commentRangeStart":
                open_ids.add(el.get(_w("id")))
                anchor.setdefault(el.get(_w("id")), {"text": "", "para": para_text})
            elif tag == "commentRangeEnd":
                open_ids.discard(el.get(_w("id")))
            elif tag == "t":
                for cid in open_ids:
                    anchor[cid]["text"] += el.text or ""
            elif tag == "commentReference":
                anchor.setdefault(el.get(_w("id")), {"text": "", "para": para_text})
    out = []
    for cid, c in comments.items():
        a = anchor.get(cid, {"text": "", "para": ""})
        out.append({**c, "anchor": a["text"].strip(), "para": a["para"].strip()})
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def locate(proj: Project, para: str, anchor: str) -> dict:
    """Абзац DOCX → элемент исходника (лучшее совпадение по тексту)."""
    target = _norm(para or anchor)
    if not target:
        return {}
    best, score = None, 0.0
    for b in proj.blocks:
        if b.kind in ("comment", "pagebreak"):
            continue
        txt = _norm(plain_text(b.inl) if b.inl else (b.math or b.text))
        if not txt:
            continue
        if anchor and _norm(anchor) and _norm(anchor) in txt:
            r = 0.9 + 0.1 * difflib.SequenceMatcher(None, target[:300], txt[:300]).ratio()
        else:
            r = difflib.SequenceMatcher(None, target[:300], txt[:300]).ratio()
        if r > score:
            best, score = b, r
    if not best or score < 0.45:
        return {}
    return {"uid": best.uid, "file": best.file, "line": best.line, "title": proj.title_of(best), "score": round(score, 2)}


def store_path(root: Path) -> Path:
    return root / "review" / "примечания.json"


def load(root: Path) -> list[dict]:
    p = store_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save(root: Path, items: list[dict]):
    p = store_path(root)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def import_review(root: Path, docx: Path, doc=None) -> list[dict]:
    proj = Project(root, doc)
    items = load(root)
    known = {(i["source"], i["id"]) for i in items}
    n = len(items)
    for c in read_comments(docx):
        if (docx.name, c["id"]) in known:
            continue
        n += 1
        loc = locate(proj, c["para"], c["anchor"])
        items.append({"n": n, "source": docx.name, "doc": proj.doc_key, "id": c["id"], "author": c["author"], "date": c["date"],
                      "text": c["text"], "anchor": c["anchor"], **loc, "done": False, "imported": time.time()})
    save(root, items)
    return items


def set_done(root: Path, n: int, done=True):
    items = load(root)
    for i in items:
        if i["n"] == n:
            i["done"] = done
    save(root, items)
    return items
