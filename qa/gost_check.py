#!/usr/bin/env python3
"""gost_check.py — проверка параметров оформления DOCX по ГОСТ 7.32-2017.

Проверяется программно то, что можно проверить детерминированно (поля, шрифты,
интервалы, отступы, нумерация страниц, стили заголовков/подписей/таблиц).
Нюансы, которые требуют человека, попадают в "manual" — это честный чек-лист,
а не «всё хорошо».

Ссылки на пункты ГОСТ — в docs/gost-requirements.md.
Код возврата: 0 — ошибок нет; 1 — есть ошибки.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

# Ожидаемые параметры (ГОСТ 7.32-2017, п.6.1.1, 6.3.1, 6.2.1, 6.2.3, 6.5.7, 6.6.3)
MM = 56.6929  # twips в миллиметре
A4_W, A4_H = 11906, 16838
MARGIN = {"left": 30 * MM, "right": 15 * MM, "top": 20 * MM, "bottom": 20 * MM}
TOL = 1.0 * MM  # допуск 1 мм


def add(issues, code, msg, where="", severity="error"):
    issues.append({"code": code, "severity": severity, "message": msg, "where": where})


def ppr_flag(p_el, tag):
    ppr = p_el.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn(tag)) is not None


def style_by_id(doc, sid):
    for st in doc.styles:
        try:
            if st.style_id == sid:
                return st
        except Exception:
            continue
    return None


def style_el(doc, sid):
    st = style_by_id(doc, sid)
    return st.element if st is not None else None


def check_page(doc, issues):
    sec = doc.sections[0]
    pg = doc.element.body.find(qn("w:sectPr"))
    if pg is None:
        add(issues, "G-6.1.1", "в DOCX нет sectPr (параметры страницы)", "sectPr")
        return
    pgsz = pg.find(qn("w:pgSz"))
    if pgsz is None or abs(int(pgsz.get(qn("w:w"), 0)) - A4_W) > 10 \
            or abs(int(pgsz.get(qn("w:h"), 0)) - A4_H) > 10:
        add(issues, "G-6.1.1", "формат страницы не A4 (п.6.1.1)", "pgSz")
    mar = pg.find(qn("w:pgMar"))
    if mar is None:
        add(issues, "G-6.1.1", "нет полей страницы (pgMar)", "pgMar")
    else:
        for side, expect in MARGIN.items():
            val = int(mar.get(qn("w:" + side), 0))
            if abs(val - expect) > TOL:
                add(issues, "G-6.1.1",
                    f"поле {side}={val / MM:.1f} мм, ожидается {expect / MM:.0f} мм "
                    f"(п.6.1.1)", "pgMar")
    # колонтитул с номером страницы
    footer_ref = pg.find(qn("w:footerReference"))
    if footer_ref is None:
        add(issues, "G-6.3.1", "нет колонтитула с номером страницы (п.6.3.1)", "sectPr")
    # титульный лист без номера страницы (п.6.3.2)
    if pg.find(qn("w:titlePg")) is None:
        add(issues, "G-6.3.2",
            "нет titlePg: номер страницы будет печататься на титульном листе "
            "(п.6.3.2)", "sectPr", severity="warning")


def check_styles(doc, issues):
    normal = style_el(doc, "Normal")
    if normal is None:
        add(issues, "G-6.1.1", "нет стиля Normal", "styles")
    else:
        rpr = normal.find(qn("w:rPr"))
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        font = fonts.get(qn("w:ascii")) if fonts is not None else None
        if font and "Times" not in font:
            add(issues, "G-6.1.1", f"основной шрифт {font!r}, рекомендуется Times "
                                   f"New Roman (п.6.1.1)", "Normal", "warning")
        if rpr is not None:
            sz = rpr.find(qn("w:sz"))
            if sz is not None and int(sz.get(qn("w:val"))) < 24:
                add(issues, "G-6.1.1",
                    f"кегль основного текста {int(sz.get(qn('w:val'))) / 2:.0f} пт "
                    f"< 12 пт (п.6.1.1)", "Normal")
        ppr = normal.find(qn("w:pPr"))
        if ppr is not None:
            sp = ppr.find(qn("w:spacing"))
            if sp is not None:
                line = int(sp.get(qn("w:line"), 240))
                if line < 360:
                    add(issues, "G-6.1.1",
                        f"межстрочный интервал {line / 240:.2f}, требуется 1,5 "
                        f"(п.6.1.1)", "Normal", "warning")
            ind = ppr.find(qn("w:ind"))
            if ind is None or abs(int(ind.get(qn("w:firstLine"), 0)) - 709) > 30:
                add(issues, "G-6.1.1",
                    "абзацный отступ не 1,25 см (п.6.1.1)", "Normal", "warning")
    for sid, name in (("Heading1", "Heading 1"), ("Heading2", "Heading 2"),
                      ("Heading3", "Heading 3")):
        el = style_el(doc, sid)
        if el is None:
            continue
        if not ppr_flag(el, "w:keepNext"):
            add(issues, "G-6.2.3", f"стиль {name} без keepNext (п.6.2.1: заголовок "
                                   f"не отрывается от текста)", sid, "warning")
        rpr = el.find(qn("w:rPr"))
        if rpr is None or rpr.find(qn("w:b")) is None:
            add(issues, "G-6.2.3", f"стиль {name} не полужирный (п.6.2.3)", sid,
                "warning")
    sh = style_el(doc, "StructuralHeading")
    if sh is None:
        add(issues, "G-6.2.1", "нет стиля StructuralHeading для структурных "
                               "элементов", "styles", "warning")
    else:
        ppr = sh.find(qn("w:pPr"))
        jc = ppr.find(qn("w:jc")) if ppr is not None else None
        if jc is None or jc.get(qn("w:val")) != "center":
            add(issues, "G-6.2.1", "StructuralHeading не по центру (п.6.2.1)",
                "StructuralHeading", "warning")
    for sid in ("ImageCaption",):
        el = style_el(doc, sid)
        if el is not None:
            ppr = el.find(qn("w:pPr"))
            jc = ppr.find(qn("w:jc")) if ppr is not None else None
            if jc is None or jc.get(qn("w:val")) != "center":
                add(issues, "G-6.5.7", "подпись рисунка не по центру (п.6.5.7)",
                    sid, "warning")
    el = style_el(doc, "TableCaption")
    if el is not None:
        ppr = el.find(qn("w:pPr"))
        jc = ppr.find(qn("w:jc")) if ppr is not None else None
        if jc is not None and jc.get(qn("w:val")) == "center":
            add(issues, "G-6.6.3", "подпись таблицы по центру, требуется слева "
                                   "(п.6.6.3)", "TableCaption", "warning")


def check_content(doc, issues, counts, prof=None):
    prof = prof or {}
    paras = doc.paragraphs
    texts = [p.text.strip() for p in paras]
    upper = [t.upper() for t in texts]
    required = {
        "СОДЕРЖАНИЕ": "6.2.1",
        "ВВЕДЕНИЕ": "6.2.1",
        "ЗАКЛЮЧЕНИЕ": "6.2.1",
        "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ": "6.16",
    }
    if prof.get("required") is not None:   # профиль (методичка) задаёт свой состав
        required = {k: v for k, v in required.items() if k in prof["required"]}
    for name, clause in required.items():
        if name not in upper:
            add(issues, f"G-{clause}", f"нет структурного элемента «{name}» (п.{clause})")
    counts["structural_elements"] = sum(1 for n in required if n in upper)

    # Приложения: заголовки-абзацы (не путать с пунктами СОДЕРЖАНИЯ)
    app_count = 0
    for p in paras:
        try:
            sid = p.style.style_id if p.style is not None else ""
        except Exception:
            sid = ""
        if sid in ("StructuralHeading", "Heading1") and p.text.strip().upper().startswith("ПРИЛОЖЕНИЕ"):
            app_count += 1
    counts["appendices"] = app_count

    # Список источников: элементы нумерованы «1.» (п.6.16)
    try:
        idx = next(i for i, t in enumerate(upper)
                   if t == "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
        entries = 0
        for t in texts[idx + 1:]:
            if not t:
                continue
            if t.isupper() and t.startswith("ПРИЛОЖЕНИЕ"):
                break
            entries += 1
        if entries == 0:
            add(issues, "G-6.16", "список использованных источников пуст (п.6.16)")
        # нумерация «1.»/«1 » в начале
        import re as _re
        numbered = sum(1 for t in texts[idx + 1: idx + 1 + entries * 2]
                       if _re.match(r"^\d+\.", t))
        if not prof.get("bib_dot", True):
            numbered = sum(1 for t in texts[idx + 1: idx + 1 + entries * 2] if _re.match(r"^\d+\.?\s", t))
        if entries and numbered < max(1, entries // 2):
            add(issues, "G-6.16",
                f"элементы списка источников не нумерованы арабскими цифрами с "
                f"точкой (п.6.16); распознано {numbered} из {entries}",
                "bibliography", "warning")
        counts["bibliography_entries"] = entries
    except StopIteration:
        pass

    # Подписи рисунков/таблиц: формат «Рисунок N — », «Таблица N — »
    import re as _re
    def sn(p):
        try:
            return p.style.name if p.style is not None else ""
        except Exception:
            return ""
    fig_bad = sum(1 for p in paras if sn(p) in ("Image Caption", "ImageCaption")
                  and not _re.match(r"^Рисунок\s+[А-ЯA-Z]?\.?\d+\s+—", p.text.strip()))
    tab_bad = sum(1 for p in paras if sn(p) in ("Table Caption", "TableCaption")
                  and not _re.match(r"^Таблица\s+[А-ЯA-Z]?\.?\d+\s+—", p.text.strip()))
    if fig_bad:
        add(issues, "G-6.5.7", f"{fig_bad} подписей рисунков не по формату "
                               f"«Рисунок N — Наименование» (п.6.5.7)")
    if tab_bad:
        add(issues, "G-6.6.3", f"{tab_bad} подписей таблиц не по формату "
                               f"«Таблица N — Наименование» (п.6.6.3)")
    counts["figure_captions"] = sum(1 for p in paras
                                    if sn(p) in ("Image Caption", "ImageCaption"))
    counts["table_captions"] = sum(1 for p in paras
                                   if sn(p) in ("Table Caption", "TableCaption"))

    # Таблицы: линии (п.6.6.6)
    try:
        with zipfile.ZipFile(doc.part.package._pkg_file) if False else zipfile.ZipFile(
                doc._docx_path if hasattr(doc, "_docx_path") else "") as _z:
            pass
    except Exception:
        pass
    no_borders = 0
    for tbl in doc.tables:
        tblPr = tbl._tbl.find(qn("w:tblPr"))
        borders = tblPr.find(qn("w:tblBorders")) if tblPr is not None else None
        if borders is None:
            no_borders += 1
    if no_borders:
        add(issues, "G-6.6.6", f"{no_borders} таблиц без линий (п.6.6.6)")
    counts["tables_no_borders"] = no_borders


MANUAL_CHECKLIST = [
    ("G-6.1.1", "Шрифт формул соответствует основному тексту или выделен осознанно"),
    ("G-6.1.4", "Имена собственные/организации приведены на языке оригинала"),
    ("G-6.2.4", "В заголовках нет переносов слов (п.6.2.4) — проверить визуально"),
    ("G-6.5.1", "Иллюстрации расположены после первого упоминания (п.6.5.1)"),
    ("G-6.8.2", "Пояснения символов формул начинаются со слова «где» (п.6.8.2)"),
    ("G-6.10", "Титульный лист: грифы согласования/утверждения при необходимости (п.6.10)"),
    ("G-6.17.3", "Заголовки приложений с прописной буквы полужирным по центру (п.6.17.3)"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--docx", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--profile-json", default=None, help='{"required": [...], "bib_dot": true} — состав по профилю')
    args = ap.parse_args()
    prof = json.loads(args.profile_json) if args.profile_json else {}

    proj = Path(args.project).resolve()
    docx = Path(args.docx) if args.docx else proj / "out" / "main.docx"
    out = Path(args.out) if args.out else proj / "out" / "qa-gost.json"
    issues: list[dict] = []
    counts: dict = {}

    if not docx.exists():
        result = {"file": str(docx), "issues": [
            {"code": "MISSING", "severity": "error",
             "message": "DOCX не найден — запустите lde docx", "where": ""}],
            "manual": [], "counts": {}}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    doc = Document(str(docx))
    doc._docx_path = str(docx)  # для возможных проверок по zip
    check_page(doc, issues)
    check_styles(doc, issues)
    check_content(doc, issues, counts, prof)

    errors = [i for i in issues if i["severity"] == "error"]
    result = {
        "file": str(docx),
        "issues": issues,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "manual": [{"code": c, "note": n} for c, n in MANUAL_CHECKLIST],
        "counts": counts,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"gost_check: {docx.name}: ошибок {result['errors']}, "
              f"предупреждений {result['warnings']}, ручных пунктов "
              f"{len(result['manual'])} -> {out}", file=sys.stderr)
        for it in issues[:20]:
            mark = "ОШИБКА" if it["severity"] == "error" else "warning"
            print(f"  [{mark}] {it['code']}: {it['message']}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
