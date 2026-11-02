#!/usr/bin/env python3
"""docx_dump.py — «DOCX как данные»: вывести структуру документа для агента.

Показывает абзацы (индекс, стиль, выравнивание, флаги keepNext/keepLines,
pageBreakBefore), таблицы, картинки, формулы, поля, разрывы страниц, свойства
страницы. Именно этим инструментом агент диагностирует дефекты DOCX без Word.

Использование:
    scripts/docx_dump.py out/main.docx [--json] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def flag(p_el, tag: str) -> bool:
    ppr = p_el.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn(tag)) is not None


def para_info(p, idx: int) -> dict:
    p_el = p._p
    txt = "".join(t.text or "" for t in p_el.iter(qn("w:t")))
    ppr = p_el.find(qn("w:pPr"))
    jc = None
    if ppr is not None:
        j = ppr.find(qn("w:jc"))
        jc = j.get(qn("w:val")) if j is not None else None
    imgs = p_el.findall(".//" + qn("a:blip"))
    math_para = p_el.find(qn("m:oMathPara")) is not None
    math_inline = (not math_para) and p_el.find(qn("m:oMath")) is not None
    flds = [t.text for t in p_el.iter(qn("w:instrText")) if t.text]
    breaks = [b.get(qn("w:type"), "line") for b in p_el.iter(qn("w:br"))]
    return {
        "index": idx,
        "style": p.style.name if p.style is not None else None,
        "style_id": p.style.style_id if p.style is not None else None,
        "alignment": str(p.alignment) if p.alignment is not None else jc,
        "keepNext": flag(p_el, "w:keepNext"),
        "keepLines": flag(p_el, "w:keepLines"),
        "pageBreakBefore": flag(p_el, "w:pageBreakBefore"),
        "images": len(imgs),
        "math_display": math_para,
        "math_inline": math_inline,
        "fields": flds,
        "breaks": breaks,
        "text": txt[:300] + ("..." if len(txt) > 300 else ""),
    }


def table_info(t, idx: int) -> dict:
    tbl = t._tbl
    first_tr = tbl.find(qn("w:tr"))
    trPr = first_tr.find(qn("w:trPr")) if first_tr is not None else None
    has_header = trPr is not None and trPr.find(qn("w:tblHeader")) is not None
    rows = []
    for r in t.rows[:8]:
        rows.append([c.text.strip()[:40] for c in r.cells])
    return {"index": idx, "rows": len(t.rows), "cols": len(t.columns),
            "repeat_header": has_header, "sample": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="ограничить абзацы (0=все)")
    args = ap.parse_args()
    path = Path(args.docx)
    if not path.exists():
        print(f"docx_dump: нет файла {path}", file=sys.stderr)
        return 2
    doc = Document(str(path))
    paras = doc.paragraphs
    limited = paras[: args.limit] if args.limit else paras

    sect = doc.element.body.find(qn("w:sectPr"))
    page = {}
    if sect is not None:
        pgsz = sect.find(qn("w:pgSz"))
        mar = sect.find(qn("w:pgMar"))
        if pgsz is not None:
            page["width_twips"] = pgsz.get(qn("w:w"))
            page["height_twips"] = pgsz.get(qn("w:h"))
        if mar is not None:
            page["margins_mm"] = {
                side: round(int(mar.get(qn("w:" + side), 0)) / 56.6929, 1)
                for side in ("left", "right", "top", "bottom")}
        page["footer"] = sect.find(qn("w:footerReference")) is not None
        page["titlePg"] = sect.find(qn("w:titlePg")) is not None

    dump = {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "paragraphs": [para_info(p, i) for i, p in enumerate(limited)],
        "paragraphs_total": len(paras),
        "tables": [table_info(t, i) for i, t in enumerate(doc.tables)],
        "page": page,
        "core": {
            "title": doc.core_properties.title,
            "author": doc.core_properties.author,
        },
    }

    if args.json:
        print(json.dumps(dump, ensure_ascii=False, indent=2))
        return 0

    print(f"# DOCX: {path} ({dump['size_bytes']} байт)")
    print(f"# абзацев: {dump['paragraphs_total']}, таблиц: {len(doc.tables)}")
    print(f"# страница: {page}")
    print()
    for p in dump["paragraphs"]:
        marks = []
        if p["style"]: marks.append(f"style={p['style']}")
        if p["alignment"]: marks.append(f"align={p['alignment']}")
        if p["keepNext"]: marks.append("keepNext")
        if p["keepLines"]: marks.append("keepLines")
        if p["pageBreakBefore"]: marks.append("PB")
        if p["images"]: marks.append(f"imgs={p['images']}")
        if p["math_display"]: marks.append("MATH")
        elif p["math_inline"]: marks.append("math")
        if p["fields"]: marks.append(f"field={p['fields']}")
        if p["breaks"]: marks.append(f"breaks={p['breaks']}")
        text = p["text"].replace("\n", "\\n")
        print(f"{p['index']:03d} [{', '.join(marks)}] {text}")
    print()
    for t in dump["tables"]:
        print(f"TABLE {t['index']}: {t['rows']}x{t['cols']}, "
              f"repeat_header={t['repeat_header']}")
        for row in t["sample"]:
            print("   | " + " | ".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
