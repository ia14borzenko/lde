#!/usr/bin/env python3
"""compare_pdf.py — сравнение LaTeX-PDF (канон) и PDF, отрендеренного из DOCX.

Классификация расхождений:
  T1 (контент/структура) — критично: пропавшие структурные элементы, заголовки,
     подписи/нумерация объектов, существенная потеря текста. exit 1.
  T3 (вёрстка) — предупреждения: число страниц, распределение текста, число
     изображений. Вёрстка Word ≠ TeX по определению (docs/decisions.md).

Порог T1: покрытие слов DOCX относительно LaTeX-PDF >= 0.90, иначе ошибка.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def load_text(path: Path):
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        return None
    doc = pymupdf.open(str(path))
    pages = []
    xrefs = set()
    for page in doc:
        pages.append(page.get_text("text"))
        try:
            for img in page.get_images(full=True):
                xrefs.add(img[0])
        except Exception:
            pass
    return {"pages": pages, "images": len(xrefs)}


def norm_tokens(text: str) -> list[str]:
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    text = re.sub(r"-\s*\n\s*", "", text)  # переносы
    return re.findall(r"[a-zа-яё0-9]+", text.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--latex-pdf", default=None)
    ap.add_argument("--docx-pdf", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).resolve()
    lp = Path(args.latex_pdf) if args.latex_pdf else proj / "out" / "main.pdf"
    dp = Path(args.docx_pdf) if args.docx_pdf else proj / "out" / "main.docx.pdf"
    out = Path(args.out) if args.out else proj / "out" / "qa-compare.json"
    errors: list[dict] = []
    warnings: list[dict] = []
    counts: dict = {}

    if not lp.exists():
        errors.append({"tier": "T1", "message": f"нет LaTeX-PDF: {lp} (сначала lde build)"})
    if not dp.exists():
        warnings.append({"tier": "T3", "message": f"нет PDF из DOCX: {dp} "
                                                  f"(lde docx рендерит автоматически)"})
    if not lp.exists() or not dp.exists():
        result = {"latex_pdf": str(lp), "docx_pdf": str(dp),
                  "errors": errors, "warnings": warnings, "counts": counts,
                  "status": "errors" if errors else "warnings"}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"compare_pdf: пропуск (нет PDF) -> {out}", file=sys.stderr)
        return 1 if errors else 0

    A = load_text(lp)
    B = load_text(dp)
    if A is None or B is None:
        result = {"latex_pdf": str(lp), "docx_pdf": str(dp),
                  "errors": errors,
                  "warnings": warnings + [{"tier": "T3",
                    "message": "PyMuPDF недоступен — сравнение PDF не выполнено"}],
                  "counts": counts, "status": "warnings",
                  "manual": ["Сравнить PDF визуально (PyMuPDF не установлен)"]}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    ta = "\n".join(A["pages"])
    tb = "\n".join(B["pages"])
    la = norm_tokens(ta)
    lb = norm_tokens(tb)
    ca, cb = Counter(la), Counter(lb)
    total = sum(ca.values()) or 1
    missed = sum((ca - cb).values())
    coverage = 1.0 - missed / total
    counts.update({
        "latex_pages": len(A["pages"]), "docx_pages": len(B["pages"]),
        "latex_words": len(la), "docx_words": len(lb),
        "word_coverage": round(coverage, 4),
        "latex_images": A["images"], "docx_images": B["images"],
    })

    # --- T1: структурные элементы и подписи ---
    def has(text: str, needle: str) -> bool:
        return " ".join(needle.lower().split()) in " ".join(text.lower().split())

    def has_compact(text: str, needle: str) -> bool:
        """Сравнение без пробелов/переносов: титул в PDF Word переносится по-другому."""
        def comp(x: str) -> str:
            x = x.replace("\u00ad", "").replace("\u00a0", " ")
            x = re.sub(r"-\s*\n\s*", "-", x)
            return re.sub(r"\s+", "", x).lower()
        return comp(needle) in comp(text)

    markers = [
        "СОДЕРЖАНИЕ", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ",
        "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", "ПРИЛОЖЕНИЕ",
    ]
    for m in markers:
        if has(ta, m) and not has(tb, m):
            errors.append({"tier": "T1",
                           "message": f"в DOCX-версии пропал структурный элемент «{m}»"})
    # заголовки разделов: сравниваем первые 5 слов каждого заголовка LaTeX-PDF
    # номер раздела — не больше двух цифр в компоненте (иначе «1592 Гц …» — это число в тексте)
    sec_re = re.compile(r"^[ \t]*(\d{1,2}(?:\.\d{1,2})*)[ \t]+([А-ЯЁA-Z][^\n]{3,90}?)[ \t]*$", re.M)
    def extract_sections(text: str) -> list[str]:
        out = []
        # список источников («1 Фамилия И. О. …») — не заголовки: вырезаем его до приложений
        low = text.lower()
        a = low.rfind("список использованных источников")
        if a >= 0:
            b = low.find("приложение", a)
            text = text[:a] + (text[b:] if b >= 0 else "")
        for m in sec_re.finditer(text):
            line = m.group(0)
            if ".." in line or ". . ." in line:  # строка оглавления, не заголовок
                continue
            if line.rstrip().endswith((".", ",", ";", ":", "-")):  # заголовки ГОСТ — без точки и переноса
                continue
            if " / " in line or "”" in line or "“" in line:  # строка списка источников
                continue
            out.append(f"{m.group(1)} {m.group(2).strip()}")
        return out
    latex_sections = extract_sections(ta)
    docx_sections = extract_sections(tb)
    def short(s):
        return " ".join(s.split()[:4]).lower()
    for h in latex_sections:
        if short(h) not in [short(x) for x in docx_sections]:
            errors.append({"tier": "T1", "message": f"в DOCX-версии пропал/изменён "
                                                    f"заголовок: «{h[:70]}»"})

    # подписи и нумерация объектов
    cap_re = re.compile(r"(Рисунок|Таблица)\s+([А-ЯA-Z]?\.?\d+)")
    for kind in ("Рисунок", "Таблица"):
        a_nums = [m.group(2) for m in cap_re.finditer(ta) if m.group(1) == kind]
        b_nums = [m.group(2) for m in cap_re.finditer(tb) if m.group(1) == kind]
        counts[f"latex_{kind.lower()}s"] = len(a_nums)
        counts[f"docx_{kind.lower()}s"] = len(b_nums)
        if a_nums != b_nums:
            errors.append({"tier": "T1",
                           "message": f"нумерация {kind.lower()}ов в PDF LaTeX {a_nums} "
                                      f"≠ PDF DOCX {b_nums}"})

    if coverage < 0.90:
        errors.append({"tier": "T1",
                       "message": f"потеря текста: покрытие слов DOCX относительно "
                                  f"LaTeX-PDF {coverage:.1%} < 90%"})
    elif coverage < 0.95:
        warnings.append({"tier": "T3",
                         "message": f"покрытие слов {coverage:.1%} (норма >=95%); "
                                    f"проверить переносы/титул"})

    # --- T3: вёрстка ---
    if len(A["pages"]) != len(B["pages"]):
        warnings.append({"tier": "T3",
                         "message": f"число страниц: LaTeX {len(A['pages'])}, "
                                    f"DOCX {len(B['pages'])} (вёрстка Word отличается)"})
    # В DOCX одно и то же изображение может быть вложено несколько раз, поэтому
    # критично только «в DOCX меньше картинок, чем в LaTeX» (потеря контента).
    if B["images"] < A["images"]:
        warnings.append({"tier": "T3",
                         "message": f"изображений меньше в DOCX: LaTeX {A['images']}, "
                                    f"DOCX {B['images']}"})

    # ключевые реквизиты титула из metadata.tex
    meta = proj / "metadata.tex"
    if meta.exists():
        mt = meta.read_text(encoding="utf-8")
        for key in ("title", "org"):
            m = re.search(key + r"\s*=\s*\{([^}]*)\}", mt)
            if m and m.group(1).strip():
                val = m.group(1).strip()
                if has_compact(ta, val) and not has_compact(tb, val):
                    errors.append({"tier": "T1",
                                   "message": f"в DOCX пропал реквизит титула «{key}»: "
                                              f"{val[:50]}"})

    status = "errors" if errors else ("warnings" if warnings else "ok")
    result = {
        "latex_pdf": str(lp), "docx_pdf": str(dp),
        "errors": errors, "warnings": warnings, "counts": counts,
        "status": status,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"compare_pdf: покрытие={coverage:.1%}, страниц LaTeX={len(A['pages'])} "
              f"DOCX={len(B['pages'])}, ошибок {len(errors)}, предупреждений "
              f"{len(warnings)} -> {out}", file=sys.stderr)
        for e in errors[:10]:
            print(f"  [T1] {e['message']}", file=sys.stderr)
        for w in warnings[:10]:
            print(f"  [T3] {w['message']}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
