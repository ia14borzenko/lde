"""lde check — линтер исходника: ошибки (красное) и замечания (жёлтое) с файлом и строкой.

Главные правила (ответ на провалы RDE):
  qty-as-text     величина из quantities.toml набрана обычным текстом (Rц, fоп, RцD(fоп), φ1)
  qty-as-math     формула $R_{ц}$ совпадает с величиной — вставьте {R_ц}
  number-typed    число, совпадающее с выходом расчёта, набрано руками
  ref-as-text     «рисунок 4», «формула (5)», «таблица 2» без ссылки @…
  cite-as-text    «[3]» набрано руками вместо [@ключ]
и проверки целостности: неизвестные величины/метки/источники/числа, устаревшие и упавшие
расчёты, отсутствующие файлы, сырая вёрстка, ГОСТ-оформление подписей и заголовков.

Линтер только сообщает и предлагает замену (fix) — сам он исходник не меняет.
"""
from __future__ import annotations

import difflib
import re

from lde import calcs as calcmod
from lde.fmt import BASES, FormatError, format_value, split_unit

UNIT_EXTRA = {"дБ", "м/с", "рад/с", "Гц/с", "рад", "%"}
from lde.project import Issue, Project
from lde.quantities import Quantity, norm_math
from lde.textfmt import Block, Node, walk

LAT = "A-Za-z\u0370-\u03ff"
CYR = "\u0400-\u04ff"


def _cls(ch: str) -> str:
    if re.match(f"[{CYR}]", ch):
        return f"[{CYR}]"
    if re.match(f"[{LAT}]", ch):
        return f"[{LAT}]"
    if ch.isdigit():
        return "[0-9]"
    return ""


def _qty_patterns(qs: dict[str, Quantity]):
    """[(regex, quantity, variant, single_latin)] — длинные варианты раньше коротких."""
    pats = []
    for q in qs.values():
        for v in q.text_variants():
            if not v:
                continue
            first, last = _cls(v[0]), _cls(v[-1])
            pre = f"(?<!{first})" if first else ""
            post = f"(?!{last})" if last else ""
            if v[-1] in ")}":
                post = ""
            pat = re.compile(pre + re.escape(v) + post)
            single_latin = len(v) == 1 and v.isascii()
            pats.append((len(v), pat, q, v, single_latin))
    pats.sort(key=lambda x: -x[0])
    return [(p, q, v, s) for _, p, q, v, s in pats]


MATHY_NEXT = set("=(<>≤≥≈^")
MATHY_PREV = set("=(+−*/·×≈^")


def _single_letter_mathy(text: str, s: int, e: int, line_text: str) -> bool:
    nxt = text[e:].lstrip()[:1]
    prv = text[:s].rstrip()[-1:]
    if nxt in MATHY_NEXT or prv in MATHY_PREV:
        return True
    # «где V – скорость», «V – скорость» в начале строки пояснения
    before = text[:s]
    if re.search(r"(^|\s)где\s*$", before) and re.match(r"\s*[–—-]\s", text[e:]):
        return True
    if not before.strip() and re.match(r"\s*[–—-]\s", text[e:]):
        return True
    if re.match(r"\s*[,и]\s*", text[e:]) and re.search(r"(^|\s)где\s*$", before):
        return True
    return False


class Checker:
    def __init__(self, proj: Project, statuses=None):
        self.p = proj
        self.issues: list[Issue] = list(proj.issues)
        self.st = statuses if statuses is not None else proj.calc_statuses()
        self.qpats = _qty_patterns(proj.quantities)
        self.qnorm = {norm_math(q.tex): q for q in proj.quantities.values()}
        self.qnorm.update({norm_math(q.key.replace("_", "_{") + ("}" if "_" in q.key else "")): q
                           for q in proj.quantities.values()})
        self._numbers = None
        self.roles = proj.explication_roles()
        self._lineoffs: dict[str, list[int]] = {}
        # расчёты, рисунки которых стоят в том же подразделе — для выбора при совпадении чисел
        self.calc_sections: dict[str, set] = {}
        for b in proj.blocks:
            if b.kind == "figure":
                for ck in proj.block_calcs(b):
                    self.calc_sections.setdefault(ck, set()).add(proj.heading_path.get(id(b), ""))

    def add(self, sev, rule, msg, b: Block | None = None, off: int | None = None, hint="", fix=None, file="", line=0):
        if b is not None:
            file = b.file
            line = b.line_of(off) if off is not None else b.line
        if fix and b is not None and "s" in fix:
            fix = self._abs_fix(b, fix["s"], fix["e"], fix["new"])
        elif fix and "s" not in (fix or {}) and "start" not in (fix or {}):
            fix = None
        self.issues.append(Issue(sev, rule, msg, file, line, hint=hint, fix=fix, element=b.uid if b else ""))

    def _abs_fix(self, b: Block, s: int, e: int, new: str, expect: str | None = None):
        """Исправление как замена [start, end) в файле; None, если исходник не совпал (экранирование и т.п.)."""
        text = self.p.texts.get(b.file)
        if text is None:
            return None
        if b.file not in self._lineoffs:
            offs, acc = [], 0
            for ln in text.split("\n"):
                offs.append(acc)
                acc += len(ln) + 1
            self._lineoffs[b.file] = offs
        base = self._lineoffs[b.file][b.line - 1]
        old = text[base + s:base + e]
        if expect is not None and old != expect:
            return None
        return {"file": b.file, "start": base + s, "end": base + e, "old": old, "new": new}

    # ------------------------------------------------------------- run ----
    def check_unit_dup(self, b: Block):
        """{=x} уже печатает единицу — «{=x} м» даст «1,59 м м»."""
        nodes = list(self.p.inline_nodes(b))
        for i, nd in enumerate(nodes[:-1]):
            if nd.k != "val" or "-" in nd.opts.split():
                continue
            v = self.p.value(nd.v)
            if not v:
                continue
            try:
                unit = format_value(v["value"], v.get("unit", ""), v.get("digits"), nd.opts)["unit"]
            except FormatError:
                continue
            nxt = nodes[i + 1]
            if not unit or nxt.k != "text":
                continue
            m = re.match(r"[ \u00a0]*" + re.escape(unit) + r"(?![А-Яа-яA-Za-z])", nxt.v)
            if m:
                self.add("warning", "unit-duplicated", f"единица «{unit}» повторена: {{={nd.v}}} уже печатается с ней",
                         b, nd.s, hint=f"уберите «{unit}» после вставки (или добавьте « -» в вставку, если единица нужна отдельно)",
                         fix=self._abs_fix(b, nxt.s, nxt.s + m.end(), "", expect=nxt.v[:m.end()]))

    def run(self) -> list[Issue]:
        p = self.p
        used_q, used_lab = set(), set()
        for b in p.blocks:
            self.check_unit_dup(b)
            if b.kind == "formula":
                self.check_formula(b)
            if b.style_len:
                from lde.style import describe_ru
                self.add("info", "manual-style", "ручное оформление вместо автоматического: "
                         + (describe_ru(b.style) or "параметры не заданы"), b,
                         hint="убрать: удалите строку {style …} над блоком или снимите флажок в Studio")
            for nd in p.inline_nodes(b):
                if nd.k == "text":
                    self.check_text(b, nd)
                elif nd.k == "q":
                    used_q.add(nd.v)
                    if nd.v not in p.quantities:
                        close = difflib.get_close_matches(nd.v, list(p.quantities), 3, 0.5)
                        self.add("error", "qty-unknown", f"величина {{{nd.v}}} не объявлена", b, nd.s,
                                 hint=("может быть: " + ", ".join("{" + c + "}" for c in close)) if close
                                 else f"объявите её: lde new quantity {nd.v} --meaning \"…\" --unit …")
                elif nd.k == "math":
                    self.check_inline_math(b, nd)
                elif nd.k == "val":
                    self.check_value(b, nd)
                elif nd.k == "ref":
                    used_lab.add(nd.v)
                    if nd.v not in p.labels:
                        close = difflib.get_close_matches(nd.v, list(p.labels), 3, 0.6)
                        self.add("error", "ref-unknown", f"ссылка на несуществующую метку @{nd.v}", b, nd.s,
                                 hint=("может быть: " + ", ".join("@" + c for c in close)) if close else "")
                elif nd.k == "cite":
                    for key, _ in nd.items:
                        if key not in p.bib:
                            close = difflib.get_close_matches(key, list(p.bib), 3, 0.6)
                            self.add("error", "cite-unknown", f"источника «{key}» нет в refs.bib", b, nd.s,
                                     hint=("может быть: " + ", ".join(close)) if close else "добавьте запись: lde new source …")
            self.check_block_gost(b)
            if b.kind == "figure":
                self.check_figure(b)
            if b.kind == "table" and b.attrs.get("file"):
                if not (p.root / b.attrs["file"]).exists():
                    self.add("error", "file-missing", f"нет файла таблицы {b.attrs['file']}", b)
        # неиспользуемые метки
        for lab, t in p.labels.items():
            if lab in used_lab:
                continue
            if t.kind in ("fig", "tab"):
                self.add("warning", "unreferenced", f"на {'рисунок' if t.kind == 'fig' else 'таблицу'} {t.number} нет ссылки в тексте",
                         t.block, hint=f"сошлитесь: «… на рисунке @{lab}»" if t.kind == "fig" else f"«… в таблице @{lab}»")
            elif t.kind == "app":
                self.add("warning", "unreferenced", f"на приложение {t.number} нет ссылки в тексте", t.block)
        for b in p.blocks:
            if b.kind in ("figure", "table") and not b.label:
                self.add("warning", "no-label", f"у {'рисунка' if b.kind == 'figure' else 'таблицы'} нет метки — на него нельзя сослаться",
                         b, hint="{#fig:имя}" if b.kind == "figure" else "{#tab:имя}")
        cited_elsewhere = self._cited_in_other_documents()
        for key in p.bib:
            if key not in p.cite_num and key not in cited_elsewhere:
                self.add("warning", "uncited", f"источник «{key}» нигде не цитируется — в список он не попадёт",
                         file="refs.bib", line=p.bib[key].line, hint=f"сошлитесь: [@{key}]")
        for k, q in p.quantities.items():
            if k not in used_q and p.doc_cfg.get("symbols"):
                self.add("info", "qty-unused", f"величина {{{k}}} объявлена, но не используется", file="quantities.toml", line=q.line)
        self.check_explications()
        self.check_symbols()
        self.check_calcs()
        return self.issues

    def _cited_in_other_documents(self) -> set:
        """Источники общей базы, процитированные в других документах проекта."""
        from lde.project import list_documents
        keys = set()
        for d in list_documents(self.p.root):
            if d["key"] == self.p.doc_key:
                continue
            base = self.p.root / ("text" if d["key"] == "main" else f"documents/{d['key']}")
            for f in base.glob("*.md"):
                for m in re.finditer(r"\[@([^\]]+)\]", f.read_text(encoding="utf-8")):
                    keys.update(re.findall(r"@([\w\-:.]+)", "@" + m.group(1)))
        return keys

    def check_explications(self):
        """«где f – линейная частота, Гц;»: обозначение в пояснении к формуле — вставкой величины.
        Если величины нет — предложить объявить её (смысл и единица — из этой же строки)."""
        rx = re.compile(r"^(\s*(?:где\s+)?)(?P<sym>\$[^$]+\$|[^\s{}–—\-$]{1,14})(\s*[–—\-]\s+)(?P<rest>.+?)\s*[;.]?\s*$")
        declared = set()
        for b in self.p.blocks:
            if id(b) not in self.roles:
                continue
            m = rx.match(b.text)
            if not m:
                continue
            sym = m.group("sym")
            raw = sym[1:-1] if sym.startswith("$") else sym
            key = norm_math(raw) if sym.startswith("$") else raw
            if norm_math(raw) in self.qnorm or key in self.p.quantities:
                continue       # это объявленная величина — о ней скажут qty-as-text / qty-as-math
            if not re.fullmatch(r"[A-Za-zΑ-Ωα-ωΔ][\w′']{0,3}(?:_[\wА-Яа-я.′']{1,8})?", key):
                continue       # сложное выражение — не обозначение
            rest = m.group("rest")
            mm = re.match(r"^(?P<meaning>.+?)(?:,\s*(?P<unit>[^,;]{1,12}))?$", rest)
            meaning, unit = (mm.group("meaning"), mm.group("unit") or "") if mm else (rest, "")
            if unit and not re.fullmatch(r"[А-Яа-яA-Za-zμΩ°%/·\-\d ]+", unit):
                meaning, unit = rest, ""
            meaning = re.sub(r"\{=?([^{}\s]+)[^{}]*\}", r"\1", meaning).replace("$", "")
            fx = self._abs_fix(b, m.start("sym"), m.end("sym"), "{" + key + "}", expect=sym)
            if fx and key not in declared:
                fx["declare"] = {"key": key, "meaning": meaning.strip(), "unit": unit.strip()}
                declared.add(key)
            self.add("warning", "symbol-in-where", f"обозначение «{raw}» в пояснении к формуле набрано текстом и не объявлено величиной",
                     b, m.start("sym"), hint=f"объявить величину {{{key}}} («{meaning.strip()}»{', ' + unit.strip() if unit else ''}) и вставить её (lde fix)",
                     fix=fx)

    def check_symbols(self):
        """Обозначение, набранное формулой несколько раз и не объявленное величиной (Y_{21}^{0,5})."""
        seen: dict[str, list] = {}
        for b in self.p.blocks:
            for nd in self.p.inline_nodes(b):
                if nd.k != "math":
                    continue
                t = nd.v.strip()
                if len(t) > 40 or not re.fullmatch(r"\\?[A-Za-zΑ-Ωα-ω]+(?:\s*[_^]\s*(?:\{[^{}]{1,12}\}|\w))+'?", t):
                    continue
                n = norm_math(t)
                if n in self.qnorm:
                    continue
                seen.setdefault(n, []).append((b, nd))
        for n, uses in seen.items():
            if len(uses) >= 2:
                b, nd = uses[0]
                self.add("info", "symbol-undeclared",
                         f"обозначение ${nd.v}$ набрано формулой {len(uses)} раз(а) и не объявлено величиной", b, nd.s,
                         hint="объявите: lde new quantity <ключ> --meaning … --unit …, затем вставляйте {ключ}")

    # ------------------------------------------------------------ rules ----
    def check_text(self, b: Block, nd: Node):
        text = nd.v
        # сырая вёрстка
        for m in re.finditer(r"\\[A-Za-z]{2,}", text):
            self.add("error", "raw-latex", f"команда LaTeX {m.group(0)} в тексте", b, nd.s + m.start(),
                     hint="формулы — в $…$, вставки — {величина}, @ссылка; оформление делает LDE")
        # величины текстом
        taken = []
        for pat, q, v, single in self.qpats:
            for m in pat.finditer(text):
                s, e = m.start(), m.end()
                if any(not (e <= a or s >= bb) for a, bb in taken):
                    continue
                if single and not _single_letter_mathy(text, s, e, text):
                    continue
                if len(v) == 1 and not v.isascii() and not re.match(f"[{LAT}]", v):
                    continue
                taken.append((s, e))
                fx = self._abs_fix(b, nd.s + s, nd.s + e, "{" + q.key + "}", expect=m.group(0))
                self.add("error", "qty-as-text", f"величина «{m.group(0)}» набрана текстом", b, nd.s + s,
                         hint=f"вставьте {{{q.key}}}", fix=fx)
        # ссылки на объекты текстом
        for m in re.finditer(r"(?<![\w@])(рисун\w*|рис\.|таблиц\w*|табл\.|формул\w*|выражени\w*)\s*\(?((?:[А-ЯA-Z]\.)?\d+(?:\.\d+)?)\)?(?:\s*,\s*[а-я]\b)?",
                             text, flags=re.IGNORECASE):
            word, num = m.group(1).lower(), m.group(2)
            kind = "fig" if word.startswith("рис") else "tab" if word.startswith("табл") else "eq"
            lab = self._label_by_number(kind, num)
            hint = f"«{m.group(1)} @{lab}»" if lab else "вставьте ссылку @fig:…/@tab:…/@eq:…"
            fx = None
            if lab:
                a, z = m.start(2), m.end(2)
                if kind == "eq" and text[a - 1:a] == "(" and text[z:z + 1] == ")":
                    a, z = a - 1, z + 1
                fx = self._abs_fix(b, nd.s + a, nd.s + z, "@" + lab, expect=text[a:z])
            self.add("warning", "ref-as-text", f"«{m.group(0).strip()}» — номер набран вручную, при перенумерации он не обновится",
                     b, nd.s + m.start(), hint=hint, fix=fx)
        word_spans = [(m.start(), m.end()) for m in re.finditer(r"формул\w*\s*\(\d+(?:\.\d+)?\)", text, re.I)]
        for m in re.finditer(r"(?<![\w@(])\((\d{1,3}(?:\.\d+)?)\)", text):
            if any(a <= m.start() < b for a, b in word_spans):
                continue
            lab = self._label_by_number("eq", m.group(1))
            if lab:
                self.add("warning", "ref-as-text", f"«({m.group(1)})» похоже на номер формулы, набранный вручную",
                         b, nd.s + m.start(), hint=f"@{lab}",
                         fix=self._abs_fix(b, nd.s + m.start(), nd.s + m.end(), "@" + lab, expect=m.group(0)))
        for m in re.finditer(r"(?<![\w@])(§\s*|п\.\s*|пункт\w*\s+|раздел\w*\s+|подраздел\w*\s+)(\d{1,2}(?:\.\d{1,2}){0,2})(?![\d.]*\d)", text, re.I):
            lab = self._label_by_number("sec", m.group(2))
            if not lab:
                continue
            fx = self._abs_fix(b, nd.s + m.start(2), nd.s + m.end(2), "@" + lab, expect=m.group(2))
            self.add("warning", "ref-as-text", f"«{m.group(0).strip()}» — номер раздела набран вручную",
                     b, nd.s + m.start(), hint=f"«{m.group(1).strip()} @{lab}»", fix=fx)
        for m in re.finditer(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)(?:,\s*с\.\s*[\d–\-]+)?\]", text):
            self.add("warning", "cite-as-text", f"«{m.group(0)}» — ссылка на источник набрана вручную",
                     b, nd.s + m.start(), hint="[@ключ] или [@ключ, с. 45]")
        self.check_numbers(b, nd)

    def _label_by_number(self, kind: str, num: str) -> str:
        for lab, t in self.p.labels.items():
            if t.kind == kind and t.number == num:
                return lab
        return ""

    def check_math_inserts(self, b: Block, tex: str, off: int | None):
        from lde import mathins
        for kind, arg, a, _ in mathins.find(tex):
            if kind == "q" and arg not in self.p.quantities:
                close = difflib.get_close_matches(arg, list(self.p.quantities), 3, 0.5)
                self.add("error", "qty-unknown", f"в формуле величина \\q{{{arg}}} не объявлена", b, off,
                         hint=("может быть: " + ", ".join(close)) if close else f"lde new quantity {arg} …")
            elif kind == "val":
                path, _, opts = arg.partition(" ")
                self.check_value(b, Node("val", off or 0, off or 0, path, opts.strip()))

    def check_inline_math(self, b: Block, nd: Node):
        self.check_math_inserts(b, nd.v, nd.s)
        if "\\q{" in nd.v or "\\val{" in nd.v:
            return
        n = norm_math(nd.v)
        q = self.qnorm.get(n)
        src = self.p.texts.get(b.file, "")
        if q:
            self.add("error", "qty-as-math", f"величина набрана формулой ${nd.v}$", b, nd.s,
                     hint=f"вставьте {{{q.key}}}: единый набор, попадёт в перечень обозначений",
                     fix=self._abs_fix(b, nd.s, nd.e, "{" + q.key + "}"))
            return
        # «V = 150 м/с»: величина и число в одной формуле
        m = re.match(r"^\s*(.+?)\s*=\s*(.+?)\s*$", nd.v)
        if not m:
            return
        q = self.qnorm.get(norm_math(m.group(1)))
        if not q:
            return
        rhs = m.group(2)
        rhs = re.sub(r"\\(text|mathrm|textrm|mbox)\s*\{([^{}]*)\}", r" \2 ", rhs)
        rhs = rhs.replace("{,}", ",").replace("\\ ", " ").replace("\\,", " ").replace("~", " ").replace("−", "-")
        rhs = re.sub(r"\s+", " ", rhs).strip()
        mm = re.fullmatch(r"(-?\d+(?:[.,]\d+)?)\s*([^\d\s]\S*)?", rhs)
        new = "{" + q.key + "} = "
        hint = f"вставьте {{{q.key}}}"
        if mm:
            val = self._value_insert(b, mm.group(1), mm.group(2) or "")
            if val:
                new += val
                hint += f" и число расчёта {val}"
            else:
                num = mm.group(1).replace(".", ",")
                new += num + ("\u00a0" + mm.group(2) if mm.group(2) else "")
        else:
            return
        self.add("error", "qty-as-math", f"величина набрана формулой ${nd.v}$", b, nd.s, hint=hint,
                 fix=self._abs_fix(b, nd.s, nd.e, new))

    def _value_insert(self, b: Block, s_num: str, unit: str) -> str | None:
        """Число (как набрано) → вставка {=расчёт.путь опции}, если оно однозначно совпадает с выходом расчёта."""
        s_num = re.sub(r"^[−\-–]\s?", "-", s_num)
        try:
            num = float(s_num.replace(",", "."))
        except ValueError:
            return None
        if num == 0:
            return None
        f, base = split_unit(unit) if unit else (1.0, "")
        dec = len(s_num.replace(",", ".").split(".")[1]) if ("," in s_num or "." in s_num) else 0
        val = num * f
        tol = 0.5 * 10 ** (-dec) * f
        hits = []
        for v, vb, ck, path in self._calc_numbers():
            if vb and base != vb:
                continue           # у значения своя единица, и она другая
            if abs(v - val) <= max(tol, 1e-12 * abs(v)) and self._close(v, val, ck, path, s_num, unit):
                hits.append((ck, path))
        if not hits:
            return None
        # вдали от рисунков расчёта короткие числа совпадают случайно («1,9 м» в теории)
        hp = self.p.heading_path.get(id(b), "")
        sig = len(re.sub(r"\D", "", s_num).lstrip("0"))
        hits = [h for h in hits if hp in self.calc_sections.get(h[0], set()) or sig >= 3]
        if not hits:
            return None
        cks = sorted(set(h[0] for h in hits))
        if len(cks) > 1:
            hp = self.p.heading_path.get(id(b), "")
            pref = [ck for ck in cks if hp and hp in self.calc_sections.get(ck, set())]
            if len(pref) != 1:
                return None
            cks = pref
        paths = sorted((h[1] for h in hits if h[0] == cks[0]), key=lambda x: (x.count("."), len(x)))
        ck, path = cks[0], paths[0]
        meta = calcmod.lookup_value(self.p.calcs[ck], path) or {}
        typed = (s_num.replace(".", ",") + ("\u00a0" + unit if unit else "")).replace("-", "\u2212")
        cands = ("", unit, f".{dec}", f".{dec} {unit}") if unit else ("", "-", f".{dec}", f".{dec} -")
        for opts in cands:
            try:
                t = format_value(meta.get("value"), meta.get("unit", ""), meta.get("digits"), opts)["text"]
            except (FormatError, TypeError):
                continue
            if t.replace("\u202f", "") == typed:
                return "{=" + f"{ck}.{path}" + (f" {opts}" if opts else "") + "}"
        return "{=" + f"{ck}.{path} .{dec}" + (f" {unit}" if unit else " -") + "}"

    def _close(self, v: float, val: float, ck: str, path: str, s_num: str, unit: str) -> bool:
        """Набранное число — то же, что выход расчёта, а не грубая оценка («1 м» при 1,499 м): расхождение
        не больше 5 % или расчёт сам печатает значение так же (его digits)."""
        if abs(v - val) <= 0.05 * abs(val):
            return True
        meta = calcmod.lookup_value(self.p.calcs[ck], path) or {}
        try:
            t = format_value(meta.get("value"), meta.get("unit", ""), meta.get("digits"), unit or "-")["text"]
        except (FormatError, TypeError):
            return False
        return t.replace("\u202f", "").replace("\u2212", "-") == s_num.replace(".", ",") + ("\u00a0" + unit if unit else "")

    @staticmethod
    def _cited_sentence(b: Block, pos: int) -> bool:
        """Число стоит в предложении со ссылкой на источник [@…] — это данные из литературы, не из расчёта."""
        src = b.src
        start = 0
        for m in re.finditer(r"[.!?…]\s+(?=[«(\[]?[А-ЯЁA-Z])", src[:pos]):
            start = m.end()
        m = re.search(r"[.!?…](?:\s+(?=[«(\[]?[А-ЯЁA-Z])|\s*$)", src[pos:])
        end = pos + m.end() if m else len(src)
        return "[@" in src[start:end]

    def check_formula(self, b: Block):
        self.check_math_inserts(b, b.math, None)
        if not b.math.strip():
            self.add("error", "formula-empty", "пустая формула", b)
        depth = 0
        for ch in re.sub(r"\\[{}]", "", b.math):
            depth += (ch == "{") - (ch == "}")
            if depth < 0:
                break
        if depth != 0:
            self.add("error", "formula-braces", "непарные фигурные скобки в формуле", b)
        if b.label and not b.label.startswith("eq:"):
            pass  # проверяется в project
        if re.search(r"\\(label|tag|ref)\b", b.math):
            self.add("error", "raw-latex", "\\label/\\tag/\\ref внутри формулы: номер ставит LDE", b,
                     hint="метку пишите после формулы: $$ … $$ {#eq:имя}")

    def check_value(self, b: Block, nd: Node):
        ck, _, rest = nd.v.partition(".")
        c = self.p.calcs.get(ck)
        if not c:
            self.add("error", "value-unknown", f"нет расчёта «{ck}» для числа {{={nd.v}}}", b, nd.s,
                     hint="расчёты: " + ", ".join(self.p.calcs) if self.p.calcs else "создайте: lde new calc …")
            return
        if not c.run_info():
            self.add("error", "value-norun", f"расчёт «{c.name}» ещё не запускался — числа {{={nd.v}}} нет", b, nd.s,
                     hint="lde update")
            return
        v = calcmod.lookup_value(c, rest)
        if v is None:
            close = difflib.get_close_matches(rest, [k for k, _ in calcmod.all_value_paths(c)], 3, 0.5)
            self.add("error", "value-unknown", f"в выходах расчёта «{c.name}» нет числа «{rest}»", b, nd.s,
                     hint=("может быть: " + ", ".join("{=" + ck + "." + x + "}" for x in close)) if close else "")
            return
        try:
            format_value(v["value"], v.get("unit", ""), v.get("digits"), nd.opts)
        except FormatError as e:
            self.add("error", "value-format", f"{{={nd.v} {nd.opts}}}: {e}", b, nd.s)

    def check_figure(self, b: Block):
        paths = [b.path] if b.path else []
        paths += [sf[1] for sf in b.subfigs]
        for path in paths:
            full = self.p.root / path
            m = re.match(r"^calc/([^/]+)/out/(.+)$", path)
            if m:
                c = self.p.calcs.get(m.group(1))
                if not c:
                    self.add("error", "file-missing", f"рисунок ссылается на несуществующий расчёт «{m.group(1)}»", b)
                elif m.group(2) not in c.outputs and c.outputs:
                    self.add("warning", "calc-output", f"файл {m.group(2)} не объявлен в outputs расчёта «{c.name}»", b,
                             hint=f"добавьте его в outputs в calc/{c.key}/calc.toml")
                if not full.exists() and c:
                    self.add("error", "file-missing", f"рисунка {path} ещё нет: расчёт «{c.name}» не запускался или не создал его", b,
                             hint="lde update")
            elif not full.exists():
                self.add("error", "file-missing", f"нет файла рисунка {path}", b)
        if b.text.strip().endswith("."):
            self.add("warning", "gost-caption", "в конце подписи рисунка точка не ставится (ГОСТ 7.32 п. 6.5.7)", b)

    def check_block_gost(self, b: Block):
        if b.kind == "heading":
            if b.text.rstrip().endswith("."):
                self.add("warning", "gost-heading", "в конце заголовка точка не ставится (ГОСТ 7.32 п. 6.2.3)", b)
        if b.kind == "table" and b.text.strip().endswith("."):
            self.add("warning", "gost-caption", "в конце заголовка таблицы точка не ставится (ГОСТ 7.32 п. 6.6.3)", b)
        if b.kind == "para" and re.match(r"^\s*где\s*:", b.text):
            self.add("warning", "gost-where", "после «где» двоеточие не ставится (ГОСТ 7.32 п. 6.8.2)", b)

    # --------------------------------------------------- numbers by hand ----
    def _calc_numbers(self):
        if self._numbers is None:
            nums = []
            for ck, c in self.p.calcs.items():
                for path, v in calcmod.all_value_paths(c):
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
                        continue
                    meta = calcmod.lookup_value(c, path) or {}
                    unit = meta.get("unit", "")
                    f, base = split_unit(unit) if unit else (1.0, "")
                    nums.append((float(v) * f, base, ck, path))
            self._numbers = nums
        return self._numbers

    def check_numbers(self, b: Block, nd: Node):
        if not self.p.calcs:
            return
        cands = self._calc_numbers()
        if not cands:
            return
        text = nd.v
        for m in re.finditer(r"(?<![\w.,])([−\-–]?\s?\d+(?:[.,]\d+)?)(?:\s?(?:·|\*)\s?10\^?([\-−]?\d+))?(?:\s*([А-Яа-яA-Za-zμ/]+))?", text):
            s_num = re.sub(r"^[−\-–]\s?", "-", m.group(1))
            if s_num.startswith("-") and m.start() > 0 and text[m.start() - 1].isalnum():
                s_num = s_num[1:]          # дефис внутри слова/диапазона, не минус
            pre = text[:m.start()].lower()
            if self._cited_sentence(b, nd.s + m.start()):
                continue
            if re.search(r"(рис\w*|табл\w*|формул\w*|раздел\w*|п\.|гл\.|с\.|подраздел\w*|приложени\w*|\[)\s*$", pre):
                continue
            num = float(s_num.replace(",", "."))
            if m.group(2):
                num *= 10 ** int(m.group(2).replace("−", "-"))
            digits = len(re.sub(r"\D", "", s_num).lstrip("0"))
            unit = m.group(3) or ""
            f, base = split_unit(unit) if unit else (1.0, "")
            known_unit = base in BASES or unit in UNIT_EXTRA
            if not known_unit:
                unit, f, base = "", 1.0, ""
            if not unit and (digits < 3 and "," not in s_num and "." not in s_num):
                continue
            if 1900 <= num <= 2100 and not unit:
                continue
            val = num * f
            dec = len(s_num.replace(",", ".").split(".")[1]) if ("," in s_num or "." in s_num) else 0
            tol = 0.5 * 10 ** (-dec) * f * (10 ** int(m.group(2).replace("−", "-")) if m.group(2) else 1)
            ins = self._value_insert(b, s_num, unit) if not m.group(2) else None
            if ins:
                span = text[m.start():m.end()].rstrip() if unit else m.group(1)
                fx = self._abs_fix(b, nd.s + m.start(), nd.s + m.start() + len(span), ins, expect=span)
                ck = ins[2:].split(".", 1)[0]
                self.add("warning", "number-typed", f"число «{span}» совпадает с выходом расчёта «{self.p.calcs[ck].name}»",
                         b, nd.s + m.start(), hint=f"вставьте как значение расчёта: {ins}", fix=fx)
                continue
            if val == 0:
                continue
            hp = self.p.heading_path.get(id(b), "")
            for v, vb, ck, path in cands:
                if vb and base != vb:
                    continue
                if hp not in self.calc_sections.get(ck, set()):
                    continue   # число далеко от рисунков этого расчёта — вероятно, совпадение
                if abs(v - val) <= max(tol, 1e-12 * abs(v)) and abs(v - val) <= 0.05 * abs(val):
                    self.add("warning", "number-typed", f"число «{m.group(0).strip()}» совпадает с выходом расчёта «{self.p.calcs[ck].name}» ({path})",
                             b, nd.s + m.start(), hint=f"вставьте как значение расчёта: {{={ck}.{path}}} (lde list values {ck})")
                    break

    # ------------------------------------------------------------ calcs ----
    def check_calcs(self):
        for line in stale_summary(self.p, self.st):
            self.add("error", "stale", line["message"], file=line.get("file", ""), line=line.get("line", 0),
                     hint="lde update — пересчитать устаревшее")
        for k, s in self.st.items():
            if s.failure:
                c = self.p.calcs[k]
                self.add("error", "calc-failed", f"расчёт «{c.name}» упал: {s.failure.get('error')}",
                         file=f"calc/{k}/calc.toml", hint=f"журнал: {s.failure.get('log')}")


def _ranges(nums: list[str]) -> str:
    """['14','15','16'] → '14–16'; ['3','5'] → '3, 5'."""
    try:
        ints = sorted(set(int(x) for x in nums))
    except ValueError:
        return ", ".join(nums)
    out, start, prev = [], ints[0], ints[0]
    for x in ints[1:] + [None]:
        if x is not None and x == prev + 1:
            prev = x
            continue
        out.append(f"{start}–{prev}" if prev - start >= 1 else str(start))
        if x is not None:
            start = prev = x
    return ", ".join(out)


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def stale_summary(p: Project, statuses: dict) -> list[dict]:
    """«Рисунки 14–15 и 3 числа в п. 5.2 устарели: изменён параметр PRF (расчёт «…»)»."""
    out = []
    for k, s in statuses.items():
        if s.state == "ok":
            continue
        c = p.calcs[k]
        figs, tabs, nvals, where = [], [], 0, []
        for b in p.blocks:
            deps = p.block_calcs(b)
            if k not in deps:
                continue
            if b.kind == "figure":
                figs.append(p.numbers.get(id(b), "?"))
            elif b.kind == "table" and (b.attrs.get("file", "").startswith(f"calc/{k}/")):
                tabs.append(p.numbers.get(id(b), "?"))
            cnt = sum(1 for nd in p.inline_nodes(b) if nd.k == "val" and nd.v.split(".", 1)[0] == k)
            if cnt:
                nvals += cnt
                hp = p.heading_path.get(id(b), "")
                if hp and hp not in where:
                    where.append(hp)
        parts = []
        if figs:
            parts.append((("Рисунок " if len(figs) == 1 else "Рисунки ") + _ranges(figs)))
        if tabs:
            parts.append((("таблица " if len(tabs) == 1 else "таблицы ") + _ranges(tabs)))
        if nvals:
            wh = f" в п. {', '.join(where)}" if where else ""
            parts.append(f"{nvals} {_plural(nvals, 'число', 'числа', 'чисел')}{wh}")
        what = " и ".join([", ".join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts) if parts else "Выходы"
        what = what[0].upper() + what[1:]
        verb = "устарел" if (len(figs) + len(tabs) == 1 and not nvals and figs) else "устарели"
        if s.state == "never":
            msg = f"{what}: расчёт «{c.name}» ещё не запускался"
        else:
            msg = f"{what} {verb}: {'; '.join(s.reasons)} (расчёт «{c.name}»)"
        out.append({"calc": k, "message": msg, "figures": figs, "tables": tabs, "values": nvals,
                    "file": f"calc/{k}/calc.toml", "state": s.state, "reasons": s.reasons})
    return out


def run_check(proj: Project, statuses=None) -> list[Issue]:
    return Checker(proj, statuses).run()
