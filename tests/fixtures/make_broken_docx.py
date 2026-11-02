#!/usr/bin/env python3
"""make_broken_docx.py — генератор «испорченных» DOCX для тестов ремонта.

Берёт чистый DOCX и воспроизводимо ломает его так, как это делает конвертация
(без коммита бинарников: файлы создаются на лету в тестовом каталоге).

Использование:
    make_broken_docx.py --input out/main.docx --outdir /tmp/fixtures
Выводит JSON: {"files": {"broken-D1.docx": ["D1"], ...}, "clean": "..."}
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(qn("w:t")))


def style_id(p_el) -> str:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        st = ppr.find(qn("w:pStyle"))
        if st is not None:
            return st.get(qn("w:val"))
    return ""


def remove_flag(p_el, tag: str) -> int:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        return 0
    n = 0
    for e in ppr.findall(qn(tag)):
        ppr.remove(e)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    src = Path(args.input)
    outdir = Path(args.outdir)
    if not src.exists():
        print(f"make_broken_docx: нет {src}", file=sys.stderr)
        return 2
    outdir.mkdir(parents=True, exist_ok=True)
    result: dict = {"files": {}, "clean": str(src)}

    # ---- D1: убрать keepNext/keepLines у рисунков и подписей ----
    d = Document(str(src))
    broken = 0
    for p in d.paragraphs:
        sn = style_id(p._p)
        if sn in ("CaptionedFigure", "Figure") or p._p.find(".//" + qn("a:blip")) is not None:
            broken += remove_flag(p._p, "w:keepNext") + remove_flag(p._p, "w:keepLines")
        if sn in ("ImageCaption", "TableCaption"):
            broken += remove_flag(p._p, "w:keepLines") + remove_flag(p._p, "w:keepNext")
    p1 = outdir / "broken-D1.docx"
    d.save(str(p1))
    if broken:
        result["files"][p1.name] = ["D1"]

    # ---- D3: убрать повтор шапки и границы таблиц ----
    d = Document(str(src))
    n = 0
    for tbl in d.tables:
        tr0 = tbl.rows[0]._tr
        trPr = tr0.find(qn("w:trPr"))
        if trPr is not None:
            for e in trPr.findall(qn("w:tblHeader")):
                trPr.remove(e)
                n += 1
        tblPr = tbl._tbl.find(qn("w:tblPr"))
        if tblPr is not None:
            for e in tblPr.findall(qn("w:tblBorders")):
                tblPr.remove(e)
                n += 1
    p3 = outdir / "broken-D3.docx"
    d.save(str(p3))
    if n:
        result["files"][p3.name] = ["D3"]

    # ---- D4: испортить СОДЕРЖАНИЕ (неверные страницы + лишний пункт) ----
    d = Document(str(src))
    toc_idx = next((i for i, p in enumerate(d.paragraphs)
                    if para_text(p._p).strip().upper() == "СОДЕРЖАНИЕ"), None)
    if toc_idx is not None:
        entries = []
        for p in d.paragraphs[toc_idx + 1:]:
            sn = style_id(p._p)
            if sn.startswith("Heading") or sn == "StructuralHeading":
                break
            if para_text(p._p).strip():
                entries.append(p._p)
        if entries:
            # первый пункт: подменить текст на устаревший
            for r in entries[0].findall(qn("w:r")):
                entries[0].remove(r)
            from docx.oxml import OxmlElement
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = "УСТАРЕВШИЙ ПУНКТ"
            r.append(t)
            entries[0].append(r)
        p4 = outdir / "broken-D4.docx"
        d.save(str(p4))
        result["files"][p4.name] = ["D4"]

    # ---- D5: сломать нумерацию рисунков ----
    d = Document(str(src))
    n = 0
    for p in d.paragraphs:
        t = para_text(p._p).strip()
        if t.startswith("Рисунок 1 —"):
            for node in p._p.iter(qn("w:t")):
                if node.text and node.text.startswith("Рисунок 1"):
                    node.text = node.text.replace("Рисунок 1", "Рисунок 7", 1)
                    n += 1
                    break
            if n:
                break
    p5 = outdir / "broken-D5.docx"
    d.save(str(p5))
    if n:
        result["files"][p5.name] = ["D5"]

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
