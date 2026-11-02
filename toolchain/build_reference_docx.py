#!/usr/bin/env python3
"""build_reference_docx.py — детерминированная генерация reference.docx (ГОСТ-стили).

Основа — официальный дефолтный reference.docx pandoc
(`pandoc --print-default-data-file reference.docx`), в который вносятся правки:
шрифт Times New Roman 14 pt, интервал 1,5, отступ 1,25 см, поля страницы
(30/15/20/20 мм), нижний колонтитул с номером страницы, стили заголовков,
подписей, таблиц, библиографии и листингов.

Запуск (обычно вызывается из convert/latex2docx.sh автоматически):
    toolchain/build_reference_docx.py [--out convert/reference.docx]

Требования: python из toolchain/venv (python-docx). pandoc — в PATH.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt

FONT = "Times New Roman"
FONT_MONO = "Courier New"
SIZE = 14  # pt, п.6.1.1 ГОСТ 7.32-2017: не менее 12, рекомендуемый Times New Roman

ROOT = Path(__file__).resolve().parent.parent


def find_pandoc() -> str:
    cand = os.environ.get("LDE_PANDOC") or shutil.which("pandoc") or str(Path.home() / ".local/bin/pandoc")
    if not Path(cand).exists():
        raise SystemExit("build_reference_docx: pandoc не найден (нужен для базового reference.docx)")
    return cand


# --------------------------------------------------------------- helpers ----
def _set_rfonts(rpr, name: str) -> None:
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)
    # шрифт темы (asciiTheme…) перекрывает явный — у заголовков pandoc это «заголовочный» шрифт темы
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]


def _set_pPr(style, *, jc=None, first_line_cm=None, left_cm=None,
             line=None, before_pt=None, after_pt=None,
             keep_next=None, keep_lines=None, page_break=None,
             outline=None, snap_to_grid=None) -> None:
    ppr = style.element.get_or_add_pPr()
    if jc is not None:
        for old in ppr.findall(qn("w:jc")):
            ppr.remove(old)
        el = OxmlElement("w:jc")
        el.set(qn("w:val"), {"left": "left", "center": "center",
                             "both": "both", "right": "right"}[jc])
        ppr.append(el)
    if first_line_cm is not None or left_cm is not None:
        for old in ppr.findall(qn("w:ind")):
            ppr.remove(old)
        ind = OxmlElement("w:ind")
        if left_cm is not None:
            ind.set(qn("w:left"), str(int(Cm(left_cm).twips)))
        else:
            ind.set(qn("w:left"), "0")
        ind.set(qn("w:firstLine"), str(int(Cm(first_line_cm or 0).twips)))
        ppr.append(ind)
    if line is not None or before_pt is not None or after_pt is not None:
        for old in ppr.findall(qn("w:spacing")):
            ppr.remove(old)
        sp = OxmlElement("w:spacing")
        if before_pt is not None:
            sp.set(qn("w:before"), str(int(before_pt * 20)))
        else:
            sp.set(qn("w:before"), "0")
        if after_pt is not None:
            sp.set(qn("w:after"), str(int(after_pt * 20)))
        else:
            sp.set(qn("w:after"), "0")
        if line is not None:
            sp.set(qn("w:line"), str(int(line * 240)))
            sp.set(qn("w:lineRule"), "auto")
        ppr.append(sp)
    flags = [("w:keepNext", keep_next), ("w:keepLines", keep_lines),
             ("w:pageBreakBefore", page_break), ("w:snapToGrid", snap_to_grid)]
    for tag, val in flags:
        if val is None:
            continue
        for old in ppr.findall(qn(tag)):
            ppr.remove(old)
        if val:
            ppr.append(OxmlElement(tag))
    if outline is not None:
        for old in ppr.findall(qn("w:outlineLvl")):
            ppr.remove(old)
        el = OxmlElement("w:outlineLvl")
        el.set(qn("w:val"), str(outline))
        ppr.append(el)


def setup_style(style, *, font=FONT, size=SIZE, bold=False, italic=False,
                **ppr_kw) -> None:
    style.font.name = font
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = italic
    # ГОСТ: текст и заголовки — чёрные (в шаблоне pandoc заголовки синие, цветом темы)
    from docx.shared import RGBColor
    style.font.color.rgb = RGBColor(0, 0, 0)
    for c in style.element.iter(qn("w:color")):
        for a in ("w:themeColor", "w:themeShade", "w:themeTint"):
            if c.get(qn(a)) is not None:
                del c.attrib[qn(a)]
    rpr = style.element.get_or_add_rPr()
    _set_rfonts(rpr, font)
    # цвет — авто/чёрный
    _set_pPr(style, **ppr_kw)


def add_page_number_footer(section) -> None:
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.text = ""
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ppr = p._p.get_or_add_pPr()
    # Отступов нет
    for old in ppr.findall(qn("w:ind")):
        ppr.remove(old)
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), "0")
    ind.set(qn("w:firstLine"), "0")
    ppr.append(ind)

    def run(text: str):
        r = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        rfonts = OxmlElement("w:rFonts")
        for a in ("w:ascii", "w:hAnsi", "w:cs"):
            rfonts.set(qn(a), FONT)
        rpr.append(rfonts)
        sz = OxmlElement("w:sz"); sz.set(qn("w:val"), str(SIZE * 2)); rpr.append(sz)
        r.append(rpr)
        return r

    # PAGE как простое поле
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), " PAGE ")
    r = run("1"); fld.append(r)
    p._p.append(fld)


def normalize_zip_timestamps(path: Path, dt=(2026, 1, 1, 0, 0, 0)) -> None:
    """Перепаковать docx с фиксированными zip-таймстампами (воспроизводимость)."""
    tmp = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in sorted(zin.infolist(), key=lambda i: i.filename):
            data = zin.read(item.filename)
            info = zipfile.ZipInfo(item.filename, date_time=dt)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zout.writestr(info, data)
    tmp.replace(path)


# ------------------------------------------------------------------ main ----
BLOCK = 0   # отступ до/после заголовков, рисунков, подписей, формул (пт): 0 — нет (ГОСТ/методичка), 21 — строка


def build(out_path: Path) -> None:
    pandoc = find_pandoc()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
        base = Path(tf.name)
    try:
        with base.open("wb") as fh:
            subprocess.run([pandoc, "--print-default-data-file", "reference.docx"],
                           stdout=fh, check=True)
        doc = Document(str(base))

        # --- базовые стили ---
        normal = doc.styles["Normal"]
        setup_style(normal, jc="both", first_line_cm=1.25, line=1.5,
                    before_pt=0, after_pt=0)

        for name in ("Body Text", "First Paragraph", "Compact"):
            try:
                setup_style(doc.styles[name], jc="both", first_line_cm=1.25,
                            line=1.5, before_pt=0, after_pt=0)
            except KeyError:
                pass

        # --- заголовки: раздел/подраздел/пункт (п.6.2.3) ---
        for name, outline in (("Heading 1", 0), ("Heading 2", 1), ("Heading 3", 2)):
            try:
                st = doc.styles[name]
                setup_style(st, bold=True, jc="left", first_line_cm=1.25,
                            line=1.5, before_pt=0 if outline == 0 else BLOCK,
                            after_pt=BLOCK, keep_next=True, keep_lines=True,
                            outline=outline,
                            page_break=outline == 0)
            except KeyError:
                pass

        # --- структурные элементы: по центру, ПРОПИСНЫМИ, с новой страницы ---
        try:
            struct = doc.styles.add_style("Structural Heading", WD_STYLE_TYPE.PARAGRAPH)
        except ValueError:
            struct = doc.styles["Structural Heading"]
        struct.base_style = doc.styles["Normal"]
        setup_style(struct, bold=True, jc="center", first_line_cm=0, left_cm=0,
                    line=1.5, before_pt=0, after_pt=BLOCK, keep_next=True,
                    keep_lines=True, page_break=True, outline=0)

        # --- заголовок приложения (название приложения, по центру) ---
        try:
            appt = doc.styles.add_style("Appendix Title", WD_STYLE_TYPE.PARAGRAPH)
        except ValueError:
            appt = doc.styles["Appendix Title"]
        appt.base_style = doc.styles["Normal"]
        setup_style(appt, bold=True, jc="center", first_line_cm=0, left_cm=0,
                    line=1.5, before_pt=0, after_pt=BLOCK, keep_next=True, keep_lines=True)

        # --- подписи рисунков (п.6.5.7) ---
        try:
            cap = doc.styles["Image Caption"]
            setup_style(cap, jc="center", first_line_cm=0, left_cm=0, line=1.5,
                        before_pt=0, after_pt=BLOCK, keep_lines=True)
        except KeyError:
            pass
        try:
            figp = doc.styles["Captioned Figure"]
            setup_style(figp, jc="center", first_line_cm=0, left_cm=0, line=1.5,
                        before_pt=BLOCK, after_pt=0, keep_next=True, keep_lines=True)
        except KeyError:
            pass
        try:
            tcap = doc.styles["Table Caption"]
            setup_style(tcap, jc="left", first_line_cm=0, left_cm=0, line=1.5,
                        before_pt=BLOCK, after_pt=0, keep_next=True, keep_lines=True)
        except KeyError:
            pass

        # --- библиография (п.6.16: с абзацного отступа) ---
        try:
            bib = doc.styles["Bibliography"]
            setup_style(bib, jc="both", first_line_cm=1.25, line=1.5,
                        before_pt=0, after_pt=0)
        except KeyError:
            pass

        # --- формула-картинка (--math=svg): по центру, без отступа ---
        try:
            eqimg = doc.styles.add_style("Equation Image", WD_STYLE_TYPE.PARAGRAPH)
        except ValueError:
            eqimg = doc.styles["Equation Image"]
        eqimg.base_style = doc.styles["Normal"]
        setup_style(eqimg, jc="center", first_line_cm=0, left_cm=0,
                    line=1.5, before_pt=0, after_pt=0, keep_lines=True)

        # --- листинги: моноширинный, без переноса колонок ---
        try:
            code = doc.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
        except ValueError:
            code = doc.styles["Source Code"]
        code.base_style = doc.styles["Normal"]
        setup_style(code, font=FONT_MONO, size=12, jc="left", first_line_cm=0,
                    left_cm=1.25, line=1.0, before_pt=0, after_pt=0,
                    keep_lines=True)

        # --- параметры страницы и колонтитул с номером страницы ---
        sec = doc.sections[0]
        sec.page_width = Mm(210)
        sec.page_height = Mm(297)
        sec.left_margin = Cm(3.0)
        sec.right_margin = Cm(1.5)
        sec.top_margin = Cm(2.0)
        sec.bottom_margin = Cm(2.0)
        sec.header_distance = Cm(1.0)
        sec.footer_distance = Cm(1.0)
        # Титульный лист: номер страницы не проставляется (п.6.3.2), но страница
        # входит в нумерацию — отдельный пустой колонтитул первой страницы.
        sec.different_first_page_header_footer = True
        add_page_number_footer(sec)
        try:
            first = sec.first_page_footer
            first.is_linked_to_previous = False
            if first.paragraphs:
                first.paragraphs[0].text = ""
        except Exception:
            pass

        # --- фиксированные свойства (детерминированность) ---
        cp = doc.core_properties
        cp.title = "LDE GOST reference document"
        cp.author = "LDE (LaTeX Document Engineering)"
        cp.last_modified_by = "LDE"
        cp.revision = 1
        cp.created = _dt.datetime(2026, 1, 1, 0, 0, 0)
        cp.modified = _dt.datetime(2026, 1, 1, 0, 0, 0)
        cp.comments = "Generated by toolchain/build_reference_docx.py"

        doc.save(str(out_path))
        normalize_zip_timestamps(out_path)
        print(f"reference.docx: {out_path} ({out_path.stat().st_size} bytes)")
    finally:
        base.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate GOST reference.docx for pandoc")
    ap.add_argument("--out", default=str(ROOT / "convert" / "reference.docx"))
    ap.add_argument("--block-spacing", choices=["none", "line"], default="none",
                    help="отступы до/после заголовков, рисунков, формул: none — нет, line — одна строка")
    args = ap.parse_args()
    global BLOCK
    BLOCK = 0 if args.block_spacing == "none" else 21
    build(Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
