#!/usr/bin/env python3
"""fix_docx.py — идемпотентный ремонт дефектов DOCX (D1..D10) без Word.

Фиксеры строго идемпотентны: повторный запуск на исправленном файле не вносит
изменений. Пользовательский контент не удаляется (кроме пересборки СОДЕРЖАНИЯ
и перепростановки номеров — это и есть ремонт). Каждое изменение пишется в лог
out/docx-fixes.json.

По умолчанию работает на копии: out/main.docx -> out/main.fixed.docx.
С --in-place правит out/main.docx (исходник не сохраняется).

Использование:
    fix_docx.py --project <dir> [--docx FILE] [--out FILE] [--in-place] [--quiet]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "convert"))
from postprocess_docx import (  # noqa: E402
    FONT, TEXT_WIDTH_TWIPS, _add_tbl_borders, clear_indent, make_para,
    make_run, para_text, set_ppr_attr, style_name,
)
from embeds import add_svg_png_fallbacks  # noqa: E402

MM = 56.6929
A4_W, A4_H = 11906, 16838
MARGINS = {"left": int(30 * MM), "right": int(15 * MM),
           "top": int(20 * MM), "bottom": int(20 * MM)}
TEXT_WIDTH_EMU = int(16.5 * 360000)  # 16,5 см в EMU


class Fixer:
    def __init__(self, path: Path):
        self.path = path
        self.doc = Document(str(path))
        self.changes: list[dict] = []
        self.warnings: list[str] = []

    def log(self, defect: str, what: str, where: str = "") -> None:
        self.changes.append({"defect": defect, "fix": what, "where": where})

    # ------------------------------------------------------------- D1/D2 ---
    def fix_keepnext(self) -> None:
        paras = self.doc.paragraphs
        for i, p in enumerate(paras):
            sn = style_name(p._p)
            is_img = (sn in ("CaptionedFigure", "Figure")
                      or (p._p.find(".//" + qn("a:blip")) is not None
                          and sn != "EquationImage"))
            if is_img:
                ppr = p._p.find(qn("w:pPr"))
                if ppr is None or ppr.find(qn("w:keepNext")) is None:
                    set_ppr_attr(p._p, "w:keepNext")
                    self.log("D1", "keepNext добавлен абзацу с рисунком",
                             f"paragraph {i}")
            if sn in ("Heading1", "Heading2", "Heading3", "StructuralHeading"):
                ppr = p._p.find(qn("w:pPr"))
                if ppr is None or ppr.find(qn("w:keepNext")) is None:
                    set_ppr_attr(p._p, "w:keepNext")
                    self.log("D2", "keepNext добавлен заголовку",
                             f"paragraph {i} «{para_text(p._p)[:40]}»")
            if sn in ("ImageCaption", "TableCaption", "CaptionedFigure"):
                ppr = p._p.find(qn("w:pPr"))
                if ppr is None or ppr.find(qn("w:keepLines")) is None:
                    set_ppr_attr(p._p, "w:keepLines")
                    self.log("D1", "keepLines добавлен подписи", f"paragraph {i}")

    # ---------------------------------------------------------------- D3 ---
    def fix_tables(self) -> None:
        for ti, tbl in enumerate(self.doc.tables):
            if len(tbl.rows) > 1:
                tr0 = tbl.rows[0]._tr
                trPr = tr0.find(qn("w:trPr"))
                if trPr is None:
                    trPr = OxmlElement("w:trPr")
                    tr0.insert(0, trPr)
                if trPr.find(qn("w:tblHeader")) is None:
                    trPr.append(OxmlElement("w:tblHeader"))
                    self.log("D3", "tblHeader добавлен первой строке", f"table {ti}")
            need_borders = True
            tblPr = tbl._tbl.find(qn("w:tblPr"))
            if tblPr is not None:
                b = tblPr.find(qn("w:tblBorders"))
                if b is not None and b.find(qn("w:top")) is not None:
                    need_borders = False
            if need_borders:
                _add_tbl_borders(tbl._tbl)
                self.log("D3", "границы таблицы добавлены", f"table {ti}")
            for ri, row in enumerate(tbl.rows):
                trPr = row._tr.find(qn("w:trPr"))
                if trPr is None:
                    trPr = OxmlElement("w:trPr")
                    row._tr.insert(0, trPr)
                if trPr.find(qn("w:cantSplit")) is None:
                    trPr.append(OxmlElement("w:cantSplit"))
                    if ri == 0:
                        self.log("D3", "cantSplit добавлен строкам", f"table {ti}")

    # ---------------------------------------------------------------- D4 ---
    def _render_pdf(self) -> Path | None:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="lde-fix-"))
            out_docx = tmp / "fixed.docx"
            shutil.copy(self.path, out_docx)
            subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                            "--outdir", str(tmp), str(out_docx)],
                           capture_output=True, timeout=180)
            pdf = tmp / "fixed.pdf"
            return pdf if pdf.exists() else None
        except Exception:
            return None

    def _heading_pages(self, pdf: Path, headings: list[str]) -> dict[str, int]:
        try:
            try:
                import pymupdf
            except ImportError:
                import fitz as pymupdf
        except ImportError:
            return {}
        doc = pymupdf.open(str(pdf))
        pages_norm = []
        for page in doc:
            t = page.get_text("text").replace("\u00ad", "").replace("\u00a0", "")
            pages_norm.append(re.sub(r"\s+", "", t).lower())
        toc_word = "содержание"
        skip = {i for i, t in enumerate(pages_norm) if toc_word in t[:400]}
        result: dict[str, int] = {}
        last = 0
        for h in headings:
            target = re.sub(r"\s+", "", h).lower()
            for i in range(last, len(pages_norm)):
                if i in skip:
                    continue
                if target and target in pages_norm[i]:
                    result[h] = i + 1
                    last = i
                    break
        return result

    def _render_page_map(self, headings: list[str]) -> dict[str, int]:
        pdf = self._render_pdf()
        if not pdf:
            return {}
        return self._heading_pages(pdf, headings)

    def _heading_texts(self) -> list[str]:
        res = []
        for p in self.doc.paragraphs:
            if style_name(p._p) in ("Heading1", "Heading2", "Heading3",
                                    "StructuralHeading"):
                t = para_text(p._p).strip()
                if t.upper() == "СОДЕРЖАНИЕ":
                    continue
                res.append(t)
        return res

    def _current_toc(self) -> list[tuple[str, int | None]]:
        paras = self.doc.paragraphs
        toc_idx = next((i for i, p in enumerate(paras)
                        if para_text(p._p).strip().upper() == "СОДЕРЖАНИЕ"), None)
        if toc_idx is None:
            return []
        entries = []
        for p in paras[toc_idx + 1:]:
            sn = style_name(p._p)
            if sn.startswith("Heading") or sn == "StructuralHeading":
                break
            txt = para_text(p._p).strip()
            if not txt:
                continue
            m = re.search(r"^(.*?)[\s.]*(\d+)\s*$", txt)
            entries.append(((m.group(1) if m else txt).strip(" ."),
                            int(m.group(2)) if m else None))
        return entries

    def fix_toc(self) -> None:
        paras = self.doc.paragraphs
        toc_idx = None
        for i, p in enumerate(paras):
            if para_text(p._p).strip().upper() == "СОДЕРЖАНИЕ":
                toc_idx = i
                break
        headings = self._heading_texts()
        page_map = self._render_page_map(headings)
        current = self._current_toc()
        expected = [(h, page_map.get(h)) for h in headings]
        if current and page_map and len(current) == len(expected) and \
                all(cur[0].strip() == exp[0].strip() and cur[1] == exp[1]
                    for cur, exp in zip(current, expected)):
            return  # СОДЕРЖАНИЕ актуально — не трогаем (идемпотентность)
        if not page_map:
            self.warnings.append("D4: не удалось отрендерить DOCX — номера страниц "
                                 "в СОДЕРЖАНИИ будут пустыми (нужен LibreOffice)")
        if toc_idx is None:
            # вставляем СОДЕРЖАНИЕ перед первым заголовком
            anchor = None
            for p in paras:
                if style_name(p._p).startswith("Heading") or style_name(p._p) == "StructuralHeading":
                    anchor = p._p
                    break
            if anchor is None:
                self.warnings.append("D4: не найден якорь для вставки СОДЕРЖАНИЯ")
                return
            heading = make_para(style="StructuralHeading", text="СОДЕРЖАНИЕ",
                                bold=True, size_pt=14)
            anchor.addprevious(heading)
            anchor = heading
            self.log("D4", "раздел СОДЕРЖАНИЕ создан заново")
        else:
            heading = paras[toc_idx]._p
            anchor = heading
            # удаляем существующие пункты оглавления до следующего заголовка
            removed = 0
            nxt = anchor.getnext()
            while nxt is not None:
                if nxt.tag == qn("w:p"):
                    sn = style_name(nxt)
                    if sn.startswith("Heading") or sn == "StructuralHeading":
                        break
                target = nxt
                nxt = nxt.getnext()
                if target.tag == qn("w:p"):
                    parent = target.getparent()
                    if parent is not None:
                        parent.remove(target)
                        removed += 1
                elif target.tag != qn("w:sectPr"):
                    target.getparent().remove(target)
            if removed:
                self.log("D4", f"удалены устаревшие пункты СОДЕРЖАНИЯ ({removed})")
        # вставка новых пунктов после заголовка СОДЕРЖАНИЕ
        cursor = anchor
        for h in headings:
            # определить уровень
            level = 1
            for p in self.doc.paragraphs:
                if para_text(p._p).strip() == h:
                    sn = style_name(p._p)
                    level = {"Heading1": 1, "StructuralHeading": 1,
                             "Heading2": 2, "Heading3": 3}.get(sn, 1)
                    break
            page = page_map.get(h)
            left = {1: 0.0, 2: 0.75, 3: 1.5}[level]
            runs = [make_run(h), make_run("\t"),
                    make_run(str(page) if page is not None else "")]
            entry = make_para(runs=runs, jc="left", left_cm=left,
                              first_line_cm=0, line=1.5, before_pt=0, after_pt=0,
                              keep_lines=True,
                              tabs=[{"val": "right", "pos": TEXT_WIDTH_TWIPS,
                                     "leader": "dot"}])
            cursor.addnext(entry)
            cursor = entry
        self.log("D4", f"СОДЕРЖАНИЕ пересобрано: {len(headings)} пунктов")

    # ---------------------------------------------------------------- D5 ---
    def fix_numbering(self) -> None:
        """D5: перепроставить номера рисунков/таблиц/формул по порядку.

        Опираемся на стили абзацев (ImageCaption/TableCaption/OMML+номер),
        поэтому пункты СОДЕРЖАНИЯ и списка источников не затрагиваются.
        """
        fig = tab = eq = 0
        cur_letter = None
        in_toc = False
        for p in self.doc.paragraphs:
            sn = style_name(p._p)
            t = para_text(p._p).strip()
            if sn == "StructuralHeading" and t.upper() == "СОДЕРЖАНИЕ":
                in_toc = True
                continue
            if in_toc:
                if sn.startswith("Heading") or sn == "StructuralHeading":
                    in_toc = False
                else:
                    continue
            m = re.match(r"^ПРИЛОЖЕНИЕ\s+([А-ЯA-Z])", t.upper())
            if m and (sn == "StructuralHeading" or sn.startswith("Heading")):
                cur_letter = m.group(1)
                fig = tab = eq = 0
                continue
            if sn == "ImageCaption":
                mm = re.match(r"^Рисунок\s+([А-ЯA-Z]?\.?\d+)\s+—\s*(.*)$", t)
                fig += 1
                expected = f"{cur_letter}.{fig}" if cur_letter else str(fig)
                if mm and mm.group(1) != expected:
                    self._rewrite_paragraph(p._p, f"Рисунок {expected} — {mm.group(2)}")
                    self.log("D5", f"перенумерован рисунок {mm.group(1)} -> {expected}")
                continue
            if sn == "TableCaption":
                mm = re.match(r"^Таблица\s+([А-ЯA-Z]?\.?\d+)\s+—\s*(.*)$", t)
                tab += 1
                expected = f"{cur_letter}.{tab}" if cur_letter else str(tab)
                if mm and mm.group(1) != expected:
                    self._rewrite_paragraph(p._p, f"Таблица {expected} — {mm.group(2)}")
                    self.log("D5", f"перенумерована таблица {mm.group(1)} -> {expected}")
                continue
            if p._p.find(qn("m:oMath")) is not None:
                last_run = p._p.findall(qn("w:r"))
                if not last_run:
                    continue
                lr = last_run[-1]
                txt = "".join(x.text or "" for x in lr.iter(qn("w:t")))
                mm = re.match(r"^\(([А-ЯA-Z]?\.?\d+)\)$", txt.strip())
                if mm:
                    eq += 1
                    expected = f"{cur_letter}.{eq}" if cur_letter else str(eq)
                    if mm.group(1) != expected:
                        for x in lr.iter(qn("w:t")):
                            x.text = f"({expected})"
                        self.log("D5", f"перенумерована формула ({mm.group(1)}) -> "
                                       f"({expected})")

    @staticmethod
    def _rewrite_paragraph(p_el, text: str) -> None:
        # сохраняем pPr, заменяем все run одним
        for r in list(p_el.findall(qn("w:r"))):
            p_el.remove(r)
        p_el.append(make_run(text))

    # ---------------------------------------------------------------- D8 ---
    def fix_image_sizes(self) -> None:
        for ext in self.doc.element.body.iter(qn("wp:extent")):
            cx = int(ext.get("cx", 0))
            cy = int(ext.get("cy", 0))
            if cx > TEXT_WIDTH_EMU:
                ratio = TEXT_WIDTH_EMU / cx
                ext.set("cx", str(int(cx * ratio)))
                ext.set("cy", str(int(cy * ratio)))
                self.log("D8", f"картинка ужата до полосы набора {cx / 360000:.1f} см "
                               f"-> {TEXT_WIDTH_EMU / 360000:.1f} см")

    # ---------------------------------------------------------------- D9 ---
    def fix_page_setup(self) -> None:
        sec = self.doc.sections[0]
        pg = self.doc.element.body.find(qn("w:sectPr"))
        if pg is None:
            return
        pgsz = pg.find(qn("w:pgSz"))
        if pgsz is None or int(pgsz.get(qn("w:w"), 0)) != A4_W:
            if pgsz is None:
                pgsz = OxmlElement("w:pgSz")
                pg.insert(0, pgsz)
            pgsz.set(qn("w:w"), str(A4_W))
            pgsz.set(qn("w:h"), str(A4_H))
            self.log("D9", "формат страницы установлен A4")
        mar = pg.find(qn("w:pgMar"))
        if mar is None:
            mar = OxmlElement("w:pgMar")
            pg.append(mar)
            self.log("D9", "поля страницы созданы")
        for side, val in MARGINS.items():
            cur = int(mar.get(qn("w:" + side), 0))
            if abs(cur - val) > 28:  # >0.5 мм
                mar.set(qn("w:" + side), str(val))
                self.log("D9", f"поле {side}: {cur / MM:.1f} мм -> {val / MM:.0f} мм")
        if pg.find(qn("w:titlePg")) is None:
            pg.append(OxmlElement("w:titlePg"))
            self.log("D9", "titlePg: номер страницы на титуле не печатается (п.6.3.2)")
        if pg.find(qn("w:footerReference")) is None:
            self._add_footer(sec)
            self.log("D9", "колонтитул с номером страницы создан (п.6.3.1)")

    def _add_footer(self, sec) -> None:
        footer = sec.footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        p.text = ""
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), " PAGE ")
        fld.append(make_run("1"))
        p._p.append(fld)
        try:
            sec.different_first_page_header_footer = True
        except Exception:
            pass

    # --------------------------------------------------------------- D10 ---
    def fix_code(self) -> None:
        style_fixed = False
        for st in self.doc.styles:
            try:
                if st.style_id in ("SourceCode", "Source Code"):
                    rpr = st.element.get_or_add_rPr()
                    rfonts = rpr.find(qn("w:rFonts"))
                    if rfonts is None:
                        rfonts = OxmlElement("w:rFonts")
                        rpr.insert(0, rfonts)
                    for a in ("w:ascii", "w:hAnsi", "w:cs"):
                        if rfonts.get(qn(a)) != "Courier New":
                            rfonts.set(qn(a), "Courier New")
                            style_fixed = True
                    break
            except Exception:
                continue
        if style_fixed:
            self.log("D10", "стиль SourceCode: моноширинный Courier New")
        for i, p in enumerate(self.doc.paragraphs):
            if style_name(p._p) == "SourceCode":
                ppr = p._p.find(qn("w:pPr"))
                if ppr is None or ppr.find(qn("w:keepLines")) is None:
                    set_ppr_attr(p._p, "w:keepLines")
                    self.log("D10", "keepLines добавлен листингу", f"paragraph {i}")

    # --------------------------------------------------------------- D7 ----
    def fix_svg_fallback(self) -> None:
        res = add_svg_png_fallbacks(self.path)
        if res.get("svg_parts"):
            self.log("D7", f"SVG->PNG fallback: {res.get('fallbacks_added')} из "
                           f"{res.get('svg_parts')}")
            for e in res.get("errors", []):
                self.warnings.append(f"D7: {e}")

    # --------------------------------------------------------------- run ---
    def run(self) -> None:
        self.fix_keepnext()
        self.fix_tables()
        self.fix_toc()
        self.fix_numbering()
        self.fix_image_sizes()
        self.fix_page_setup()
        self.fix_code()
        self.fix_svg_fallback()
        self.warnings.append("D6: inline-картинки/формулы в тексте чинятся только "
                             "сменой режима (--math=omml), автоматический перенос "
                             "по базовой линии не выполняется")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--docx", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).resolve()
    src = Path(args.docx) if args.docx else proj / "out" / "main.docx"
    if not src.exists():
        print(f"fix_docx: нет DOCX: {src} (сначала lde docx)", file=sys.stderr)
        return 1
    if args.in_place:
        dst = src
    elif args.out:
        dst = Path(args.out)
    else:
        dst = proj / "out" / "main.fixed.docx"

    # работаем на копии: fixer сохраняет в dst
    tmp = dst
    if src != dst:
        shutil.copy(src, tmp)
    else:
        pass

    fixer = Fixer(tmp)
    fixer.run()
    fixer.doc.save(str(tmp))

    # рендер исправленного DOCX для последующего QA
    pdf_path = tmp.with_suffix(".pdf")
    try:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", str(tmp.parent), str(tmp)],
                       capture_output=True, timeout=180)
        produced = tmp.parent / (tmp.stem + ".pdf")
        if produced.exists() and produced != pdf_path:
            produced.replace(pdf_path)
    except Exception as exc:
        fixer.warnings.append(f"рендер исправленного DOCX не удался: {exc}")

    report = {
        "input": str(src), "output": str(tmp),
        "rendered_pdf": str(pdf_path) if pdf_path.exists() else None,
        "changes": fixer.changes,
        "warnings": fixer.warnings,
        "changes_count": len(fixer.changes),
    }
    rp = proj / "out" / "docx-fixes.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"fix_docx: {src.name} -> {tmp.name}: исправлений "
              f"{len(fixer.changes)} -> {rp}", file=sys.stderr)
        for c in fixer.changes[:25]:
            print(f"  [{c['defect']}] {c['fix']} {c.get('where', '')}", file=sys.stderr)
        for w in fixer.warnings:
            print(f"  [warning] {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
