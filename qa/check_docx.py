#!/usr/bin/env python3
"""check_docx.py — структурные проверки DOCX по каталогу дефектов (D1..D10).

Проверяет `out/main.docx` (и, если есть, рендер `out/main.docx.pdf`):
  D1 подпись рисунка уехала на другую страницу / без keepNext
  D2 заголовок внизу страницы
  D3 таблица разорвана без повторяющейся шапки
  D4 оглавление устарело/пустое/не совпадает с заголовками
  D5 нумерация рисунков/таблиц/формул разъехалась
  D6 inline-формула картинкой (риск смещения по базовой линии)
  D7 SVG без PNG-fallback
  D8 картинки без явных размеров / гигантские/мелкие
  D10 листинги без моноширинного шрифта / keepLines

Ссылки: docs/docx-defects.md. Формат вывода: JSON (+ человекочитаемо в stderr).
Код возврата: 0 — дефектов-ошибок нет; 1 — есть ошибки.

Использование:
    check_docx.py --project <dir> [--docx FILE] [--pdf FILE] [--out FILE] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent


def err(issues, did, msg, where="", severity="error", critical=True):
    issues.append({"id": did, "severity": severity, "critical": critical,
                   "message": msg, "where": where})


def para_text(p_el) -> str:
    return "".join(t.text or "" for t in p_el.iter(qn("w:t")))


def style_name(p_el) -> str:
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        st = ppr.find(qn("w:pStyle"))
        if st is not None:
            return st.get(qn("w:val"))
    return ""


def has_ppr_flag(p_el, tag: str) -> bool:
    ppr = p_el.find(qn("w:pPr"))
    return ppr is not None and ppr.find(qn(tag)) is not None


def page_texts(pdf_path: Path):
    """Текст по страницам: [(номер_страницы, [(y0,y1,text)])]."""
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        return None
    result = []
    doc = pymupdf.open(str(pdf_path))
    for page in doc:
        blocks = []
        for b in page.get_text("blocks"):
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            blocks.append((y0, y1, " ".join(text.split())))
        result.append((page.number + 1, blocks, page.rect.height))
    return result


def page_images(pdf_path: Path):
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        return None
    out = {}
    doc = pymupdf.open(str(pdf_path))
    for page in doc:
        infos = page.get_image_info()
        out[page.number + 1] = [(i.get("bbox"), i.get("width"), i.get("height"))
                                for i in infos]
    return out


# ------------------------------------------------------------------ checks --
def check_render(doc, docx_path, pdf_path, issues, counts):
    """Проверки по рендеру DOCX->PDF (D1, D2, D3-частично)."""
    pages = page_texts(pdf_path) if pdf_path else None
    if not pages:
        issues.append({"id": "RENDER", "severity": "warning", "critical": False,
                       "message": "рендер DOCX->PDF недоступен — проверки D1/D2/D3 "
                                  "(геометрия страниц) не выполнены",
                       "where": str(pdf_path) if pdf_path else ""})
        return
    imgs = page_images(pdf_path) or {}

    # --- D1: подпись рисунка не на странице с рисунком ---
    caption_re = re.compile(r"^Рисунок\s+([А-ЯA-Z]?\.?\d+)\s+—")
    for pno, blocks, _h in pages:
        for (y0, y1, txt) in blocks:
            if not caption_re.match(txt):
                continue
            has_img = bool(imgs.get(pno))
            if not has_img:
                err(issues, "D1",
                    f"подпись «{txt[:60]}» на стр.{pno}, но рисунка на этой "
                    f"странице нет — подпись оторвалась от рисунка",
                    where=f"page {pno}")
            counts["figure_captions"] = counts.get("figure_captions", 0) + 1

    # --- D2: заголовок в самом низу страницы (оторвётся от текста) ---
    heading_re = re.compile(r"^(\d+(\.\d+)*\s+[А-ЯЁA-Z]|[А-ЯЁ ]{4,}$)")
    for pno, blocks, h in pages:
        text_blocks = [b for b in blocks if b[2].strip()]
        if len(text_blocks) < 3:
            continue
        # последний содержательный блок (исключая номер страницы внизу)
        body = [b for b in text_blocks if b[0] < h - 60]
        if not body:
            continue
        y0, y1, txt = body[-1]
        if y1 > h - 75 and heading_re.match(txt) and len(txt) < 120:
            err(issues, "D2",
                f"заголовок «{txt[:60]}» внизу страницы {pno} (y1={y1:.0f} "
                f"из {h:.0f}) — оторвётся от текста",
                where=f"page {pno}", severity="warning")
            counts["headings_at_bottom"] = counts.get("headings_at_bottom", 0) + 1

    # --- D3: таблица разорвана без повторения шапки ---
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        pdfdoc = pymupdf.open(str(pdf_path))
        tab_caption_re = re.compile(r"^Таблица\s+([А-ЯA-Z]?\.?\d+)\s+—\s*(.*)$")
        tbl_caps = []
        for pno, blocks, _h in pages:
            for (_y0, _y1, txt) in blocks:
                m = tab_caption_re.match(txt)
                if m:
                    tbl_caps.append((m.group(1), m.group(2)[:40], pno))
        for num, name, pno in tbl_caps:
            if pno > 1 and any(
                    "Продолжение таблицы" in b[2] for b in pages[pno - 1][1]):
                continue
            # ищем подозрительный разрыв: следующая страница начинается с чисел
            if pno < len(pages):
                nxt = [b for b in pages[pno][1] if b[2].strip()][:2]
                if nxt and re.match(r"^[\d\s,.\-]+$", nxt[0][2]) and nxt[0][0] < 150:
                    err(issues, "D3",
                        f"таблица «Таблица {num} — {name}» похоже разорвана: "
                        f"на стр.{pno + 1} продолжение без «Продолжение таблицы»",
                        where=f"page {pno}", severity="warning")
                    counts["tables_split"] = counts.get("tables_split", 0) + 1
        pdfdoc.close()
    except Exception:
        pass


def check_xml(doc, docx_path, issues, counts):
    body = doc.element.body
    paras = doc.paragraphs

    # --- D1 (XML): рисунок/подпись без keepNext, подпись не рядом ---
    for i, p in enumerate(paras):
        sn = style_name(p._p)
        if sn == "EquationImage":
            continue  # формула-картинка (--math=svg): не рисунок и не inline-формула
        if sn in ("CaptionedFigure", "Figure") or p._p.find(".//" + qn("a:blip")) is not None:
            if not has_ppr_flag(p._p, "w:keepNext"):
                err(issues, "D1",
                    "абзац с рисунком без keepNext — подпись может уехать на "
                    "другую страницу",
                    where=f"paragraph {i}", severity="warning")
            nxt = paras[i + 1] if i + 1 < len(paras) else None
            if nxt is not None and style_name(nxt._p) != "ImageCaption":
                err(issues, "D1",
                    "после рисунка нет абзаца подписи (Image Caption)",
                    where=f"paragraph {i}", severity="warning")
        if sn == "ImageCaption" and not has_ppr_flag(p._p, "w:keepLines"):
            err(issues, "D1", "подпись рисунка без keepLines",
                where=f"paragraph {i}", severity="warning")

    # --- D2 (XML): заголовок без keepNext ---
    for i, p in enumerate(paras):
        if style_name(p._p) in ("Heading1", "Heading2", "Heading3"):
            if not has_ppr_flag(p._p, "w:keepNext"):
                err(issues, "D2", f"заголовок «{para_text(p._p)[:50]}» без keepNext",
                    where=f"paragraph {i}", severity="warning")

    # --- D3 (XML): шапка таблицы не повторяется, строки не cantSplit ---
    for ti, tbl in enumerate(doc.tables):
        if len(tbl.rows) > 1:
            tr0 = tbl.rows[0]._tr
            trPr = tr0.find(qn("w:trPr"))
            if trPr is None or trPr.find(qn("w:tblHeader")) is None:
                err(issues, "D3",
                    f"таблица {ti}: у первой строки нет tblHeader — при переносе "
                    f"шапка не повторится",
                    where=f"table {ti}")
                counts["tables_no_header"] = counts.get("tables_no_header", 0) + 1

    # --- D4: СОДЕРЖАНИЕ ---
    toc_start = None
    for i, p in enumerate(paras):
        t = para_text(p._p).strip().upper()
        if t == "СОДЕРЖАНИЕ":
            toc_start = i
            break
    heading_texts = []
    for p in paras:
        if style_name(p._p) in ("Heading1", "Heading2", "Heading3", "StructuralHeading"):
            txt = para_text(p._p).strip()
            if txt.upper() == "СОДЕРЖАНИЕ":
                continue
            heading_texts.append(txt)
    if toc_start is None:
        err(issues, "D4", "в DOCX нет раздела СОДЕРЖАНИЕ", where="")
        counts["toc_entries"] = 0
    else:
        entries = []
        i = toc_start + 1
        while i < len(paras):
            p = paras[i]
            txt = para_text(p._p).strip()
            if style_name(p._p) in ("Heading1", "Heading2", "Heading3", "StructuralHeading"):
                break
            if txt:
                m = re.search(r"^(.*?)[\s.]*(\d+)\s*$", txt)
                entries.append({"text": (m.group(1) if m else txt).strip(" .\t"),
                                "page": int(m.group(2)) if m else None,
                                "xml": p._p})
            i += 1
        counts["toc_entries"] = len(entries)
        # сверка пунктов оглавления с заголовками
        norm = lambda s: " ".join(s.replace("\u00a0", " ").split()).lower().strip(" .")
        h_norm = [norm(h) for h in heading_texts]
        e_norm = [norm(e["text"]) for e in entries]
        for e in entries:
            if e["page"] is None:
                err(issues, "D4",
                    f"в СОДЕРЖАНИИ у пункта «{e['text'][:50]}» нет номера страницы",
                    where="toc")
        for h, hn in zip(heading_texts, h_norm):
            if hn and not any(hn == en or hn in en for en in e_norm):
                err(issues, "D4",
                    f"заголовка «{h[:50]}» нет в СОДЕРЖАНИИ — оглавление устарело",
                    where="toc")
        for e in entries:
            en = norm(e["text"])
            if en and not any(en == hn or en in hn for hn in h_norm):
                err(issues, "D4",
                    f"пункт СОДЕРЖАНИЯ «{e['text'][:50]}» не соответствует "
                    f"заголовкам (устарело)", where="toc", severity="warning")
        # поле TOC без кэша
        for it in body.iter(qn("w:instrText")):
            if "TOC" in (it.text or "").upper():
                err(issues, "D4", "в DOCX осталось поле TOC (без статического "
                                  "оглавления)", where="field")
                break

    # --- D5: нумерация рисунков/таблиц/формул ---
    fig_nums = []
    tab_nums = []
    for p in paras:
        t = para_text(p._p).strip()
        m = re.match(r"^Рисунок\s+([А-ЯA-Z]?\.?\d+)\s+—", t)
        if m:
            fig_nums.append(m.group(1))
        m = re.match(r"^Таблица\s+([А-ЯA-Z]?\.?\d+)\s+—", t)
        if m:
            tab_nums.append(m.group(1))
    eq_nums = []
    for p in paras:
        t = para_text(p._p).strip()
        m = re.search(r"\(([А-ЯA-Z]?\.?\d+)\)\s*$", t)
        if m and p._p.find(qn("m:oMath")) is not None:
            eq_nums.append(m.group(1))
    def expected_sequence(nums: list[str]) -> list[str]:
        """Ожидаемая последовательность с учётом сквозной (1,2,..) и
        приложений (А.1, А.2, Б.1, ...; п.6.5.5, 6.6.4, 6.8.5 ГОСТ 7.32-2017)."""
        exp: list[str] = []
        main_i = 0
        app_counter: dict[str, int] = {}
        for n in nums:
            if "." in n:
                prefix = n.split(".", 1)[0]
                app_counter[prefix] = app_counter.get(prefix, 0) + 1
                exp.append(f"{prefix}.{app_counter[prefix]}")
            else:
                main_i += 1
                exp.append(str(main_i))
        return exp

    for kind, nums in (("рисунков", fig_nums), ("таблиц", tab_nums), ("формул", eq_nums)):
        expected = expected_sequence(nums)
        if nums != expected:
            err(issues, "D5",
                f"нумерация {kind} разъехалась: {nums} (ожидалось {expected})")
    counts.update({"figures": len(fig_nums), "tables": len(tab_nums),
                   "equations": len(eq_nums)})

    # --- D6: inline-формула картинкой внутри текста ---
    for i, p in enumerate(paras):
        if style_name(p._p) == "EquationImage":
            continue
        blips = p._p.findall(".//" + qn("a:blip"))
        if not blips:
            continue
        txt = para_text(p._p).strip()
        if txt and not style_name(p._p) in ("CaptionedFigure", "Figure"):
            err(issues, "D6",
                f"inline-картинка внутри текста: «{txt[:40]}» — возможен сдвиг "
                f"по базовой линии; для inline-формул используйте OMML",
                where=f"paragraph {i}", severity="warning")
            counts["inline_images"] = counts.get("inline_images", 0) + 1

    # --- D7: SVG без PNG fallback ---
    svg_without_png = 0
    try:
        import zipfile
        with zipfile.ZipFile(docx_path) as z:
            docxml = z.read("word/document.xml").decode("utf-8", "replace")
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "replace")
        rel_map = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
        # ищем asvg:svgBlip и смотрим, есть ли у каждого blip png-fallback
        for m in re.finditer(r"<asvg:svgBlip[^>]*r:embed=\"([^\"]+)\"", docxml):
            rid = m.group(1)
            target = rel_map.get(rid, "")
            if target.lower().endswith(".svg"):
                # ищем blip, внутри которого лежит extLst с svgBlip
                if "png" not in target.lower():
                    svg_without_png += 1
        if svg_without_png:
            err(issues, "D7",
                f"{svg_without_png} SVG-картинок без PNG-fallback — старые "
                f"Word/LibreOffice увидят пустое место",
                where="media")
        counts["svg_without_fallback"] = svg_without_png
    except Exception as exc:
        issues.append({"id": "D7", "severity": "warning", "critical": False,
                       "message": f"не удалось проверить SVG-fallback: {exc}", "where": ""})

    # --- D8: размеры картинок ---
    try:
        with zipfile.ZipFile(docx_path) as z:
            docxml = z.read("word/document.xml").decode("utf-8", "replace")
        for m in re.finditer(r"<wp:extent\s+cx=\"(\d+)\"\s+cy=\"(\d+)\"", docxml):
            cx, cy = int(m.group(1)), int(m.group(2))
            width_cm = cx / 360000.0
            if width_cm > 16.5 + 0.2:
                err(issues, "D8", f"картинка шире полосы набора: {width_cm:.1f} см",
                    where="drawing")
            if width_cm < 0.5:
                err(issues, "D8", f"картинка подозрительно мала: {width_cm:.2f} см",
                    where="drawing", severity="warning")
        counts["drawings"] = len(re.findall(r"<wp:extent\b", docxml))
    except Exception:
        pass

    # --- D10: листинги ---
    # собрать шрифты/флаги стиля SourceCode (pandoc наследует их из стиля)
    style_fonts: list[str] = []
    style_keeps = False
    try:
        for st in doc.styles:
            if getattr(st, "style_id", "") in ("SourceCode", "Source Code"):
                el = st.element
                style_fonts = [rf.get(qn("w:ascii")) or "" for rf in el.iter(qn("w:rFonts"))]
                style_keeps = el.find(qn("w:pPr")) is not None and \
                    el.find(qn("w:pPr")).find(qn("w:keepLines")) is not None
                break
    except Exception:
        pass
    for i, p in enumerate(paras):
        if style_name(p._p) == "SourceCode":
            fonts = [rf.get(qn("w:ascii")) or "" for rf in p._p.iter(qn("w:rFonts"))]
            all_fonts = fonts + style_fonts
            if not any(f and ("Courier" in f or "Mono" in f) for f in all_fonts):
                err(issues, "D10", "листинг не моноширинным шрифтом",
                    where=f"paragraph {i}", severity="warning")
            if not has_ppr_flag(p._p, "w:keepLines") and not style_keeps:
                err(issues, "D10", "листинг без keepLines — может разрываться",
                    where=f"paragraph {i}", severity="warning")
            counts["code_blocks"] = counts.get("code_blocks", 0) + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--docx", default=None)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).resolve()
    docx = Path(args.docx) if args.docx else proj / "out" / "main.docx"
    pdf = Path(args.pdf) if args.pdf else proj / "out" / "main.docx.pdf"
    if not docx.exists():
        print(f"check_docx: нет DOCX: {docx} (сначала `lde docx`)", file=sys.stderr)
        result = {"file": str(docx), "issues": [
            {"id": "MISSING", "severity": "error", "critical": True,
             "message": "DOCX не найден — запустите lde docx", "where": ""}],
            "counts": {}}
        out = Path(args.out) if args.out else proj / "out" / "qa-check.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    doc = Document(str(docx))
    issues: list[dict] = []
    counts: dict = {}
    check_xml(doc, docx, issues, counts)
    if pdf.exists():
        check_render(doc, docx, pdf, issues, counts)
    else:
        issues.append({"id": "RENDER", "severity": "warning", "critical": False,
                       "message": "нет out/main.docx.pdf — запустите `lde docx` "
                                  "(рендер выполняется автоматически)", "where": ""})

    errors = [i for i in issues if i["severity"] == "error"]
    result = {
        "file": str(docx),
        "pdf": str(pdf) if pdf.exists() else None,
        "issues": issues,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "counts": counts,
    }
    out = Path(args.out) if args.out else proj / "out" / "qa-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"check_docx: {docx.name}: ошибок {result['errors']}, "
              f"предупреждений {result['warnings']} -> {out}", file=sys.stderr)
        for it in issues[:20]:
            mark = "ОШИБКА" if it["severity"] == "error" else "warning"
            print(f"  [{mark}] {it['id']}: {it['message']}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
