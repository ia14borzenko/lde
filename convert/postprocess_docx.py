#!/usr/bin/env python3
"""postprocess_docx.py — детерминированная постобработка DOCX после pandoc.

Делает то, что pandoc не умеет для ГОСТ-документа:
  1) титульный лист (центрирование/жирность/разрывы по плану);
  2) СОДЕРЖАНИЕ: статические строки с отточием и номерами страниц (п.6.13);
  3) структурные заголовки — по центру, ПРОПИСНЫМИ, с новой страницы (п.6.2.1);
  4) подписи рисунков/таблиц — keepNext/keepLines (дефекты D1/D2);
  5) таблицы — повтор шапки (tblHeader), запрет разрыва строк, линии (D3, п.6.6.6);
  6) формулы — нумерация справа в круглых скобках (п.6.8.3, дефект D6/D5);
  7) перенос СПИСКА ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ перед приложениями (п.6.16);
  8) PNG-fallback для SVG (D7) — выполняется в latex2docx.sh отдельным шагом,
     если включён режим --math=svg (см. embeds.py).

Использование:
    postprocess_docx.py --docx out/main.raw.docx --plan out/docx-source.plan.json \
        --out out/main.docx [--toc-pages out/toc-pages.json] [--report out/main.docx.report.json]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, Twips

try:
    from embeds import add_svg_png_fallbacks
except Exception:  # запуск не из каталога convert/
    add_svg_png_fallbacks = None

FONT = "Times New Roman"
TEXT_WIDTH_TWIPS = 9354  # A4 (210мм) - 30мм - 15мм = 165мм ≈ 9354 twips
ALIGN = {"center": "center", "right": "right", "left": "left", "both": "both"}


BLOCK_TW = "240"
BLOCK_TW_AFTER = "240"


def el(tag: str, **attrs) -> OxmlElement:
    e = OxmlElement(tag)
    for k, v in attrs.items():
        if k.startswith("w_") or k.startswith("m_"):
            e.set(qn(k.replace("_", ":", 1)), str(v))
        else:
            e.set(k, str(v))
    return e


def make_run(text: str, *, bold=False, size_pt: int | None = None,
             font: str = FONT) -> OxmlElement:
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rfonts = OxmlElement("w:rFonts")
    for a in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(a), font)
    rpr.append(rfonts)
    if bold:
        rpr.append(OxmlElement("w:b"))
    if size_pt:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size_pt * 2)))
        rpr.append(sz)
        szcs = OxmlElement("w:szCs")
        szcs.set(qn("w:val"), str(int(size_pt * 2)))
        rpr.append(szcs)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    return r


def make_para(*, style: str | None = None, runs: list[OxmlElement] | None = None,
              text: str | None = None, jc: str | None = None,
              left_cm: float | None = None, first_line_cm: float | None = None,
              line: float | None = None, before_pt: float | None = None,
              after_pt: float | None = None, keep_next=False, keep_lines=False,
              page_break=False, tabs: list[dict] | None = None,
              bold=False, size_pt: int | None = None) -> OxmlElement:
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    if style:
        ppr.append(el("w:pStyle", w_val=style))
    if tabs:
        tabs_el = OxmlElement("w:tabs")
        for t in tabs:
            tab = OxmlElement("w:tab")
            tab.set(qn("w:val"), t.get("val", "left"))
            tab.set(qn("w:pos"), str(t["pos"]))
            if t.get("leader"):
                tab.set(qn("w:leader"), t["leader"])
            tabs_el.append(tab)
        ppr.append(tabs_el)
    if jc:
        ppr.append(el("w:jc", w_val=jc))
    if left_cm is not None or first_line_cm is not None:
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(int(Cm(left_cm or 0).twips)))
        ind.set(qn("w:firstLine"), str(int(Cm(first_line_cm or 0).twips)))
        ppr.append(ind)
    if line is not None or before_pt is not None or after_pt is not None:
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:before"), str(int((before_pt or 0) * 20)))
        sp.set(qn("w:after"), str(int((after_pt or 0) * 20)))
        sp.set(qn("w:line"), str(int((line or 1.5) * 240)))
        sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
    for tag, flag in (("w:keepNext", keep_next), ("w:keepLines", keep_lines),
                      ("w:pageBreakBefore", page_break)):
        if flag:
            ppr.append(OxmlElement(tag))
    p.append(ppr)
    run_list = runs if runs is not None else ([make_run(text, bold=bold, size_pt=size_pt)]
                                              if text is not None else [])
    for r in run_list:
        p.append(r)
    return p


def para_text(p) -> str:
    el_ = p._p if hasattr(p, "_p") else p
    return "".join(t.text or "" for t in el_.iter(qn("w:t")))


def style_name(p) -> str:
    ps = p.find(qn("w:pPr"))
    if ps is not None:
        st = ps.find(qn("w:pStyle"))
        if st is not None:
            return st.get(qn("w:val"))
    return ""


def set_para_style(p_el, style_id: str) -> None:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p_el.insert(0, ppr)
    for old in ppr.findall(qn("w:pStyle")):
        ppr.remove(old)
    st = OxmlElement("w:pStyle")
    st.set(qn("w:val"), style_id)
    ppr.insert(0, st)


def set_ppr_attr(p_el, tag: str, **attrs) -> None:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p_el.insert(0, ppr)
    for old in ppr.findall(qn(tag)):
        ppr.remove(old)
    e = OxmlElement(tag)
    for k, v in attrs.items():
        e.set(qn(k), v)
    ppr.append(e)


def clear_indent(p_el) -> None:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p_el.insert(0, ppr)
    for old in ppr.findall(qn("w:ind")):
        ppr.remove(old)


# ------------------------------------------------------------- title page ---
def format_titlepage(paras, plan, report) -> None:
    specs = plan.get("titlepage", [])
    if not specs:
        return
    for i, spec in enumerate(specs):
        if i >= len(paras):
            report["warnings"].append("титул: в DOCX меньше абзацев, чем в плане")
            break
        p = paras[i]
        if para_text(p).strip() != spec["text"].strip():
            report["warnings"].append(
                f"титул: несовпадение текста абзаца {i}: {para_text(p)[:40]!r} != {spec['text'][:40]!r}")
        ppr = p._p.get_or_add_pPr()
        for old in ppr.findall(qn("w:jc")):
            ppr.remove(old)
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), ALIGN.get(spec["align"], "center"))
        ppr.append(jc)
        clear_indent(p._p)
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), "0")
        ind.set(qn("w:firstLine"), "0")
        ppr.append(ind)
        for tag in ("w:spacing", "w:keepNext", "w:keepLines"):
            for old in ppr.findall(qn(tag)):
                ppr.remove(old)
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:before"), str(int(spec.get("space_before_pt", 0) * 20)))
        sp.set(qn("w:after"), "0")
        sp.set(qn("w:line"), "240")
        sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
        for r in p._p.findall(qn("w:r")):
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                r.insert(0, rpr)
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = OxmlElement("w:rFonts")
                rpr.insert(0, rfonts)
            for a in ("w:ascii", "w:hAnsi", "w:cs"):
                rfonts.set(qn(a), FONT)
            for old in rpr.findall(qn("w:b")):
                rpr.remove(old)
            if spec.get("bold"):
                rpr.append(OxmlElement("w:b"))
            sz = str(int(spec.get("size_pt", 14) * 2))
            for tag in ("w:sz", "w:szCs"):
                for old in rpr.findall(qn(tag)):
                    rpr.remove(old)
                s = OxmlElement(tag)
                s.set(qn("w:val"), sz)
                rpr.append(s)
    report["counts"]["titlepage_lines"] = len(specs)


# ------------------------------------------------------------------- TOC ----
def build_toc_elements(plan, toc_pages: dict[str, int] | None,
                       report) -> list[OxmlElement]:
    headings = plan.get("headings", [])
    if not headings:
        return []
    elements: list[OxmlElement] = []
    # заголовок СОДЕРЖАНИЕ
    elements.append(make_para(style="StructuralHeading", text=plan.get("toc_heading", "СОДЕРЖАНИЕ"),
                              bold=True, size_pt=14, keep_next=True))
    missing = 0
    for h in headings:
        level = h.get("level", 1)
        text = h["text"]
        page = None
        if toc_pages:
            page = toc_pages.get(text)
            if page is None:
                page = toc_pages.get(_norm(text))
            if page is None:
                for k, v in toc_pages.items():
                    if _norm(k) == _norm(text):
                        page = v
                        break
            if page is None:
                missing += 1
        left = {1: 0.0, 2: 0.75, 3: 1.5}.get(level, 0.0)
        runs = [make_run(text)]
        runs.append(make_run("\t"))
        runs.append(make_run(str(page) if page is not None else ""))
        elements.append(make_para(
            runs=runs, jc="left", left_cm=left, first_line_cm=0,
            line=1.5, before_pt=0, after_pt=0, keep_lines=True,
            tabs=[{"val": "right", "pos": TEXT_WIDTH_TWIPS, "leader": "dot"}]))
    if toc_pages and missing:
        report["warnings"].append(f"СОДЕРЖАНИЕ: не определены страницы для {missing} пунктов")
    report["counts"]["toc_entries"] = len(headings)
    return elements


def _norm(s: str) -> str:
    return " ".join(s.replace("\u00a0", " ").split()).lower()


# -------------------------------------------------------------- headings ----
def fix_headings(doc, plan, report) -> None:
    # вставляемые постобработкой заголовки (СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ)
    # не участвуют в сверке «заголовок DOCX <-> план»: их ещё нет в документе
    expected = [h for h in plan.get("headings", []) if not h.get("inserted")]
    idx = 0
    appendix_titles = {a["heading"]: a["title"] for a in plan.get("appendix_titles", [])
                       if a.get("heading")}
    last_appendix_heading = None
    for p in doc.paragraphs:
        if style_name(p._p) in ("Heading1", "Heading2", "Heading3", "StructuralHeading"):
            text = para_text(p).strip()
            if idx >= len(expected):
                report["warnings"].append(f"заголовок вне плана: {text!r}")
                continue
            exp = expected[idx]
            if _norm(text) != _norm(exp["text"]):
                report["warnings"].append(
                    f"заголовок не совпал с планом: DOCX={text!r} plan={exp['text']!r}")
            if exp.get("structural"):
                set_para_style(p._p, "StructuralHeading")
                if not text.isupper() and plan.get("structural_upper", True):
                    # привести текст к ПРОПИСНЫМ (п.6.2.1)
                    for t in p._p.iter(qn("w:t")):
                        if t.text:
                            t.text = t.text.upper()
                set_ppr_attr(p._p, "w:jc", **{"w:val": "center"})
                clear_indent(p._p)
                set_ppr_attr(p._p, "w:keepNext")
                set_ppr_attr(p._p, "w:pageBreakBefore")
                if exp.get("appendix"):
                    last_appendix_heading = exp["text"]
            else:
                # ensure keepNext для заголовка (D2)
                set_ppr_attr(p._p, "w:keepNext")
            idx += 1
    if last_appendix_heading:
        # первый абзац после заголовка приложения — название приложения
        title = appendix_titles.get(last_appendix_heading)
        if title:
            for p in doc.paragraphs:
                if _norm(para_text(p)) == _norm(title) and style_name(p._p) not in (
                        "Heading1", "StructuralHeading"):
                    set_para_style(p._p, "AppendixTitle")
                    set_ppr_attr(p._p, "w:jc", **{"w:val": "center"})
                    break
    report["counts"]["headings"] = idx


# ---------------------------------------------------------------- figures ---
def fix_figures(doc, report) -> None:
    paras = doc.paragraphs
    count = 0
    for i, p in enumerate(paras):
        sn = style_name(p._p)
        if sn == "CaptionedFigure" or (p._p.find(".//" + qn("a:blip")) is not None):
            set_ppr_attr(p._p, "w:keepNext")   # рисунок держится с подписью (D1)
            set_ppr_attr(p._p, "w:keepLines")
            set_ppr_attr(p._p, "w:jc", **{"w:val": "center"})
            clear_indent(p._p)
            count += 1
            if i + 1 < len(paras) and style_name(paras[i + 1]._p) == "ImageCaption":
                cap = paras[i + 1]._p
                set_ppr_attr(cap, "w:jc", **{"w:val": "center"})
                clear_indent(cap)
                set_ppr_attr(cap, "w:keepLines")
    report["counts"]["figures"] = count


# ---------------------------------------------------------------- tables ----
def _add_tbl_borders(tbl) -> None:
    tblPr = tbl.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr")
        tbl.insert(0, tblPr)
    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), "000000")
        borders.append(e)
    tblPr.append(borders)
    # ширина на всю полосу набора
    for old in tblPr.findall(qn("w:tblW")):
        tblPr.remove(old)
    w = OxmlElement("w:tblW")
    w.set(qn("w:w"), "5000")
    w.set(qn("w:type"), "pct")
    tblPr.append(w)
    for old in tblPr.findall(qn("w:jc")):
        tblPr.remove(old)
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "left")
    tblPr.append(jc)


def fix_tables(doc, report) -> None:
    for tbl in doc.tables:
        _add_tbl_borders(tbl._tbl)
        rows = tbl.rows
        if not rows:
            continue
        # шапка повторяется на каждой странице (D3, п.6.6.3)
        first_tr = rows[0]._tr
        trPr = first_tr.find(qn("w:trPr"))
        if trPr is None:
            trPr = OxmlElement("w:trPr")
            first_tr.insert(0, trPr)
        if trPr.find(qn("w:tblHeader")) is None:
            trPr.append(OxmlElement("w:tblHeader"))
        # строки не разрываются между страницами
        for row in rows:
            trPr = row._tr.find(qn("w:trPr"))
            if trPr is None:
                trPr = OxmlElement("w:trPr")
                row._tr.insert(0, trPr)
            if trPr.find(qn("w:cantSplit")) is None:
                trPr.append(OxmlElement("w:cantSplit"))
        # формат ячеек: без абзацного отступа, одинарный интервал, 12 pt
        for ri, row in enumerate(rows):
            for cell in row.cells:
                for p in cell.paragraphs:
                    clear_indent(p._p)
                    set_ppr_attr(p._p, "w:spacing", **{"w:line": "240", "w:lineRule": "auto"})
                    if ri == 0:
                        set_ppr_attr(p._p, "w:jc", **{"w:val": "center"})
                    else:
                        set_ppr_attr(p._p, "w:jc", **{"w:val": "left"})
                    for r in p._p.findall(qn("w:r")):
                        rpr = r.find(qn("w:rPr"))
                        if rpr is None:
                            rpr = OxmlElement("w:rPr")
                            r.insert(0, rpr)
                        for tag in ("w:sz", "w:szCs"):
                            for old in rpr.findall(qn(tag)):
                                rpr.remove(old)
                            s = OxmlElement(tag)
                            s.set(qn("w:val"), "24")  # 12 pt — допустимо меньше текста (п.6.6.7)
                            rpr.append(s)
        # подпись таблицы — над таблицей, keepNext (D1-аналог)
        prev = tbl._tbl.getprevious()
        while prev is not None:
            if prev.tag == qn("w:p"):
                if style_name(prev) == "TableCaption" or para_text(prev).strip():
                    if style_name(prev) == "TableCaption":
                        set_ppr_attr(prev, "w:keepNext")
                        set_ppr_attr(prev, "w:keepLines")
                        set_ppr_attr(prev, "w:jc", **{"w:val": "left"})
                        clear_indent(prev)
                    break
            prev = prev.getprevious()
    report["counts"]["tables"] = len(doc.tables)


# -------------------------------------------------------------- equations ---
def fix_equations(doc, plan, report) -> None:
    seq = plan.get("display_math", [])
    paras = [p for p in doc.paragraphs if p._p.find(qn("m:oMathPara")) is not None]
    report["counts"]["equations_total"] = len(paras)
    report["counts"]["equations_numbered"] = sum(1 for e in seq if e.get("numbered"))
    if len(paras) != len(seq):
        report["warnings"].append(
            f"формулы: в DOCX {len(paras)} display-math, в плане {len(seq)} — нумерация может съехать")
    for p, meta in zip(paras, seq):
        p_el = p._p
        omml_para = p_el.find(qn("m:oMathPara"))
        if omml_para is None:
            continue
        math_children = [c for c in omml_para if c.tag == qn("m:oMath")]
        if not meta.get("numbered"):
            # без номера: по центру, с отбивками (п.6.8.1)
            jc = omml_para.find(qn("m:oMathParaPr"))
            if jc is not None:
                j = jc.find(qn("m:jc"))
                if j is not None:
                    j.set(qn("m:val"), "center")
            set_ppr_attr(p_el, "w:spacing", **{"w:before": BLOCK_TW, "w:after": BLOCK_TW_AFTER,
                                               "w:line": "240", "w:lineRule": "auto"})
            clear_indent(p_el)
            continue
        number = meta.get("number") or ""
        # новая нумерованная строка: таб-стопы center/right + формула + (N)
        new_p = OxmlElement("w:p")
        ppr = OxmlElement("w:pPr")
        tabs = OxmlElement("w:tabs")
        for val, pos in (("center", TEXT_WIDTH_TWIPS // 2), ("right", TEXT_WIDTH_TWIPS)):
            tab = OxmlElement("w:tab")
            tab.set(qn("w:val"), val)
            tab.set(qn("w:pos"), str(pos))
            tabs.append(tab)
        ppr.append(tabs)
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), "0")
        ind.set(qn("w:firstLine"), "0")
        ppr.append(ind)
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:before"), BLOCK_TW)
        sp.set(qn("w:after"), BLOCK_TW_AFTER)
        sp.set(qn("w:line"), "240")
        sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
        new_p.append(ppr)
        run_tab1 = OxmlElement("w:r")
        run_tab1.append(OxmlElement("w:tab"))
        new_p.append(run_tab1)
        for mc in math_children:
            new_p.append(copy.deepcopy(mc))
        run_tab2 = OxmlElement("w:r")
        run_tab2.append(OxmlElement("w:tab"))
        new_p.append(run_tab2)
        new_p.append(make_run(f"({number})"))
        p_el.addprevious(new_p)
        p_el.getparent().remove(p_el)


# ------------------------------------------------- svg-формулы (--math=svg) --
def fix_svg_equations(doc, plan, report) -> None:
    """Нумерация display-формул, отрендеренных в SVG (math2svg.lua).

    pandoc переименовывает картинки в word/media/rIdNN.svg, поэтому номер
    формулы восстанавливается по порядку абзацев-картинок: рисунки (стиль
    CaptionedFigure/Figure) пропускаются, остальные картинки идут в том же
    порядке, что display-формулы в плане.
    """
    seq = plan.get("display_math", [])
    if not seq:
        return
    eq_paras = []
    for p in doc.paragraphs:
        if p._p.find(".//" + qn("a:blip")) is None:
            continue
        if style_name(p._p) in ("CaptionedFigure", "Figure"):
            continue
        eq_paras.append(p)
    if len(eq_paras) != len(seq):
        report["warnings"].append(
            f"SVG-формулы: найдено {len(eq_paras)} картинок-формул, в плане {len(seq)}")
    found = 0
    for p, meta in zip(eq_paras, seq):
        p_el = p._p
        if not meta.get("numbered"):
            set_ppr_attr(p_el, "w:jc", **{"w:val": "center"})
            clear_indent(p_el)
            set_para_style(p_el, "EquationImage")
            found += 1
            continue
        number = meta.get("number") or ""
        new_p = OxmlElement("w:p")
        ppr = OxmlElement("w:pPr")
        tabs = OxmlElement("w:tabs")
        for val, pos in (("center", TEXT_WIDTH_TWIPS // 2), ("right", TEXT_WIDTH_TWIPS)):
            tab = OxmlElement("w:tab")
            tab.set(qn("w:val"), val)
            tab.set(qn("w:pos"), str(pos))
            tabs.append(tab)
        ppr.append(tabs)
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), "0")
        ind.set(qn("w:firstLine"), "0")
        ppr.append(ind)
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:before"), BLOCK_TW)
        sp.set(qn("w:after"), BLOCK_TW_AFTER)
        sp.set(qn("w:line"), "240")
        sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
        new_p.append(ppr)
        set_para_style(new_p, "EquationImage")
        tab_run = OxmlElement("w:r")
        tab_run.append(OxmlElement("w:tab"))
        new_p.append(tab_run)
        for child in list(p_el):
            if child.tag == qn("w:pPr"):
                continue
            new_p.append(copy.deepcopy(child))
        tab_run2 = OxmlElement("w:r")
        tab_run2.append(OxmlElement("w:tab"))
        new_p.append(tab_run2)
        new_p.append(make_run(f"({number})"))
        p_el.addprevious(new_p)
        p_el.getparent().remove(p_el)
        found += 1
    report["counts"]["equations_from_svg"] = found


# ----------------------------------------------------------- bibliography ---# ----------------------------------------------------------- bibliography ---
def relocate_bibliography(doc, plan, report) -> None:
    if plan.get("bibliography_inline"):
        # LDE v2: список источников уже стоит на своём месте (формирует сам LDE, не citeproc)
        report["counts"]["bibliography_entries"] = plan.get("bibliography_count", 0)
        return
    paras = doc.paragraphs
    bib = [p._p for p in paras if style_name(p._p) == "Bibliography"]
    anchor = None
    for p in paras:
        t = para_text(p).strip()
        if t.upper().startswith("ПРИЛОЖЕНИЕ"):
            anchor = p._p
            break
    heading = make_para(style="StructuralHeading",
                        text=plan.get("bibliography_heading",
                                      "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ"),
                        bold=True, size_pt=14, keep_next=True)
    if not bib:
        report["warnings"].append(
            "список использованных источников пуст (нет \\RdeCite в тексте?)")
    if anchor is not None:
        anchor.addprevious(heading)
        for b in bib:
            anchor.addprevious(b)
    else:
        last = paras[-1]._p if paras else None
        if last is not None:
            last.addnext(heading)
            cursor = heading
            for b in bib:
                cursor.addnext(b)
                cursor = b
    report["counts"]["bibliography_entries"] = len(bib)


# ------------------------------------------------------------ svg fallback --
def svg_fallback(docx_path: Path, report) -> None:
    """Добавить PNG-fallback к SVG-картинкам (D7, MS-ODRAWXML).

    Реализация минимальная: если SVG вставлен как image part без fallback,
    конвертируем SVG -> PNG через ImageMagick и заменяем blip на PNG,
    сохраняя SVG в asvg:svgBlip (Word 2016+ покажет SVG).
    """
    try:
        import zipfile
        import re as _re
        import subprocess
        import tempfile
    except Exception:  # pragma: no cover
        return
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        svgs = [n for n in names if n.startswith("word/media/") and n.lower().endswith(".svg")]
        if not svgs:
            return
    report["warnings"].append(
        f"SVG в DOCX: {len(svgs)} шт.; PNG-fallback выполняет qa/fix_docx.py "
        "(D7) — постобработка не дублирует эту операцию")
    report["counts"]["svg_images"] = len(svgs)


# ------------------------------------------------------------- переносы ----
# Порядок дочерних элементов w:settings (ECMA-376, CT_Settings) — до и после autoHyphenation.
_SETTINGS_BEFORE = ("writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
                    "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
                    "printFractionalCharacterWidth", "printFormsData", "embedTrueTypeFonts", "embedSystemFonts",
                    "saveSubsetFonts", "saveFormsData", "mirrorMargins", "alignBordersAndEdges",
                    "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter", "gutterAtTop", "hideSpellingErrors",
                    "hideGrammaticalErrors", "activeWritingStyle", "proofState", "formsDesign", "attachedTemplate",
                    "linkStyles", "stylePaneFormatFilter", "stylePaneSortMethod", "documentType", "mailMerge",
                    "revisionView", "trackRevisions", "doNotTrackMoves", "doNotTrackFormatting", "documentProtection",
                    "autoFormatOverride", "styleLockTheme", "styleLockQFSet", "defaultTabStop")


def set_hyphenation(doc, on: bool, report) -> None:
    """Настройка документа hyphenation: false — автоперенос в Word/LibreOffice выключен явно
    (w:autoHyphenation w:val="false"); true — как было (без элемента: решает программа)."""
    if on:
        return
    st = doc.settings.element
    for e in st.findall(qn("w:autoHyphenation")):
        st.remove(e)
    el = OxmlElement("w:autoHyphenation")
    el.set(qn("w:val"), "false")
    before = {qn("w:" + n) for n in _SETTINGS_BEFORE}
    last = None
    for ch in st:
        if ch.tag in before:
            last = ch
    if last is not None:
        last.addnext(el)
    else:
        st.insert(0, el)
    report["steps"].append("переносы слов выключены (autoHyphenation=false)")


# ------------------------------------------------------------------ main ----
def _gde_width_twips() -> int:
    """Ширина «где » в Times New Roman 14 pt (для выравнивания обозначений в пояснениях)."""
    try:
        import subprocess
        from PIL import ImageFont
        path = subprocess.run(["fc-match", "-f", "%{file}", "Times New Roman"], capture_output=True, text=True).stdout
        f = ImageFont.truetype(path, 1400)
        return int(round(f.getlength("где ") / 100 * 20))
    except Exception:
        return 460


def fix_explications(doc, report) -> None:
    """Пояснения к формуле: «где» без отступа, обозначения следующих строк — под обозначением первой."""
    w = str(_gde_width_twips())
    n = 0
    for p in doc.paragraphs:
        ts = list(p._p.iter(qn("w:t")))
        if not ts or not ts[0].text or ts[0].text[0] not in "\u2060\u2063":
            continue
        role = "gde" if ts[0].text[0] == "\u2060" else "cont"
        ts[0].text = ts[0].text[1:]
        ppr = p._p.get_or_add_pPr()
        for old in ppr.findall(qn("w:ind")):
            ppr.remove(old)
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), "0")
        ind.set(qn("w:firstLine"), "0")
        ppr.append(ind)
        tabs = OxmlElement("w:tabs")
        tab = OxmlElement("w:tab")
        tab.set(qn("w:val"), "left")
        tab.set(qn("w:pos"), w)
        tabs.append(tab)
        ppr.insert(0, tabs) if ppr.find(qn("w:pStyle")) is None else ppr.find(qn("w:pStyle")).addnext(tabs)
        if role == "gde":
            t0 = ts[0]
            m = re.match(r"^\s*где\s+", t0.text or "")
            if m:
                rest = t0.text[m.end():]
                t0.text = "где"
                run = t0.getparent()
                tab_el = OxmlElement("w:tab")
                t0.addnext(tab_el)
                if rest:
                    t2 = OxmlElement("w:t")
                    t2.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    t2.text = rest
                    tab_el.addnext(t2)
        else:
            r = OxmlElement("w:r")
            r.append(OxmlElement("w:tab"))
            first = p._p.find(qn("w:r"))
            if first is None:
                first = next(iter(p._p.iterchildren(qn("m:oMath"))), None)
            if first is not None:
                first.addprevious(r)
            else:
                p._p.append(r)
            if ts[0].text is not None:
                ts[0].text = ts[0].text.lstrip()
        n += 1
    report["counts"]["explications"] = n


def _merge_ppr(p_el, tag: str, **attrs) -> None:
    """Дописать атрибуты в элемент свойств абзаца (не теряя прочих)."""
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        p_el.insert(0, ppr)
    e = ppr.find(qn(tag))
    if e is None:
        e = OxmlElement(tag)
        ppr.append(e)
    for k, v in attrs.items():
        if v is None:
            e.attrib.pop(qn(k), None)
        else:
            e.set(qn(k), v)


def _style_spacing(doc, p_el, attr: str) -> int:
    """Интервал перед/после абзаца, twips: явный или из стиля абзаца (по цепочке basedOn)."""
    ppr = p_el.find(qn("w:pPr"))
    sp = ppr.find(qn("w:spacing")) if ppr is not None else None
    if sp is not None and sp.get(qn(attr)) is not None:
        return int(sp.get(qn(attr)))
    sid = style_name(p_el) or "Normal"
    styles = doc.styles.element
    seen = set()
    while sid and sid not in seen:
        seen.add(sid)
        st = next((x for x in styles.findall(qn("w:style")) if x.get(qn("w:styleId")) == sid), None)
        if st is None:
            break
        sppr = st.find(qn("w:pPr"))
        ssp = sppr.find(qn("w:spacing")) if sppr is not None else None
        if ssp is not None and ssp.get(qn(attr)) is not None:
            return int(ssp.get(qn(attr)))
        b = st.find(qn("w:basedOn"))
        sid = b.get(qn("w:val")) if b is not None else None
    return 0


def _set_run_props(r_el, st: dict) -> None:
    rpr = r_el.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        r_el.insert(0, rpr)
    def put(tag, val):
        for old in rpr.findall(qn(tag)):
            rpr.remove(old)
        e = OxmlElement(tag)
        if val is not None:
            e.set(qn("w:val"), val)
        rpr.append(e)
    if "bold" in st:
        put("w:b", None if st["bold"] else "0")
        put("w:bCs", None if st["bold"] else "0")
    if "italic" in st:
        put("w:i", None if st["italic"] else "0")
        put("w:iCs", None if st["italic"] else "0")
    if "size" in st:
        put("w:sz", str(int(round(st["size"] * 2))))
        put("w:szCs", str(int(round(st["size"] * 2))))


def apply_manual_styles(doc, plan, report) -> None:
    """Ручное оформление блоков ({style …}): параметры — абзацам между метками LDEFMTBEGINn/ENDn."""
    styles = plan.get("manual_styles") or []
    body = doc.element.body
    n_done = 0
    begin_re = re.compile(r"^LDEFMTBEGIN(\d+)$")
    for el_ in list(body):
        if el_.tag != qn("w:p"):
            continue
        m = begin_re.match(para_text(el_).strip())
        if not m:
            continue
        idx = int(m.group(1))
        span, cur = [], el_.getnext()
        while cur is not None and not (cur.tag == qn("w:p") and para_text(cur).strip() == f"LDEFMTEND{idx}"):
            span.append(cur)
            cur = cur.getnext()
        end = cur
        body.remove(el_)
        if end is not None:
            body.remove(end)
        else:
            report["warnings"].append(f"ручное оформление {idx}: нет конечной метки")
        if idx >= len(styles):
            continue
        st = styles[idx]
        paras = []
        for e in span:
            if e.tag == qn("w:p"):
                paras.append(e)
            elif e.tag == qn("w:tbl"):
                paras += list(e.iter(qn("w:p")))
        top = [e for e in span if e.tag == qn("w:p")]
        if not paras:
            continue
        first, last = (top[0] if top else paras[0]), (top[-1] if top else paras[-1])
        for p in (top if st.get("kind") != "table" else paras):
            if "align" in st and st.get("kind") != "table":
                _merge_ppr(p, "w:jc", **{"w:val": {"justify": "both"}.get(st["align"], st["align"])})
            ind = {}
            if "indent" in st:
                tw = int(round(st["indent"] * 567))
                ind.update({"w:firstLine": str(tw), "w:hanging": None} if tw >= 0 else {"w:hanging": str(-tw), "w:firstLine": None})
            elif st.get("align") in ("center", "right"):
                ind.update({"w:firstLine": "0", "w:hanging": None})
            if "left" in st:
                ind["w:left"] = str(int(round(st["left"] * 567)))
            if "right" in st:
                ind["w:right"] = str(int(round(st["right"] * 567)))
            if ind:
                _merge_ppr(p, "w:ind", **ind)
            if "line" in st:
                _merge_ppr(p, "w:spacing", **{"w:line": str(int(round(st["line"] * 240))), "w:lineRule": "auto"})
            if st.get("keep"):
                set_ppr_attr(p, "w:keepNext")
        if st.get("before"):
            v = max(0, _style_spacing(doc, first, "w:before") + int(round(st["before"] * 20)))
            _merge_ppr(first, "w:spacing", **{"w:before": str(v)})
        if st.get("after"):
            v = max(0, _style_spacing(doc, last, "w:after") + int(round(st["after"] * 20)))
            _merge_ppr(last, "w:spacing", **{"w:after": str(v)})
        if st.get("newpage"):
            set_ppr_attr(first, "w:pageBreakBefore")
        if any(k in st for k in ("size", "bold", "italic")):
            for p in paras:
                for r in p.iter(qn("w:r")):
                    _set_run_props(r, st)
                if "size" in st and p.find(".//" + qn("m:oMath")) is not None:
                    # кегль формулы: LibreOffice берёт его из знака абзаца, Word — из прогонов m:r
                    ppr = p.find(qn("w:pPr"))
                    if ppr is None:
                        ppr = OxmlElement("w:pPr")
                        p.insert(0, ppr)
                    prpr = ppr.find(qn("w:rPr"))
                    if prpr is None:
                        prpr = OxmlElement("w:rPr")
                        ppr.append(prpr)
                    for t in ("w:sz", "w:szCs"):
                        for old in prpr.findall(qn(t)):
                            prpr.remove(old)
                        e = OxmlElement(t)
                        e.set(qn("w:val"), str(int(round(st["size"] * 2))))
                        prpr.append(e)
                if "size" in st:
                    for r in p.iter(qn("m:r")):
                        rpr = r.find(qn("w:rPr"))
                        if rpr is None:
                            rpr = OxmlElement("w:rPr")
                            mrpr = r.find(qn("m:rPr"))
                            (mrpr.addnext(rpr) if mrpr is not None else r.insert(0, rpr))
                        for t in ("w:sz", "w:szCs"):
                            for old in rpr.findall(qn(t)):
                                rpr.remove(old)
                            e = OxmlElement(t)
                            e.set(qn("w:val"), str(int(round(st["size"] * 2))))
                            rpr.append(e)
        n_done += 1
    report["counts"]["manual_styles"] = n_done
    if n_done:
        report["steps"].append(f"ручное оформление: {n_done} элементов")


def gost_bullets(doc, report) -> None:
    """Маркированные перечисления — тире (ГОСТ 7.32 п. 6.4.6), а не «•» шаблона pandoc."""
    try:
        numbering = doc.part.numbering_part.element
    except (NotImplementedError, AttributeError, KeyError):
        return
    n = 0
    for lvl in numbering.iter(qn("w:lvl")):
        fmt = lvl.find(qn("w:numFmt"))
        if fmt is None or fmt.get(qn("w:val")) != "bullet":
            continue
        txt = lvl.find(qn("w:lvlText"))
        if txt is not None:
            txt.set(qn("w:val"), "\u2013")
            n += 1
        rpr = lvl.find(qn("w:rPr"))
        if rpr is not None:
            for rf in rpr.findall(qn("w:rFonts")):
                rpr.remove(rf)
            rf = OxmlElement("w:rFonts")
            for a in ("w:ascii", "w:hAnsi", "w:cs"):
                rf.set(qn(a), FONT)
            rpr.insert(0, rf)
    report["counts"]["bullet_levels"] = n


def process(docx_path: Path, plan_path: Path, out_path: Path,
            toc_pages_path: Path | None, report_path: Path | None,
            math_mode: str = "omml") -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    global BLOCK_TW, BLOCK_TW_AFTER
    # формула стоит «как следующая строка»: поправки подобраны замером (golden/gaps.py) по рендеру
    # LibreOffice; режим line добавляет ещё одну строку (420 twips = 21 pt)
    extra = 0 if plan.get("block_spacing", "line") == "none" else 420
    BLOCK_TW = str(40 + extra)
    BLOCK_TW_AFTER = str(184 + extra)
    toc_pages = None
    if toc_pages_path and toc_pages_path.exists():
        raw = json.loads(toc_pages_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            toc_pages = {e["text"]: e["page"] for e in raw}
        elif isinstance(raw, dict) and "map" in raw:
            toc_pages = raw["map"]
        else:
            toc_pages = raw
    report = {"steps": [], "warnings": list(plan.get("warnings", [])), "counts": {}}
    doc = Document(str(docx_path))

    format_titlepage(doc.paragraphs, plan, report)
    report["steps"].append("титульный лист")

    # Якорь для СОДЕРЖАНИЯ: первый заголовок документа (после титула)
    paras0 = doc.paragraphs
    anchor = None
    for p in paras0:
        if style_name(p._p).startswith("Heading"):
            anchor = p._p
            break
    if anchor is None:
        n_tp = len(plan.get("titlepage", []))
        if n_tp and len(paras0) > n_tp:
            anchor = paras0[n_tp]._p
        else:
            report["warnings"].append("не найден якорь для вставки СОДЕРЖАНИЯ")

    gost_bullets(doc, report)
    fix_explications(doc, report)
    fix_headings(doc, plan, report)
    report["steps"].append("заголовки/структурные элементы")
    fix_figures(doc, report)
    report["steps"].append("рисунки/подписи (keepNext)")
    fix_tables(doc, report)
    report["steps"].append("таблицы (tblHeader, границы, формат)")
    if math_mode == "svg":
        fix_svg_equations(doc, plan, report)
    else:
        fix_equations(doc, plan, report)
    report["steps"].append("нумерация формул")
    # листинги: keepLines на уровне абзаца (страховка от разрыва)
    for p in doc.paragraphs:
        if style_name(p._p) in ("SourceCode", "Source Code"):
            set_ppr_attr(p._p, "w:keepLines")

    relocate_bibliography(doc, plan, report)
    report["steps"].append("перенос списка источников перед приложениями")
    # ручное оформление — последним: перекрывает автоматическое
    apply_manual_styles(doc, plan, report)

    # СОДЕРЖАНИЕ вставляем после всех правок — перед первым заголовком
    if anchor is not None:
        for e in build_toc_elements(plan, toc_pages, report):
            anchor.addprevious(e)
        report["steps"].append(
            f"СОДЕРЖАНИЕ: {report['counts'].get('toc_entries', 0)} пунктов")
    svg_fallback(docx_path, report)

    set_hyphenation(doc, plan.get("hyphenation", True), report)

    # свойства документа
    cp = doc.core_properties
    cp.title = plan.get("metadata", {}).get("title", "Отчёт")
    cp.author = plan.get("metadata", {}).get("author", "")
    cp.comments = "Сгенерировано LDE (pandoc + постобработка)"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    if add_svg_png_fallbacks is not None:
        emb = add_svg_png_fallbacks(out_path)
        if emb.get("svg_parts"):
            report["steps"].append(
                f"SVG->PNG ({emb.get('mode')}): {emb.get('fallbacks_added')}/"
                f"{emb.get('svg_parts')}")
            report["png_fallbacks"] = emb
            for e in emb.get("errors", []):
                report["warnings"].append(f"SVG fallback: {e}")
    report["steps"].append(f"сохранено: {out_path}")

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Postprocess pandoc DOCX for GOST report")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--toc-pages", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--math", default="omml", help="omml|svg (влияет на нумерацию картинок-формул)")
    args = ap.parse_args()
    report = process(Path(args.docx), Path(args.plan), Path(args.out),
                     Path(args.toc_pages) if args.toc_pages else None,
                     Path(args.report) if args.report else None,
                     math_mode=args.math)
    n_warn = len(report.get("warnings", []))
    print(f"postprocess: {args.out} (предупреждений: {n_warn})")
    for w in report.get("warnings", [])[:10]:
        print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
