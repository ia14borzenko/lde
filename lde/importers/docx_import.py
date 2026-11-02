"""lde import-docx: существующий Word-отчёт → проект LDE.

Переносит: заголовки, абзацы, перечисления, рисунки с подписями, таблицы, формулы (OMML → LaTeX
через pandoc; MathType Equation.DSMT4 → LaTeX через MTEF), номера формул «(N)» справа →
нумерованные формулы с метками, текстовые ссылки «рисунке 4», «(5)», «таблице 2» → ссылки,
цитаты «[3]» → [@ключ], список источников → refs.bib (готовые описания), перечень сокращений →
abbreviations.toml, титульный лист → реквизиты.

Исходный DOCX не изменяется. Итог — отчёт import/report.md: что перенесено, что приблизительно,
что потеряно — со ссылками на места.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from lde.importers import mathconv
from lde.importers.mtef import MtefError, latex_from_ole

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def w(tag):
    return f"{{{W}}}{tag}"


def wv(el, attr="val"):
    return el.get(f"{{{W}}}{attr}") if el is not None else None


TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text: str, words=3, maxlen=24) -> str:
    t = "".join(TRANSLIT.get(c, c) for c in text.lower())
    parts = [p for p in re.split(r"[^a-z0-9]+", t) if p and len(p) > 1 or (p and p.isdigit())]
    stop = {"v", "na", "i", "s", "po", "dlya", "ot", "pri", "k", "iz", "the", "of", "a", "and", "in"}
    parts = [p for p in parts if p not in stop] or parts
    s = "-".join(parts[:words])[:maxlen].strip("-")
    return s or "x"


CAPTION_FIG = re.compile(r"^\s*Рис(?:унок|\.)\s*([А-ЯA-Z]?\.?\d+(?:\.\d+)?)\s*[—–\-−]\s*(.*)$", re.S)
CAPTION_TAB = re.compile(r"^\s*Таблица\s*([А-ЯA-Z]?\.?\d+(?:\.\d+)?)\s*[—–\-−]\s*(.*)$", re.S)
NUM_RIGHT = re.compile(r"\(\s*([А-ЯA-Z]?\.?\d+(?:\.\d+)?)\s*\)\s*$")
STRUCT_ABBR = re.compile(r"^(список\s+(использованных\s+)?сокращений|перечень\s+сокращений.*|сокращения)$", re.I)
STRUCT_BIB = re.compile(r"^(список\s+(использованных\s+)?(источников|литературы)|библиографический\s+список|литература)$", re.I)
STRUCT_TOC = re.compile(r"^(содержание|оглавление)$", re.I)
STRUCT_UNNUM = re.compile(r"^(введение|заключение|реферат|аннотация|выводы|термины и определения)$", re.I)


@dataclass
class Seg:
    k: str            # t | m | q | img | raw
    v: str = ""
    sub: bool = False
    sup: bool = False
    bold: bool = False
    ital: bool = False


@dataclass
class Item:
    kind: str                 # para | heading | formula | figure | caption | table | toc | pagebreak | list
    segs: list = field(default_factory=list)
    level: int = 0
    text: str = ""
    latex: str = ""
    number: str = ""
    images: list = field(default_factory=list)   # пути внутри проекта
    rows: list = field(default_factory=list)     # таблица: [[segs]]
    style: str = ""
    src: str = ""             # место в исходнике: «абзац 57»
    label: str = ""
    caption: object = None    # Item подписи
    list_kind: str = ""
    approx: list = field(default_factory=list)
    punct: str = ""
    file: str = ""
    line: int = 0


class Report:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.moved: list[str] = []
        self.approx: list[str] = []
        self.lost: list[str] = []
        self.refs: list[dict] = []

    def inc(self, k, n=1):
        self.counts[k] = self.counts.get(k, 0) + n


class DocxImporter:
    def __init__(self, docx: Path, dst: Path, resolver=None, ref_hint=None, log=print):
        self.docx = Path(docx)
        self.dst = Path(dst)
        self.resolver = resolver          # функция(sdt-тег, sdt) → Seg|Item-подсказка (для import-rde)
        self.ref_hint = ref_hint          # функция(kind, number, context) → label|None (спорные номера)
        self.log = log
        self.rep = Report()
        self.items: list[Item] = []
        self.title_items: list = []
        self.abbr: dict[str, str] = {}
        self.bib: list[str] = []
        self.omml_queue: list = []        # (элемент, setter)
        self.fig_counter = 0
        self.para_no = 0
        self.figures_dir = self.dst / "figures"

    # ------------------------------------------------------------ package ----
    def load(self):
        self.z = zipfile.ZipFile(self.docx)
        self.doc = etree.fromstring(self.z.read("word/document.xml"))
        self.rels = {}
        try:
            rx = etree.fromstring(self.z.read("word/_rels/document.xml.rels"))
            for r in rx:
                self.rels[r.get("Id")] = (r.get("Target"), r.get("TargetMode"))
        except KeyError:
            pass
        self.styles = {}
        try:
            sx = etree.fromstring(self.z.read("word/styles.xml"))
            for s in sx.findall(w("style")):
                sid = s.get(w("styleId"))
                nm = s.find(w("name"))
                ol = s.find(f"{w('pPr')}/{w('outlineLvl')}")
                based = s.find(w("basedOn"))
                self.styles[sid] = {"name": (wv(nm) or "").lower(), "outline": int(wv(ol)) if ol is not None else None,
                                    "based": wv(based)}
        except KeyError:
            pass
        self.numfmt = {}
        try:
            nx = etree.fromstring(self.z.read("word/numbering.xml"))
            absfmt = {}
            for an in nx.findall(w("abstractNum")):
                lv = an.find(w("lvl"))
                fmt = lv.find(w("numFmt")) if lv is not None else None
                absfmt[an.get(w("abstractNumId"))] = wv(fmt) or "bullet"
            for nm in nx.findall(w("num")):
                aid = nm.find(w("abstractNumId"))
                self.numfmt[nm.get(w("numId"))] = absfmt.get(wv(aid), "bullet")
        except KeyError:
            pass

    def style_heading_level(self, sid: str | None) -> int:
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            st = self.styles.get(sid)
            if not st:
                return 0
            m = re.match(r"(heading|заголовок)\s*(\d)", st["name"])
            if m:
                return int(m.group(2))
            if st["outline"] is not None and st["outline"] < 9:
                return st["outline"] + 1
            sid = st["based"]
        return 0

    # ---------------------------------------------------------------- media ----
    def media(self, rid: str) -> str | None:
        """Сохранить картинку по r:id в figures/, вернуть путь в проекте (PNG/JPG)."""
        tgt = self.rels.get(rid)
        if not tgt or tgt[1] == "External":
            self.rep.lost.append(f"внешняя картинка по ссылке {tgt[0] if tgt else rid} — не скачивается")
            return None
        name = "word/" + tgt[0].lstrip("/") if not tgt[0].startswith("word/") else tgt[0]
        name = re.sub(r"word/\.\./", "", name)
        try:
            data = self.z.read(name)
        except KeyError:
            self.rep.lost.append(f"картинка {name} отсутствует в пакете")
            return None
        self.fig_counter += 1
        ext = Path(name).suffix.lower()
        self.figures_dir.mkdir(parents=True, exist_ok=True)
        base = f"img{self.fig_counter:03d}"
        if ext in (".png", ".jpg", ".jpeg"):
            out = self.figures_dir / f"{base}{ext}"
            out.write_bytes(data)
            return str(out.relative_to(self.dst))
        out = self.figures_dir / f"{base}.png"
        try:
            if ext in (".tif", ".tiff", ".gif", ".bmp"):
                from PIL import Image
                im = Image.open(io.BytesIO(data))
                im.save(out)
                return str(out.relative_to(self.dst))
            if ext in (".emf", ".wmf", ".svg"):
                with tempfile.TemporaryDirectory() as td:
                    src = Path(td) / f"{base}{ext}"
                    src.write_bytes(data)
                    subprocess.run(["soffice", "--headless", "--convert-to", "png", "--outdir", td, str(src)],
                                   capture_output=True, timeout=120)
                    res = Path(td) / f"{base}.png"
                    if res.exists():
                        shutil.copy2(res, out)
                        self.rep.approx.append(f"рисунок {name} ({ext}) переведён в PNG через LibreOffice")
                        return str(out.relative_to(self.dst))
        except Exception as e:  # noqa: BLE001
            self.rep.lost.append(f"не удалось перевести картинку {name}: {e}")
            return None
        self.rep.lost.append(f"картинка {name}: формат {ext} не поддержан")
        return None

    # --------------------------------------------------------------- inline ----
    def runs(self, el, segs: list, ctx: dict):
        """Обойти inline-содержимое абзаца, наполнив segs."""
        for ch in el:
            tag = etree.QName(ch)
            ln = tag.localname
            if tag.namespace == M and ln in ("oMath", "oMathPara"):
                seg = Seg("m", "")
                seg.display = (ln == "oMathPara")
                segs.append(seg)
                self.omml_queue.append((ch, seg))
                continue
            if tag.namespace != W:
                if ln in ("AlternateContent",):
                    fb = ch.find("{http://schemas.openxmlformats.org/markup-compatibility/2006}Choice")
                    if fb is not None:
                        self.runs(fb, segs, ctx)
                continue
            if ln == "r":
                self.one_run(ch, segs, ctx)
            elif ln in ("hyperlink", "smartTag", "ins", "fldSimple", "customXml", "bdo", "dir"):
                self.runs(ch, segs, ctx)
            elif ln == "sdt":
                tagv = wv(ch.find(f"{w('sdtPr')}/{w('tag')}"))
                content = ch.find(w("sdtContent"))
                handled = False
                if self.resolver and tagv:
                    res = self.resolver(tagv, ch)
                    if res is not None:
                        segs.extend(res if isinstance(res, list) else [res])
                        handled = True
                if not handled and content is not None:
                    self.runs(content, segs, ctx)
            elif ln == "del":
                continue

    def one_run(self, r, segs, ctx):
        rpr = r.find(w("rPr"))
        va = wv(rpr.find(w("vertAlign"))) if rpr is not None else None
        bold = rpr is not None and rpr.find(w("b")) is not None and wv(rpr.find(w("b"))) not in ("0", "false")
        ital = rpr is not None and rpr.find(w("i")) is not None and wv(rpr.find(w("i"))) not in ("0", "false")
        for ch in r:
            ln = etree.QName(ch).localname
            if ln == "fldChar":
                t = wv(ch, "fldCharType")
                if t == "begin":
                    ctx["fld"] = ctx.get("fld", 0) + 1
                    ctx.setdefault("fld_sep", []).append(False)
                elif t == "separate" and ctx.get("fld_sep"):
                    ctx["fld_sep"][-1] = True
                elif t == "end" and ctx.get("fld"):
                    ctx["fld"] -= 1
                    ctx["fld_sep"].pop()
                continue
            if ln == "instrText":
                ctx["instr"] = ctx.get("instr", "") + (ch.text or "")
                continue
            in_code = ctx.get("fld") and ctx.get("fld_sep") and not ctx["fld_sep"][-1]
            if in_code:
                continue
            if ln == "t":
                segs.append(Seg("t", ch.text or "", sub=va == "subscript", sup=va == "superscript", bold=bold, ital=ital))
            elif ln == "tab":
                segs.append(Seg("t", "\t"))
            elif ln in ("br", "cr"):
                if wv(ch, "type") == "page":
                    ctx["pagebreak"] = True
                else:
                    segs.append(Seg("t", " "))
            elif ln == "noBreakHyphen":
                segs.append(Seg("t", "‑"))
            elif ln == "sym":
                segs.append(Seg("t", symbol_char(wv(ch, "font") or "", wv(ch, "char") or "20"), sub=va == "subscript",
                                sup=va == "superscript"))
            elif ln == "drawing":
                for bl in ch.iter(f"{{{A}}}blip"):
                    p = self.media(bl.get(f"{{{R}}}embed"))
                    if p:
                        segs.append(Seg("img", p))
                    break
                else:
                    self.rep.lost.append(f"{ctx.get('where')}: объект без картинки (диаграмма/надпись) не перенесён")
            elif ln == "object":
                ole = ch.find(f".//{{{O}}}OLEObject")
                prog = ole.get("ProgID") if ole is not None else ""
                if prog and prog.startswith("Equation"):
                    rid = ole.get(f"{{{R}}}id")
                    tgt = self.rels.get(rid, ("", ""))[0]
                    try:
                        data = self.z.read("word/" + tgt.lstrip("/"))
                        latex = latex_from_ole(data)
                        seg = Seg("m", mathconv.normalize_latex(latex))
                        seg.display = False
                        seg.mathtype = True
                        segs.append(seg)
                        self.rep.inc("MathType → LaTeX")
                    except (MtefError, KeyError) as e:
                        im = ch.find(f".//{{{V}}}imagedata")
                        p = self.media(im.get(f"{{{R}}}id")) if im is not None else None
                        if p:
                            segs.append(Seg("img", p))
                        self.rep.approx.append(f"{ctx.get('where')}: формула MathType не разобрана ({e}) — оставлена картинкой")
                else:
                    im = ch.find(f".//{{{V}}}imagedata")
                    p = self.media(im.get(f"{{{R}}}id")) if im is not None else None
                    if p:
                        segs.append(Seg("img", p))
                        self.rep.approx.append(f"{ctx.get('where')}: OLE-объект «{prog}» перенесён картинкой")
                    else:
                        self.rep.lost.append(f"{ctx.get('where')}: OLE-объект «{prog}» не перенесён")
            elif ln == "pict":
                im = ch.find(f".//{{{V}}}imagedata")
                if im is not None:
                    p = self.media(im.get(f"{{{R}}}id"))
                    if p:
                        segs.append(Seg("img", p))
                elif ch.find(f".//{{{V}}}textbox") is not None:
                    self.rep.lost.append(f"{ctx.get('where')}: надпись (текстовое поле) не перенесена")
            elif ln == "footnoteReference":
                self.rep.lost.append(f"{ctx.get('where')}: сноска не перенесена (сноски в LDE пока не поддержаны)")

    # ---------------------------------------------------------------- blocks ----
    def walk(self, container, in_table=False):
        for ch in container:
            ln = etree.QName(ch).localname
            if ln == "p":
                self.paragraph(ch)
            elif ln == "tbl":
                self.table(ch)
            elif ln == "sdt":
                content = ch.find(w("sdtContent"))
                gal = ch.find(f".//{w('docPartGallery')}")
                instr = "".join(x.text or "" for x in ch.iter(w("instrText")))
                if (gal is not None and "Contents" in (wv(gal) or "")) or " TOC " in f" {instr} ":
                    self.items.append(Item("toc", src="СОДЕРЖАНИЕ (поле Word)"))
                    self.rep.moved.append("СОДЕРЖАНИЕ: поле Word → генерируется LDE с номерами страниц")
                    continue
                tagv = wv(ch.find(f"{w('sdtPr')}/{w('tag')}"))
                if self.resolver and tagv and content is not None and content.find(w("p")) is not None:
                    self._block_tag = tagv
                    self._block_sdt = ch
                    self.walk(content)
                    self._block_tag = None
                    self._block_sdt = None
                elif content is not None:
                    self.walk(content)

    def paragraph(self, p):
        self.para_no += 1
        where = f"абзац {self.para_no}"
        ppr = p.find(w("pPr"))
        sid = wv(ppr.find(w("pStyle"))) if ppr is not None else None
        level = self.style_heading_level(sid)
        if ppr is not None and ppr.find(w("outlineLvl")) is not None and not level:
            lv = int(wv(ppr.find(w("outlineLvl"))))
            if lv < 9:
                level = lv + 1
        segs: list[Seg] = []
        ctx = {"where": where}
        blk_tag = getattr(self, "_block_tag", None)
        if blk_tag and self.resolver:
            res = self.resolver(blk_tag, getattr(self, "_block_sdt", None), block=True, paragraph=p)
            if res is not None:
                self.runs(p, [], ctx)  # учесть поля и т.п.
                segs = res if isinstance(res, list) else [res]
            else:
                self.runs(p, segs, ctx)
        else:
            self.runs(p, segs, ctx)
        if ctx.get("pagebreak") and not any(s.k != "t" or s.v.strip() for s in segs):
            self.items.append(Item("pagebreak", src=where))
            return
        text = "".join(s.v for s in segs if s.k == "t")
        it = Item("para", segs=segs, style=(self.styles.get(sid, {}).get("name", "") if sid else ""), src=where)
        stripped = text.strip()
        nonws = [s for s in segs if not (s.k == "t" and not s.v.strip())]
        if level and stripped:
            it.kind, it.level, it.text = "heading", level, stripped
            self.items.append(it)
            return
        if (sid and "toc heading" in self.styles.get(sid, {}).get("name", "")) or STRUCT_TOC.match(stripped):
            self.items.append(Item("toc", src=where))
            return
        if nonws and all(s.k == "img" for s in nonws):
            it.kind = "figure"
            it.images = [s.v for s in nonws]
            self.items.append(it)
            return
        # выносная формула: одна формула + (знак препинания) + (N)
        maths = [s for s in nonws if s.k == "m"]
        texts = "".join(s.v for s in nonws if s.k == "t")
        instr = ctx.get("instr", "")
        num = ""
        mnum = NUM_RIGHT.search(texts)
        rest = texts
        if mnum:
            num = mnum.group(1)
            rest = texts[:mnum.start()]
        elif "MTEqn" in instr or "SEQ" in instr:
            mi = re.search(r"\((?:[^()]*?)(\d+(?:\.\d+)?)\s*\)\s*$", instr.strip())
            if mi:
                num = mi.group(1)
        if len(maths) == 1 and re.fullmatch(r"[\s.,;:]*", rest) and \
                (getattr(maths[0], "display", False) or num or "display" in (it.style or "") or len(nonws) == 1
                 or "mtdisplayequation" in (it.style or "").replace(" ", "")):
            it.kind = "formula"
            it.segs = maths
            it.number = num
            it.punct = rest.strip()
            self.items.append(it)
            return
        if not stripped and not nonws:
            return
        # заголовки, оформленные не стилем, а текстом: «1 Введение» полужирным — не трогаем (это абзац)
        it.text = stripped
        numpr = ppr.find(w("numPr")) if ppr is not None else None
        if numpr is not None:
            nid = wv(numpr.find(w("numId")))
            ilvl = int(wv(numpr.find(w("ilvl"))) or 0)
            fmt = self.numfmt.get(nid, "bullet")
            it.kind = "list"
            it.list_kind = "-" if fmt == "bullet" else ("а)" if "russian" in fmt.lower() else "1)")
            it.level = ilvl + 1
        self.items.append(it)

    def table(self, tbl):
        rows = []
        merged = False
        for tr in tbl.findall(w("tr")):
            row = []
            for tc in tr.findall(w("tc")):
                tcpr = tc.find(w("tcPr"))
                if tcpr is not None and (tcpr.find(w("gridSpan")) is not None or tcpr.find(w("vMerge")) is not None):
                    merged = True
                segs = []
                for p in tc.iter(w("p")):
                    if segs:
                        segs.append(Seg("t", " "))
                    self.runs(p, segs, {"where": "таблица"})
                row.append(segs)
            rows.append(row)
        it = Item("table", rows=rows, src=f"таблица после абзаца {self.para_no}")
        if merged:
            it.approx.append("объединённые ячейки разъединены")
        self.items.append(it)

    # ----------------------------------------------------------- post-pass ----
    def convert_omml(self):
        if not self.omml_queue:
            return
        res = mathconv.omml_to_latex(self.docx, [e for e, _ in self.omml_queue])
        for (el, seg), tex in zip(self.omml_queue, res):
            if tex is None:
                seg.k = "t"
                seg.v = "".join(el.itertext())
                self.rep.approx.append(f"формула OMML «{seg.v[:40]}» не переведена в LaTeX — оставлена текстом")
            else:
                seg.v = mathconv.normalize_latex(tex)
                self.rep.inc("формулы OMML → LaTeX")

    def structure(self):
        """Титул, СОДЕРЖАНИЕ, сокращения, источники, подписи → объекты."""
        items = self.items
        # титул: всё до первого заголовка/содержания
        first = next((i for i, it in enumerate(items) if it.kind in ("heading", "toc")), 0)
        self.title_items = items[:first]
        items = items[first:]
        out: list[Item] = []
        mode = None
        for it in items:
            if it.kind == "heading":
                t = re.sub(r"^\s*\d+(\.\d+)*\.?\s+", "", it.text).strip()
                if STRUCT_ABBR.match(t):
                    mode = "abbr"
                    self.abbr_heading = t
                    continue
                if STRUCT_BIB.match(t):
                    mode = "bib"
                    self.bib_heading = t
                    continue
                if STRUCT_TOC.match(t):
                    mode = "skip"
                    out.append(Item("toc"))
                    continue
                mode = None
            if mode == "abbr" and it.kind in ("para", "list"):
                txt = self.plain(it.segs).strip()
                m = re.match(r"^(.+?)\s+[–—\-−]\s+(.+)$", txt)
                if m:
                    self.abbr[m.group(1).strip()] = m.group(2).strip()
                elif txt:
                    self.rep.approx.append(f"строка перечня сокращений не распознана: «{txt[:60]}»")
                continue
            if mode == "bib" and it.kind in ("para", "list"):
                txt = re.sub(r"\s+", " ", self.plain(it.segs)).strip()
                txt = re.sub(r"^\[?\d+\]?[.)]?\s+", "", txt)
                if txt:
                    self.bib.append(txt)
                continue
            if mode == "skip" and it.kind != "heading":
                continue
            if it.kind == "pagebreak":
                self.rep.inc("разрывы страниц (вёрстку делает LDE)")
                continue
            out.append(it)
        # подписи рисунков и таблиц
        res: list[Item] = []
        for i, it in enumerate(out):
            txt = self.plain(it.segs).strip() if it.kind == "para" else ""
            mf = CAPTION_FIG.match(txt) if txt else None
            mt = CAPTION_TAB.match(txt) if txt else None
            if mf:
                fig = next((x for x in reversed(res[-2:]) if x.kind == "figure" and x.caption is None), None)
                if fig is not None:
                    fig.caption = it
                    fig.number = mf.group(1)
                    it.text = mf.group(2).strip()
                    self._strip_caption_prefix(it, CAPTION_FIG)
                    continue
                self.rep.approx.append(f"{it.src}: подпись «{txt[:50]}» без рисунка над ней — оставлена абзацем")
            if mt:
                nxt = out[i + 1] if i + 1 < len(out) else None
                if nxt is not None and nxt.kind == "table":
                    nxt.caption = it
                    nxt.number = mt.group(1)
                    self._strip_caption_prefix(it, CAPTION_TAB)
                    continue
            res.append(it)
        for it in res:
            if it.kind == "figure" and it.caption is None:
                self.rep.approx.append(f"{it.src}: рисунок без подписи «Рисунок N — …» — подпись нужно дописать")
        self.items = res

    def _strip_caption_prefix(self, it: Item, rx):
        """Удалить «Рисунок N — » из сегментов подписи."""
        full = ""
        for s in it.segs:
            if s.k != "t":
                break
            full += s.v
        m = rx.match(full) if full else None
        cut = len(full) - len(m.group(2)) if m else 0
        new = []
        for s in it.segs:
            if cut > 0 and s.k == "t":
                if len(s.v) <= cut:
                    cut -= len(s.v)
                    continue
                s.v = s.v[cut:]
                cut = 0
            new.append(s)
        it.segs = new

    @staticmethod
    def plain(segs) -> str:
        return "".join(s.v if s.k == "t" else " " for s in segs)

    # ----------------------------------------------------------------- labels ----
    def assign_labels(self):
        used = set()

        def uniq(base):
            lab = base
            k = 2
            while lab in used:
                lab = f"{base}-{k}"
                k += 1
            used.add(lab)
            return lab

        self.fig_by_num, self.tab_by_num, self.eq_by_num = {}, {}, {}
        for it in self.items:
            if it.kind == "figure":
                base = getattr(it, "label_hint", "") or slug(self.plain(it.caption.segs) if it.caption else "ris")
                it.label = uniq("fig:" + base)
                if it.number:
                    self.fig_by_num.setdefault(it.number, it)
            elif it.kind == "table":
                base = slug(self.plain(it.caption.segs)) if it.caption else "tab"
                it.label = uniq("tab:" + base)
                if it.number:
                    self.tab_by_num.setdefault(it.number, it)
            elif it.kind == "formula" and it.number:
                it.label = uniq("eq:" + eq_slug(it.segs[0].v, it.number))
                self.eq_by_num.setdefault(it.number, it)
            elif it.kind == "heading":
                t = re.sub(r"^\s*\d+(\.\d+)*\.?\s+", "", it.text).strip()
                if not STRUCT_UNNUM.match(t):
                    it.label = uniq("sec:" + slug(t, 2, 18))
        self.bib_keys = []
        for i, txt in enumerate(self.bib, 1):
            self.bib_keys.append(bib_key(txt, i, set(self.bib_keys)))

    # ------------------------------------------------------------ rendering ----
    def esc(self, s: str) -> str:
        s = s.replace("\\", "\\\\")
        for ch in "{}$*":
            s = s.replace(ch, "\\" + ch)
        s = s.replace("[@", "\\[@")
        s = re.sub(r"(?<![\w@])@", r"\\@", s)
        return s

    def render_segs(self, segs, item: Item) -> str:
        """Сегменты → LDE-текст: индексы → $…$, ссылки/цитаты текстом → @метки/[@ключи]."""
        # 1) склеить текст, превращая индексы в формулы
        out: list[tuple[str, str]] = []   # (kind, text) kind: t — сырой текст, x — готовая разметка
        merged: list[Seg] = []
        for s in segs:   # соседние индексы одного вида — один индекс («max» + «21» → «max21»)
            if merged and s.k == "t" and merged[-1].k == "t" and (s.sub or s.sup) \
                    and (s.sub, s.sup) == (merged[-1].sub, merged[-1].sup):
                merged[-1] = Seg("t", merged[-1].v + s.v, s.sub, s.sup, s.bold, s.ital)
            else:
                merged.append(s)
        segs = merged
        for s in segs:
            if s.k == "t" and (s.sub or s.sup) and s.v.strip():
                prev = out[-1] if out and out[-1][0] == "t" else None
                v = s.v.strip()
                base = ""
                if prev:
                    m = re.search(r"([A-Za-z\u0370-\u03ff\u0394′']{1,3}|[A-Za-z]{1,3}\d?)$", prev[1])
                    if m:
                        base = m.group(1)
                        out[-1] = ("t", prev[1][:m.start()])
                if not base and prev and re.search(r"[А-Яа-яЁё]$", prev[1]) and s.sup and re.fullmatch(r"[\-−]?\d+", v):
                    sup = v.replace("−", "-").translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
                    out.append(("t", sup))
                    continue
                if not base and prev and s.sub and re.fullmatch(r"\d+", v) and re.search(r"[A-Za-zА-Яа-я]$", prev[1]):
                    out.append(("t", v.translate(str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉"))))
                    continue
                tex = mathconv.normalize_latex(base) if base else "{}"
                tex += ("_{" if s.sub else "^{") + v + "}"
                if out and out[-1][0] == "m" and not base:
                    k, t = out.pop()
                    out.append(("m", t + tex[2:] if tex.startswith("{}") else t + tex))
                else:
                    out.append(("m", tex))
                self.rep.inc("индексы в тексте → формулы")
                continue
            if s.k == "t":
                txt = s.v.replace("\t", " ")
                if out and out[-1][0] == "t":
                    out[-1] = ("t", out[-1][1] + txt)
                else:
                    out.append(("t", txt))
            elif s.k == "m":
                out.append(("m", s.v))
            elif s.k == "q":
                out.append(("x", "{" + s.v + "}"))
            elif s.k == "raw":
                out.append(("x", s.v))
            elif s.k == "img":
                self.rep.approx.append(f"{item.src}: картинка внутри текста абзаца — вынесена в отдельный рисунок без подписи")
                self.pending_inline_figs.append(s.v)
        res = []
        for k, t in out:
            if k == "t":
                res.append(self.convert_refs(t, item))
            elif k == "m":
                t = t.strip()
                if t:
                    res.append("$" + t.replace("$", "\\$") + "$")
            else:
                res.append(t)
        txt = "".join(res)
        txt = re.sub(r"[ \u00a0]{2,}", " ", txt)
        return txt.strip()

    def convert_refs(self, t: str, item: Item) -> str:
        """В обычном тексте: «рисунке 4» → «рисунке @fig:x», «(5)» → @eq:x, «[3, 4]» → [@a; @b]."""
        pieces = []
        pos = 0
        pat = re.compile(
            r"(?P<fig>(?<![\w])(?:рис(?:ун\w*|\.))\s*)(?P<fn>\d+(?:\.\d+)?)"
            r"|(?P<tab>(?<![\w])(?:табл(?:иц\w*|\.))\s*)(?P<tn>\d+(?:\.\d+)?)"
            r"|(?P<eq>\((?P<en>\d+(?:\.\d+)?)\))"
            r"|(?P<cite>\[(?P<cn>\d+(?:\s*[,–\-]\s*\d+)*)(?:,\s*(?P<loc>с\.\s*[\d–\-]+))?\])",
            re.I)
        for m in pat.finditer(t):
            rep = None
            # контекст — ближайшие слова вокруг номера (для спорной нумерации)
            ctx = t[max(0, m.start() - 35):m.end() + 35]
            if m.group("fn"):
                lab = self.resolve_ref("fig", m.group("fn"), ctx, item)
                if lab:
                    rep = m.group("fig") + "@" + lab
                    # «рисунки 11 и 12», «рисунках 3–5»
            elif m.group("tn"):
                lab = self.resolve_ref("tab", m.group("tn"), ctx, item)
                if lab:
                    rep = m.group("tab") + "@" + lab
            elif m.group("en"):
                lab = self.resolve_ref("eq", m.group("en"), ctx, item)
                if lab:
                    rep = "@" + lab
            elif m.group("cn"):
                nums = []
                for part in re.split(r"\s*,\s*", m.group("cn")):
                    if re.search(r"[–\-]", part):
                        a, b = re.split(r"\s*[–\-]\s*", part)
                        nums += list(range(int(a), int(b) + 1))
                    else:
                        nums.append(int(part))
                if nums and all(1 <= n <= len(self.bib_keys) for n in nums):
                    loc = m.group("loc")
                    keys = [self.bib_keys[n - 1] for n in nums]
                    if loc:
                        rep = "[" + "; ".join("@" + k for k in keys[:-1]) + ("; " if len(keys) > 1 else "") + f"@{keys[-1]}, {loc}]"
                    else:
                        rep = "[" + "; ".join("@" + k for k in keys) + "]"
                    self.rep.inc("цитаты [N] → [@ключ]")
            if rep is None:
                continue
            pieces.append(self.esc(t[pos:m.start()]))
            pieces.append(rep)
            pos = m.end()
        pieces.append(self.esc(t[pos:]))
        res = "".join(pieces)
        # «рисунки @fig:a и 12» — вторые номера в перечне
        def more(mm):
            kind = mm.group(1)
            lab = self.resolve_ref(kind, mm.group(3), t, item)
            return f"{mm.group(0)[:-len(mm.group(3))]}@{lab}" if lab else mm.group(0)
        res = re.sub(r"@(fig|tab):[\w\-]++(\s*,\s*|\s+и\s+|\s*[–—]\s*)(\d+)\b(?!\s*[,)]\s*[а-я]\b)",
                     lambda mm: more(mm), res)
        return res

    def resolve_ref(self, kind, num, ctx, item) -> str | None:
        table = {"fig": self.fig_by_num, "tab": self.tab_by_num, "eq": self.eq_by_num}[kind]
        if self.ref_hint:
            lab = self.ref_hint(kind, num, ctx, self)
            if lab:
                return lab
        tgt = table.get(num)
        if not tgt:
            if kind != "eq":
                self.rep.approx.append(f"{item.src}: «{kind} {num}» — объекта с таким номером нет, ссылка оставлена текстом")
            return None
        self.rep.refs.append({"kind": kind, "number": num, "label": tgt.label, "where": item.src})
        return tgt.label

    # ---------------------------------------------------------------- emit ----
    def emit(self):
        files: list[tuple[str, list[str]]] = []
        cur: list[str] = []
        name = "00-nachalo"
        idx = 0
        self.pending_inline_figs = []
        prev_kind = None
        list_buf: list[str] = []

        def flush_list():
            nonlocal list_buf
            if list_buf:
                cur.append("\n".join(list_buf))
                list_buf = []

        for it in self.items:
            if it.kind == "toc":
                continue
            if it.kind != "list":
                flush_list()
            if it.kind == "heading" and it.level == 1:
                if cur:
                    files.append((name, cur))
                idx += 1
                t = re.sub(r"^\s*\d+(\.\d+)*\.?\s+", "", it.text).strip()
                name = f"{idx:02d}-{slug(t, 2, 20)}"
                cur = []
            if it.kind == "heading":
                t = re.sub(r"^\s*\d+(\.\d+)*\.?\s+", "", it.text).strip()
                attrs = "{-}" if STRUCT_UNNUM.match(t) else (f"{{#{it.label}}}" if it.label else "")
                tt = self.esc(t)
                cur.append("#" * min(it.level, 3) + " " + tt + (" " + attrs if attrs else ""))
                self.rep.inc("заголовки")
            elif it.kind == "para":
                txt = self.render_segs(it.segs, it)
                if txt:
                    m = re.match(r"^([-–—•])\s+(.*)$", txt)
                    if m:
                        list_buf.append("- " + m.group(2))
                        self.rep.inc("пункты перечислений")
                        continue
                    flush_list()
                    cur.append(txt)
                    self.rep.inc("абзацы")
                for pth in self.pending_inline_figs:
                    cur.append(f"![]({pth})")
                self.pending_inline_figs = []
            elif it.kind == "list":
                txt = self.render_segs(it.segs, it)
                ind = "  " * (it.level - 1)
                list_buf.append(f"{ind}{'-' if it.list_kind == '-' else '1.'} {txt}")
                self.rep.inc("пункты перечислений")
            elif it.kind == "formula":
                tex = it.segs[0].v.strip()
                if it.punct:
                    tex += it.punct
                attrs = f" {{#{it.label}}}" if it.label else ""
                cur.append(f"$$ {tex} $$" + attrs)
                self.rep.inc("формулы выносные" + (" нумерованные" if it.number else ""))
            elif it.kind == "figure":
                cap = self.render_segs(it.caption.segs, it) if it.caption else ""
                attrs = f"{{#{it.label}}}"
                if len(it.images) == 1:
                    cur.append(f"![{cap}]({it.images[0]}){attrs}")
                else:
                    lines = [f"::: figure {attrs}"] + [f"![]({p})" for p in it.images] + [cap, ":::"]
                    cur.append("\n".join(lines))
                self.rep.inc("рисунки")
            elif it.kind == "table":
                if not it.rows:
                    continue
                ncol = max(len(r) for r in it.rows)
                cap = self.render_segs(it.caption.segs, it) if it.caption else "Таблица"
                lines = [f": {cap} {{#{it.label}}}"]
                for ri, r in enumerate(it.rows):
                    cells = [self.render_segs(c, it).replace("|", "\\|") for c in r] + [""] * (ncol - len(r))
                    lines.append("| " + " | ".join(cells) + " |")
                    if ri == 0:
                        lines.append("|" + "---|" * ncol)
                cur.append("\n".join(lines))
                for a in it.approx:
                    self.rep.approx.append(f"{it.src}: {a}")
                self.rep.inc("таблицы")
            prev_kind = it.kind
        flush_list()
        if cur:
            files.append((name, cur))
        td = self.dst / "text"
        td.mkdir(parents=True, exist_ok=True)
        written = []
        for name, blocks in files:
            p = td / f"{name}.md"
            p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
            written.append(str(p.relative_to(self.dst)))
        return written

    # ----------------------------------------------------------------- run ----
    def run(self, profile=None, title_fields=None, extra_config=""):
        self.load()
        self.dst.mkdir(parents=True, exist_ok=True)
        body = self.doc.find(w("body"))
        self.walk(body)
        self.convert_omml()
        self.structure()
        self.assign_labels()
        files = self.emit()
        self.write_meta(files, profile, title_fields, extra_config)
        return self.rep

    def title_lines(self) -> list[dict]:
        lines = []
        for it in self.title_items:
            if it.kind == "table":
                for r in it.rows:
                    cells = [self.plain(c).strip() for c in r]
                    txt = "   ".join(c for c in cells if c)
                    if txt:
                        lines.append({"text": txt, "align": "center"})
            elif it.kind in ("para", "heading", "list"):
                txt = re.sub(r"\s+", " ", self.plain(it.segs)).strip()
                if txt:
                    lines.append({"text": txt, "align": "center"})
        return lines

    def write_meta(self, files, profile, title_fields, extra_config):
        d = self.dst
        from lde.profiles import detect_profile
        prof = profile or detect_profile(self.title_lines())
        tf = dict(title_fields or {})
        if not tf:
            from lde.profiles import get_profile
            tf = get_profile(prof).fields_from_lines(self.title_lines())
        cfg = ['# Настройки документа (импорт из ' + self.docx.name + '). Правятся в Studio (шестерёнка) или здесь.', "",
               "[document]", 'name = "report"', f'profile = "{prof}"', 'numbering = "continuous"',
               "toc = true", f"abbreviations = {'true' if self.abbr else 'false'}", "symbols = false",
               f"bibliography = {'true' if self.bib else 'false'}", "", "[title]"]
        for k, v in tf.items():
            if isinstance(v, list):
                continue
            cfg.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
        if "lines" in tf:
            for ln in tf["lines"]:
                cfg += ["", "[[title.lines]]"] + [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in ln.items()]
        if extra_config:
            cfg.append(extra_config)
        (d / "lde.toml").write_text("\n".join(cfg) + "\n", encoding="utf-8")
        if self.abbr:
            (d / "abbreviations.toml").write_text(
                "# Сокращения (перенесены из перечня сокращений документа)\n" +
                "".join(f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}\n" for k, v in self.abbr.items()),
                encoding="utf-8")
        if not (d / "quantities.toml").exists():
            (d / "quantities.toml").write_text("# Величины (обозначения): [\"R_ц\"] meaning = \"…\" unit = \"м\"\n",
                                               encoding="utf-8")
        bib = ["% Источники (перенесены из списка документа как готовые описания — поле formatted).",
               "% Ключи можно переименовать; в тексте ссылка — [@ключ].", ""]
        for key, txt in zip(self.bib_keys, self.bib):
            bib.append(f"@misc{{{key},\n  formatted = {{{bib_escape(txt)}}},\n}}\n")
        (d / "refs.bib").write_text("\n".join(bib), encoding="utf-8")
        for sub in ("calc", "data", "sources"):
            (d / sub).mkdir(exist_ok=True)
        imp = d / "import"
        imp.mkdir(exist_ok=True)
        shutil.copy2(self.docx, imp / ("original" + self.docx.suffix))
        self.rep.counts["сокращения"] = len(self.abbr)
        self.rep.counts["источники"] = len(self.bib)
        self.write_report(files)

    def write_report(self, files):
        r = self.rep
        imp = self.dst / "import"
        lines = [f"# Отчёт импорта: {self.docx.name}", "",
                 "Исходный файл не изменён; его копия — `import/original.docx`.", "", "## Перенесено", ""]
        for k, v in r.counts.items():
            lines.append(f"- {k}: {v}")
        lines += ["", "Файлы разделов: " + ", ".join(f"`{f}`" for f in files), ""]
        for m in r.moved:
            lines.append(f"- {m}")
        lines += ["", f"## Ссылки, превращённые в @метки ({len(r.refs)})", ""]
        for x in r.refs[:400]:
            lines.append(f"- {x['where']}: {dict(fig='рисунок', tab='таблица', eq='формула')[x['kind']]} {x['number']} → @{x['label']}"
                         + (f" — {x['note']}" if x.get("note") else ""))
        lines += ["", f"## Приблизительно ({len(r.approx)})", ""] + [f"- {x}" for x in r.approx]
        lines += ["", f"## Не перенесено ({len(r.lost)})", ""] + ([f"- {x}" for x in r.lost] or ["- ничего"])
        (imp / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (imp / "report.json").write_text(json.dumps(r.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")


SYMBOL_FONT = dict(zip(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "αβχδεφγηιϕκλμνοπθρστυϖωξψζΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"))
SYMBOL_EXTRA = {0xB4: "×", 0xB1: "±", 0xA3: "≤", 0xB3: "≥", 0xB9: "≠", 0xBB: "≈", 0xA5: "∞", 0xB6: "∂",
                0xD6: "√", 0xE5: "∑", 0xF2: "∫", 0xAE: "→", 0xAC: "←", 0xD7: "·", 0x2D: "−", 0xA2: "′",
                0xB2: "″", 0xBA: "≡", 0xB5: "∝", 0xC4: "⊗", 0xC5: "⊕", 0xD1: "∇", 0xB0: "°", 0xBC: "…"}


def symbol_char(font: str, code: str) -> str:
    """w:sym (символ шрифта Symbol/Wingdings) → юникод."""
    try:
        c = int(code, 16)
    except ValueError:
        return ""
    if c >= 0xF000:
        c -= 0xF000
    if "symbol" in font.lower():
        ch = chr(c)
        if ch in SYMBOL_FONT:
            return SYMBOL_FONT[ch]
        return SYMBOL_EXTRA.get(c, chr(c) if 0x20 <= c < 0x7F else "")
    return chr(c) if c >= 0x20 else ""


def bib_escape(s: str) -> str:
    depth = 0
    out = []
    for c in s:
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                continue
            depth -= 1
        out.append(c)
    return "".join(out) + "}" * depth


def bib_key(txt: str, i: int, used: set) -> str:
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", txt)
    year = m.group(1) if m else ""
    first = re.search(r"([A-Za-zА-Яа-яЁё\-]{3,})", txt)
    word = slug(first.group(1), 1, 14) if first else "src"
    key = f"{word}{year}" or f"src{i}"
    base, k = key, 2
    while key in used:
        key = f"{base}{chr(ord('a') + k - 2)}"
        k += 1
    return key


def eq_slug(tex: str, num: str) -> str:
    lhs = re.split(r"=|\\approx|\\le|\\ge|<|>", tex, maxsplit=1)[0]
    lhs = re.sub(r"\\(left|right|begin\{\w+\}|end\{\w+\}|mathrm|text|operatorname|mathcal)", "", lhs)
    lhs = re.sub(r"\\([A-Za-z]+)", r"\1", lhs)
    t = "".join(TRANSLIT.get(c, c) for c in lhs.lower())
    t = re.sub(r"[^a-z0-9]", "", t)[:10]
    return t if t and len(t) >= 1 and not t.isdigit() else f"f{num}"


def import_docx(src: Path, dst: Path, profile=None, log=print, **kw) -> Report:
    imp = DocxImporter(src, dst, log=log, **kw)
    return imp.run(profile=profile)
