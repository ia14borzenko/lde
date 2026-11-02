#!/usr/bin/env python3
"""find_toc_pages.py — найти номера страниц заголовков в отрендеренном PDF.

Нужен для статического СОДЕРЖАНИЯ в DOCX (п.6.13 ГОСТ 7.32-2017): Word-поле TOC
не обновляется в headless-LibreOffice, поэтому конвейер один раз рендерит DOCX в
PDF, находит фактические страницы заголовков и вписывает их в СОДЕРЖАНИЕ.

Использование:
    find_toc_pages.py --pdf out/.docx-pdf/pass1.pdf \
        --plan out/docx-source.plan.json --out out/docx-toc-pages.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def norm(s: str) -> str:
    """Нормализация для поиска: без пробелов/мягких переносов, нижний регистр."""
    s = s.replace("\u00ad", "").replace("\u00a0", "")
    return re.sub(r"\s+", "", s).lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        import pymupdf  # PyMuPDF >= 1.24 (fitz deprecated)
    except ImportError:
        try:
            import fitz as pymupdf  # старые версии
        except ImportError:
            print("find_toc_pages: PyMuPDF не установлен — пропускаю (номера страниц "
                  "в СОДЕРЖАНИИ будут пустыми)", file=sys.stderr)
            Path(args.out).write_text("{}", encoding="utf-8")
            return 0

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    headings = plan.get("headings", [])
    doc = pymupdf.open(args.pdf)
    pages_norm = []
    pages_raw = []
    for page in doc:
        txt = page.get_text("text")
        pages_raw.append(txt)
        pages_norm.append(norm(txt))

    # страницы, где находится само СОДЕРЖАНИЕ, из поиска исключаем
    toc_pages = set()
    toc_word = norm(plan.get("toc_heading", "СОДЕРЖАНИЕ"))
    for i, ptxt in enumerate(pages_norm):
        if i < 4 and toc_word in ptxt[:400]:
            toc_pages.add(i)

    result: dict[str, int] = {}
    missing: list[str] = []
    last_page = 0
    for h in headings:
        text = h["text"]
        target = norm(text)
        found = None
        for i in range(last_page, len(pages_norm)):
            if i in toc_pages:
                continue
            if target and target in pages_norm[i]:
                found = i
                break
        if found is None:
            # мягкий поиск: первые 25 символов нормализованного текста
            short = target[:25]
            for i in range(last_page, len(pages_norm)):
                if i in toc_pages:
                    continue
                if short and short in pages_norm[i]:
                    found = i
                    break
        if found is None:
            missing.append(text)
        else:
            last_page = found
            result[text] = found + 1
    status = {
        "pdf": str(Path(args.pdf).resolve()),
        "pages_total": len(pages_norm),
        "found": len(result),
        "missing": missing,
        "toc_pages": sorted(p + 1 for p in toc_pages),
        "map": result,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"find_toc_pages: {args.pdf}: найдено {len(result)}/{len(headings)}, "
          f"пропущено {len(missing)}, страниц в PDF: {len(pages_norm)}")
    for m in missing:
        print(f"  - не найдено: {m}")
    return 0 if not missing else 0


if __name__ == "__main__":
    sys.exit(main())
