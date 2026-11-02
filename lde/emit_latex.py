"""Модель документа → LaTeX для XeLaTeX (PDF — канонический результат).

Номера, тексты ссылок и цитат подставляются здесь же из Project.resolve(): LaTeX не считает
ничего, кроме страниц. Это гарантирует совпадение с DOCX (тот же код нумерации).
"""
from __future__ import annotations

import re
from pathlib import Path

from lde import bib as bibmod
from lde.fmt import FormatError, format_value, latex_escape
from lde.profiles import get_profile
from lde.project import Project
from lde.textfmt import Block, Node

TEXT_CMDS = {"text", "textrm", "mbox", "textit", "textbf", "textnormal", "hbox", "label", "tag"}


def strip_unit_matrix(tex: str) -> str:
    """`\\begin{matrix}X\\end{matrix}` из одной ячейки (так RDE/импорт оборачивали содержимое скобок)
    → `X`: матрица набирает содержимое уменьшенным стилем и раздувает скобки. Текст формулы в
    исходнике не меняется — только при выводе."""
    b, e = "\\begin{matrix}", "\\end{matrix}"
    out, i = [], 0
    while True:
        j = tex.find(b, i)
        if j < 0:
            out.append(tex[i:])
            return "".join(out)
        # парный \end{matrix}, содержимое без & и \\ на своём уровне
        k, depth, brace, single = j + len(b), 1, 0, True
        while k < len(tex):
            if tex.startswith(b, k):
                depth += 1
                k += len(b)
                continue
            if tex.startswith(e, k):
                depth -= 1
                if depth == 0:
                    break
                k += len(e)
                continue
            c = tex[k]
            if c == "\\" and k + 1 < len(tex) and tex[k + 1] == "\\" and depth == 1 and brace == 0:
                single = False
            if c == "\\":
                k += 2
                continue
            if c == "{":
                brace += 1
            elif c == "}":
                brace -= 1
            elif c == "&" and depth == 1 and brace == 0:
                single = False
            k += 1
        if depth != 0 or not single:
            out.append(tex[i:j + len(b)])
            i = j + len(b)
            continue
        inner = strip_unit_matrix(tex[j + len(b):k])
        prev = tex[:j].rstrip()[-1:] if tex[:j].strip() else ""
        out.append(tex[i:j] + ("{" + inner + "}" if prev in ("^", "_") else inner))
        i = k + len(e)


def fix_math(tex: str) -> str:
    """Обернуть кириллицу вне \\text{…} в \\text{…} (прямой текстовый шрифт)."""
    tex = strip_unit_matrix(tex)
    out = []
    stack: list[bool] = []
    pending_text = False
    i, n = 0, len(tex)
    while i < n:
        c = tex[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", tex[i:])
            if m:
                name = m.group(1)
                out.append(m.group(0))
                i += len(m.group(0))
                if name in TEXT_CMDS:
                    j = i
                    while j < n and tex[j] == " ":
                        j += 1
                    if j < n and tex[j] == "{":
                        pending_text = True
                continue
            out.append(tex[i:i + 2])
            i += 2
            continue
        if c == "{":
            stack.append(pending_text or (bool(stack) and stack[-1]))
            pending_text = False
            out.append(c)
            i += 1
            continue
        if c == "}":
            if stack:
                stack.pop()
            out.append(c)
            i += 1
            continue
        if "\u0400" <= c <= "\u04ff" and not (stack and stack[-1]):
            j = i
            while j < n and ("\u0400" <= tex[j] <= "\u04ff" or (tex[j] == "." and j + 1 < n and "\u0400" <= tex[j + 1] <= "\u04ff")):
                j += 1
            out.append(r"\text{" + tex[i:j] + "}")
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉₋₊", "0123456789-+")


def text_latex(s: str) -> str:
    """Текст → LaTeX: экранирование; юникодные индексы (с⁻², s₀) — \\textsuperscript/\\textsubscript,
    потому что в Times New Roman этих знаков нет."""
    parts = re.split(r"([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+|[₀₁₂₃₄₅₆₇₈₉₋₊]+)", s)
    out = []
    for p in parts:
        if not p:
            continue
        if p[0] in "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺":
            out.append(r"\textsuperscript{" + p.translate(SUP).replace("-", r"\textminus{}") + "}")
        elif p[0] in "₀₁₂₃₄₅₆₇₈₉₋₊":
            out.append(r"\textsubscript{" + p.translate(SUB) + "}")
        else:
            out.append(latex_escape(p))
    return "".join(out)


def display_math(tex: str) -> str:
    t = tex.strip()
    if "\\\\" in t and not re.search(r"\\begin\{", t):
        env = "aligned" if "&" in t else "gathered"
        t = f"\\begin{{{env}}}{t}\\end{{{env}}}"
    return fix_math(t)


class LatexEmitter:
    def __init__(self, proj: Project, statuses: dict | None = None, mark: bool = True):
        self.p = proj
        self.st = statuses if statuses is not None else proj.calc_statuses()
        self.mark = mark
        self.marks: list[dict] = []          # id маркера → описание блока
        self.profile = get_profile(proj.doc_cfg.get("profile", "gost-7.32"))
        self.roles = proj.explication_roles()

    # --------------------------------------------------------- markers ----
    def _mark(self, b: Block, part: str = "") -> str:
        if not self.mark:
            return ""
        mid = len(self.marks)
        self.marks.append({"uid": b.uid, "file": b.file, "line": b.line, "kind": b.kind, "part": part,
                           "float": b.kind in ("figure",)})
        return str(mid)

    def _gen(self, uid: str) -> str:
        """Маркер сгенерированной части (титул, содержание, перечни, список источников)."""
        if not self.mark:
            return ""
        mid = len(self.marks)
        self.marks.append({"uid": uid, "file": "", "line": 0, "kind": "gen", "part": "", "float": False})
        return str(mid)

    math_mode = "latex"

    def expand(self, tex: str) -> str:
        from lde import mathins
        stale = [k for k, s_ in self.st.items() if s_.state != "ok"]
        return mathins.expand(tex, self.p, self.math_mode, stale)

    def stale_calc(self, key: str) -> bool:
        s = self.st.get(key)
        return bool(s and s.state != "ok")

    # ---------------------------------------------------------- inline ----
    def inline(self, nodes: list[Node]) -> str:
        out = []
        for nd in nodes:
            k = nd.k
            if k == "text":
                out.append(text_latex(nd.v))
            elif k == "b":
                out.append(r"\textbf{" + self.inline(nd.items) + "}")
            elif k == "i":
                out.append(r"\textit{" + self.inline(nd.items) + "}")
            elif k == "math":
                out.append("$" + fix_math(self.expand(nd.v)) + "$")
            elif k == "q":
                q = self.p.quantities.get(nd.v)
                if q:
                    out.append(r"\ensuremath{" + fix_math(q.tex) + "}")
                else:
                    out.append(r"\ldeunknown{" + latex_escape("{" + nd.v + "}") + "}")
            elif k == "val":
                out.append(self.value(nd))
            elif k == "ref":
                t = self.p.labels.get(nd.v)
                if t:
                    num = f"({t.number})" if t.kind == "eq" else t.number
                    out.append(r"\hyperlink{" + _hid(nd.v) + "}{" + latex_escape(num) + "}")
                else:
                    out.append(r"\ldeunknown{??}")
            elif k == "cite":
                nums = [(self.p.cite_num.get(key, 0), loc) for key, loc in nd.items]
                if any(n == 0 for n, _ in nums):
                    out.append(r"\ldeunknown{[?]}")
                else:
                    out.append(latex_escape(bibmod.cite_text(nums)))
        return "".join(out)

    def value(self, nd: Node) -> str:
        ck = nd.v.split(".", 1)[0]
        v = self.p.value(nd.v)
        if v is None:
            return r"\ldeunknown{[нет значения " + latex_escape(nd.v) + "]}"
        try:
            f = format_value(v["value"], v.get("unit", ""), v.get("digits"), nd.opts)
        except FormatError as e:
            return r"\ldeunknown{[" + latex_escape(str(e)) + "]}"
        if self.stale_calc(ck):
            return r"\ldestaleval{" + f["latex"] + "}"
        return f["latex"]

    # ---------------------------------------------------------- blocks ----
    def body(self) -> str:
        p = self.p
        out = []
        prev = None
        bib_done = False
        first_app = next((i for i, b in enumerate(p.blocks) if b.kind == "heading" and "appendix" in b.classes), None)
        for i, b in enumerate(p.blocks):
            if i == first_app and not bib_done:
                out.append(self.bibliography())
                bib_done = True
            out.append(self.block(b, prev))
            if b.kind != "comment":
                prev = b
        if not bib_done:
            out.append(self.bibliography())
        return "\n".join(x for x in out if x)

    def block(self, b: Block, prev: Block | None) -> str:
        s = self._block(b, prev)
        if s and b.style and b.kind in ("para", "heading", "list", "code"):
            s = self._styled(b, s)
        return s

    def _styled(self, b: Block, s: str) -> str:
        """Ручное оформление ({style …}): блок в группе со своими параметрами."""
        from lde.style import _num, latex_align, latex_font
        st = b.style
        pre = "\n\\par"
        if st.get("newpage"):
            pre += "\\clearpage"
        hooks = ""
        if b.kind == "heading":
            if any(k in st for k in ("align", "left", "right", "indent")):
                hooks += ("\\renewcommand{\\ldeheadalign}{" + latex_align(st, "") + "}"
                          "\\renewcommand{\\ldestructalign}{" + latex_align(st, "\\centering") + "}")
            if st.get("before"):
                hooks += "\\renewcommand{\\ldeheadbefore}{\\vspace*{" + _num(st["before"]) + "pt}}"
        else:
            if st.get("before"):
                pre += "\\vspace{" + _num(st["before"]) + "pt}"
            if b.kind == "code" and "size" in st:
                sz = _num(st["size"]), _num(st["size"] * 1.2)
                hooks += f"\\lstset{{basicstyle=\\ttfamily\\fontsize{{{sz[0]}pt}}{{{sz[1]}pt}}\\selectfont}}"
                st = {k: v for k, v in st.items() if k != "size"}
            hooks += latex_align(st) if b.kind in ("para", "list") else ""
        post = ""
        if st.get("keep") or b.kind == "heading":
            post += "\\nopagebreak[4]"
        if st.get("after"):
            post += "\\vspace{" + _num(st["after"]) + "pt}"
        return f"{pre}{{{hooks}{latex_font(st)}{s}\\par}}{post}"

    def _block(self, b: Block, prev: Block | None) -> str:
        p = self.p
        k = b.kind
        if k == "comment":
            return ""
        if k == "pagebreak":
            return r"\clearpage"
        if k == "conflict":
            m = self._mark(b)
            mk = f"\\ldemark{{{m}}}" if self.mark else ""
            return (f"\n\\par\\noindent{mk}\\fbox{{\\parbox{{0.97\\linewidth}}{{\\color{{red}}\\bfseries Конфликт слияния: "
                    f"этот фрагмент правили оба. Выберите вариант в Studio (щелчок здесь) или lde merge --resolve.}}}}\\par")
        if k == "heading":
            m = self._mark(b)
            t = self.inline(b.inl)
            num = p.numbers.get(id(b), "")
            target = r"\hypertarget{" + _hid(b.label) + "}{}" if b.label else ""
            if "appendix" in b.classes and b.level == 1:
                status = latex_escape(b.attrs.get("status", ""))
                return f"\\ldeappendix{{{num}}}{{{t}}}{{{m}}}{{{status}}}{target}"
            if b.unnumbered:
                if b.level == 1 and self.profile.structural_upper:
                    return f"\\ldestructural{{{t.upper() if self.profile.structural_upper else t}}}{{{m}}}{target}"
                if b.level == 1:
                    return f"\\ldestructural{{{t}}}{{{m}}}{target}"
                return f"\\ldeunnumbered{{{b.level}}}{{{t}}}{{{m}}}{target}"
            cmd = {1: "ldesection", 2: "ldesubsection", 3: "ldesubsubsection"}[b.level]
            return f"\\{cmd}{{{num}}}{{{t}}}{{{m}}}{target}"
        if k == "para":
            m = self._mark(b)
            text = self.inline(b.inl)
            role = self.roles.get(id(b))
            lead = ""
            if role == "gde":
                lead = r"\noindent"
                text = re.sub(r"^\s*где\s+", r"\\ldegde{}", text, count=1)
            elif role == "cont":
                lead = r"\noindent\ldegdeindent{}"
                text = text.lstrip()
            return f"\n{lead}\\ldemark{{{m}}}{text}" if self.mark else f"\n{lead}{text}"
        if k == "formula":
            m = self._mark(b)
            num = p.numbers.get(id(b))
            tag = f"\\tag{{{num}}}" if num else ""
            target = r"\hypertarget{" + _hid(b.label) + "}{}" if b.label else ""
            pre = ""
            if prev is None or prev.kind != "para" or prev.style:
                pre = "\n\\par\\vspace{-\\baselineskip}\\noindent"
            body = display_math(self.expand(b.math))
            mk = f"\\ldemark{{{m}}}" if self.mark else ""
            eq = f"\\begin{{equation*}}\n{body}{tag}\n\\end{{equation*}}"
            if b.style:
                from lde.style import _num, latex_font
                st = b.style
                adj = latex_font({"size": st["size"]}, keep_baseline=True) if "size" in st else ""
                for k_, lens in (("before", ("abovedisplayskip", "abovedisplayshortskip")),
                                 ("after", ("belowdisplayskip", "belowdisplayshortskip"))):
                    if st.get(k_):
                        adj += "".join(f"\\addtolength{{\\{ln}}}{{{_num(st[k_])}pt}}" for ln in lens)
                eq = f"\\begingroup{adj}%\n{eq}\\endgroup"
            return f"{pre}{mk}{target}%\n{eq}"
        if k == "figure":
            return self.figure(b)
        if k == "table":
            return self.table(b)
        if k == "list":
            return self.list_block(b)
        if k == "code":
            lang = {"python": "Python", "py": "Python", "c": "C", "cpp": "C++", "c++": "C++",
                    "matlab": "Matlab", "bash": "bash", "sh": "bash", "java": "Java"}.get(b.lang.lower(), "")
            opt = f"[language={lang}]" if lang else ""
            m = self._mark(b)
            return f"\n\\par\\ldemarkv{{{m}}}\\begin{{lstlisting}}{opt}\n{b.text}\n\\end{{lstlisting}}"
        return ""

    def _figure_stale(self, b: Block) -> str:
        reasons = []
        for ck in sorted(self.p.block_calcs(b)):
            s = self.st.get(ck)
            if s and s.state != "ok":
                c = self.p.calcs.get(ck)
                why = "; ".join(s.reasons[:2])
                reasons.append(f"Устарело: {why} (расчёт «{c.name if c else ck}»)")
        return " ".join(reasons)

    def _img(self, path: str, width: str) -> str:
        full = (self.p.root / path)
        if not path or not full.exists():
            return r"\ldemissing{Нет файла рисунка: " + latex_escape(path or "(путь не указан)") + "}"
        if full.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
            return r"\ldemissing{Формат " + latex_escape(full.suffix) + " не поддерживается: нужен PNG, JPG или PDF}"
        return (f"\\includegraphics[width={width}\\linewidth,height=0.72\\textheight,keepaspectratio]"
                f"{{{_texpath(self._fast_image(full))}}}")

    def _fast_image(self, full: Path) -> Path:
        """PNG → PDF без потерь (кэш по хешу): xdvipdfmx встраивает PDF копированием, а PNG
        перекодирует при каждой сборке (на эталоне — 2,7 с из 3,4 с)."""
        if full.suffix.lower() != ".png":
            return full
        import hashlib
        st = full.stat()
        key = hashlib.sha256(f"{full}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:20]
        cache = self.p.root / ".lde" / "img"
        out = cache / f"{key}.pdf"
        if out.exists():
            return out
        try:
            import pymupdf as fitz
            cache.mkdir(parents=True, exist_ok=True)
            with fitz.open(full) as img:
                data = img.convert_to_pdf()
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(out)
            return out
        except Exception:
            return full

    def figure(self, b: Block) -> str:
        m1 = self._mark(b)
        num = self.p.numbers.get(id(b), "?")
        cap = self.inline(b.inl)
        target = r"\hypertarget{" + _hid(b.label) + "}{}" if b.label else ""
        # по умолчанию «здесь» (как в DOCX — порядок совпадает); place=float разрешает перенос
        place = {"float": "!htbp", "here": "H", "top": "!t"}.get(b.attrs.get("place", "here"), "H")
        st = b.style
        from lde.style import _num, latex_font
        above = "\\ldefigabove" if not st.get("before") else f"\\dimexpr\\ldefigabove+{_num(st['before'])}pt\\relax"
        align = {"left": "\\raggedright", "right": "\\raggedleft"}.get(st.get("align", ""), "\\centering")
        font = latex_font({"size": st["size"]}) if "size" in st else ""
        parts = (["\\clearpage"] if st.get("newpage") else []) + [
            f"\\begin{{figure}}[{place}]", f"\\vspace*{{{above}}}{align}{font}" + (f"\\renewcommand{{\\ldefigalign}}{{{align}}}" if st.get("align") else ""), f"\\ldemarkv{{{m1}}}{target}%"]
        if b.subfigs:
            nsub = len(b.subfigs)
            per_row = int(b.attrs.get("cols", min(nsub, 2)))
            w = 0.98 / per_row
            for idx, (inl, path, attrs, raw) in enumerate(b.subfigs):
                sw = attrs.get("width")
                parts.append(f"\\begin{{minipage}}[t]{{{w:.3f}\\linewidth}}\\centering")
                parts.append(self._img(path, sw or "1.0"))
                if raw.strip():
                    parts.append(f"\\ldesubcaption{{{self.inline(inl)}}}")
                parts.append("\\end{minipage}" + ("\\par\\medskip" if (idx + 1) % per_row == 0 else "\\hfill"))
        else:
            parts.append(self._img(b.path, b.attrs.get("width", "0.9")) + "\\par")
        parts.append(f"\\ldefigcaption{{{latex_escape(num)}}}{{{cap}}}")
        stale = self._figure_stale(b)
        if stale:
            parts.append(f"\\ldestale{{{latex_escape(stale)}}}")
        m2 = self._mark(b, "end")
        parts.append(f"\\ldemarkv{{{m2}}}")
        parts.append("\\end{figure}")
        if st.get("after"):
            parts.append(f"\\vspace{{{_num(st['after'])}pt}}")
        return "\n" + "\n".join(parts)

    def table_cells(self, b: Block):
        """(заголовок, строки, выравнивания) — из блока или CSV."""
        if b.attrs.get("file"):
            import csv
            path = self.p.root / b.attrs["file"]
            if not path.exists():
                return None, f"Нет файла таблицы: {b.attrs['file']}"
            from lde.textfmt import parse_inline
            with open(path, encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                except csv.Error:
                    dialect = csv.excel
                rows = list(csv.reader(f, dialect))
            rows = [[(c, 0, parse_inline(c)) for c in r] for r in rows if r]
            return (rows[0], rows[1:], []), None
        return (b.header, b.rows, b.aligns), None

    def table(self, b: Block) -> str:
        m = self._mark(b)
        num = latex_escape(self.p.numbers.get(id(b), "?"))
        cap = self.inline(b.inl)
        target = r"\hypertarget{" + _hid(b.label) + "}{}" if b.label else ""
        data, err = self.table_cells(b)
        if err:
            return f"\n\\par\\noindent\\ldemark{{{m}}}Таблица {num} \\textemdash\\ {cap}\\par\\ldemissing{{{latex_escape(err)}}}\\par"
        header, rows, aligns = data
        ncol = max([len(header)] + [len(r) for r in rows] + [1])
        # ширины колонок пропорционально длине содержимого
        lens = [1] * ncol
        for r in ([header] if header else []) + rows:
            for j, c in enumerate(r[:ncol]):
                lens[j] = max(lens[j], min(len(c[0]), 60))
        tot = sum(lens)
        avail = 0.98
        widths = [max(0.08, avail * l / tot) for l in lens]
        sc = avail / sum(widths)
        widths = [w * sc for w in widths]
        al = {"l": "\\raggedright", "c": "\\centering", "r": "\\raggedleft"}
        cols = "|" + "|".join(
            f">{{{al.get((aligns[j] if j < len(aligns) else 'l'), '')}\\arraybackslash}}p{{{widths[j]:.3f}\\linewidth-2\\tabcolsep}}"
            for j in range(ncol)) + "|"

        def row_tex(r):
            cells = [self.inline(c[2]) for c in r] + [""] * (ncol - len(r))
            return " & ".join(cells[:ncol]) + r" \\ \hline"

        stale = self._figure_stale(b)
        st = b.style
        from lde.style import _num, latex_font
        sty = ""
        if st:
            sty = ("\\clearpage" if st.get("newpage") else "") + "\\begingroup" + latex_font({k: v for k, v in st.items() if k == "size"})
            sty += f"\\addtolength{{\\LTpre}}{{{_num(st.get('before', 0))}pt}}\\addtolength{{\\LTpost}}{{{_num(st.get('after', 0))}pt}}"
        lines = [f"\n\\par{sty}\\noindent\\ldemark{{{m}}}{target}%",
                 f"\\begin{{longtable}}{{{cols}}}",
                 f"\\multicolumn{{{ncol}}}{{@{{}}p{{\\linewidth}}@{{}}}}{{\\hspace*{{-\\tabcolsep}}Таблица {num} \\textemdash\\ {cap}}}\\\\[2pt]"
                 + (f"\\multicolumn{{{ncol}}}{{@{{}}p{{\\linewidth}}@{{}}}}{{\\color{{red}}\\small {latex_escape(stale)}}}\\\\" if stale else ""),
                 "\\hline"]
        if header:
            lines.append(row_tex(header))
        lines.append("\\endfirsthead")
        lines.append(f"\\multicolumn{{{ncol}}}{{@{{}}l}}{{\\hspace*{{-\\tabcolsep}}Продолжение таблицы {num}}}\\\\[2pt]\\hline")
        if header:
            lines.append(row_tex(header))
        lines.append("\\endhead")
        for r in rows:
            lines.append(row_tex(r))
        lines.append("\\end{longtable}")
        if st:
            lines.append("\\par\\endgroup")
        return "\n".join(lines)

    def list_block(self, b: Block) -> str:
        m = self._mark(b)
        out = []
        depth = 0
        envs = []
        first = True

        def env_for(mark):
            if mark in ("-", "*"):
                return "itemize", ""
            if re.fullmatch(r"\d+\.", mark):
                return "enumerate", r"[label=\arabic*.]"
            if re.fullmatch(r"\d+\)", mark):
                return "enumerate", r"[label=\arabic*)]"
            if re.fullmatch(r"[а-я]\)", mark):
                return "enumerate", r"[label=\asbuk*)]"
            return "enumerate", r"[label=\alph*)]"

        for lvl, mark, text, off, nodes in b.items:
            while depth < lvl:
                env, opt = env_for(mark)
                out.append(f"\\begin{{{env}}}{opt}")
                envs.append(env)
                depth += 1
            while depth > lvl:
                out.append(f"\\end{{{envs.pop()}}}")
                depth -= 1
            mk = f"\\ldemark{{{m}}}" if first else ""
            first = False
            out.append(f"\\item {mk}{self.inline(nodes)}")
        while envs:
            out.append(f"\\end{{{envs.pop()}}}")
        return "\n" + "\n".join(out)

    # ------------------------------------------------- generated parts ----
    def bibliography(self) -> str:
        p = self.p
        if not p.doc_cfg.get("bibliography", True) or not p.cite_order:
            return ""
        head = self.profile.heading("bibliography")
        items = []
        for i, key in enumerate(p.cite_order, 1):
            e = p.bib.get(key)
            text = bibmod.format_entry(e) if e else f"[нет записи «{key}» в refs.bib]"
            esc = latex_escape(text)
            if not e:
                esc = r"\ldeunknown{" + esc + "}"
            dot = "." if self.profile.bib_number_dot else ""
            items.append(f"\\par\\hypertarget{{cite:{_hid(key)}}}{{}}\\ldemark{{{self._gen('gen:bib:' + key)}}}{i}{dot}\\ {esc}")
        return f"\\ldestructural{{{head}}}{{{self._gen('gen:bib')}}}\n" + "\n".join(items)

    def abbreviations(self) -> str:
        p = self.p
        parts = []
        if p.doc_cfg.get("abbreviations", True) and p.abbreviations:
            items = list(p.abbreviations.items())
            if self.profile.abbr_sort:
                items.sort(key=lambda kv: kv[0].lower())
            parts.append(f"\\ldestructural{{{self.profile.heading('abbreviations')}}}{{{self._gen('gen:abbr')}}}")
            for k, v in items:
                parts.append(f"\\par\\noindent {latex_escape(k)}~\\textendash\\ {latex_escape(v)}")
        if p.doc_cfg.get("symbols", False) and p.quantities:
            if not parts:
                parts.append(f"\\ldestructural{{{self.profile.heading('symbols')}}}{{{self._gen('gen:symbols')}}}")
            else:
                parts.append("\\par\\bigskip")
            for q in p.quantities.values():
                unit = f", {latex_escape(q.unit)}" if q.unit else ""
                parts.append(f"\\par\\noindent $" + fix_math(q.tex) + f"$~\\textendash\\ {latex_escape(q.meaning)}{unit}")
        return "\n".join(parts)

    def document(self) -> str:
        p = self.p
        pre = Path(__file__).parent / "tex" / "preamble.tex"
        out = [f"\\input{{{_texpath(pre)}}}"]
        out.append(self.profile.preamble_extra(p))
        out.append(f"\\hypersetup{{pdftitle={{{latex_escape(p.title)}}}}}")
        if p.doc_cfg.get("block_spacing", "none") == "line":
            out.append("\\setlength{\\ldeblockskip}{\\baselineskip}\\AtBeginDocument{\\ldeapplyskips}")
        if not p.doc_cfg.get("hyphenation", True):
            out.append("\\ldenohyphenation")
        out.append("\\begin{document}")
        tp = self.profile.titlepage_latex(p)
        if tp and self.mark:
            tp = tp.replace("\\thispagestyle{empty}", "\\thispagestyle{empty}\\ldemarkv{" + self._gen("gen:title") + "}", 1)
        out.append(tp)
        if p.doc_cfg.get("toc", True):
            out.append(f"\\ldetoc{{{self.profile.heading('toc')}}}{{{self._gen('gen:toc')}}}")
        ab = self.abbreviations()
        if ab:
            out.append(ab)
        out.append(self.body())
        out.append("\\end{document}")
        return "\n".join(out) + "\n"


def _hid(label: str) -> str:
    return "lde:" + re.sub(r"[^A-Za-z0-9:_\-.]", lambda m: f"u{ord(m.group(0)):x}", label)


def _texpath(p: Path) -> str:
    s = str(p)
    if any(c in s for c in " #%&{}~$"):
        return "\"" + s + "\""
    return s
