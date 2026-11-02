"""Парсер «LDE-текста» — формата исходника разделов (text/*.md).

Блоки разделяются пустыми строками; особый блок узнаётся по началу строки:

    # Заголовок {#sec:метка}        заголовок (#, ##, ###); {-} — без номера; {.appendix}
    $$ LaTeX $$ {#eq:метка}          выносная формула (с меткой — нумерованная)
    ![Подпись](путь){#fig:метка}     рисунок; ::: figure … ::: — с подрисунками
    : Заголовок {#tab:метка}         таблица: строка заголовка + строки «| … |» (или file=…csv)
    - пункт / 1. пункт / а) пункт    перечисление
    ```язык … ```                    листинг
    <!-- … -->                       комментарий (в документ не попадает)
    ::: pagebreak                    принудительный разрыв страницы
    всё остальное                    абзац

Вставки внутри текста:

    {R_ц}            величина (объявлена в quantities.toml)
    {=csa.R0 км}     значение из расчёта (calc/csa), опции: единица, .N знаков, - без единицы
    @fig:x @tab:x @eq:x @sec:x @app:x   номер объекта (для формулы — «(N)»)
    [@key] [@key, с. 45] [@a; @b]       ссылка на источник
    $…$              формула LaTeX в строке
    **жирный**  *курсив*  \\{ \\$ \\@ \\*   экранирование

Парсер ничего не исправляет и не нормализует: он только размечает исходник и запоминает
номера строк, чтобы линтер и Studio могли указывать место и править блок минимально.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ inline ----

REF_KINDS = ("fig", "tab", "eq", "sec", "app")
LABEL_RE = r"[\w\-]+(?:[.:][\w\-]+)*"
_REF_RE = re.compile(r"@(fig|tab|eq|sec|app):(" + LABEL_RE + ")")
_QKEY_RE = re.compile(r"\{([^\s{}=\\][^\s{}]*)\}")
_VAL_RE = re.compile(r"\{=([^\s{}]+)((?:\s+[^\s{}]+)*)\s*\}")
_CITE_ITEM_RE = re.compile(r"\s*@(" + LABEL_RE + r")\s*(?:,\s*([^;\]]*?))?\s*$")
ESCAPABLE = set("{}$@*[]\\_~#|")


@dataclass
class Node:
    k: str              # text | math | q | val | ref | cite | b | i
    s: int              # смещение начала в тексте блока
    e: int              # смещение конца (не включительно)
    v: str = ""         # текст / LaTeX / ключ величины / метка / путь значения
    opts: str = ""      # val: опции форматирования
    items: list = field(default_factory=list)  # cite: [(key, locator)]; b/i: дочерние узлы

    def to_dict(self) -> dict:
        d = {"k": self.k, "s": self.s, "e": self.e, "v": self.v}
        if self.opts:
            d["opts"] = self.opts
        if self.k == "cite":
            d["items"] = [list(x) for x in self.items]
        elif self.items:
            d["items"] = [n.to_dict() for n in self.items]
        return d


def _find_closing(s: str, i: int, ch: str) -> int:
    """Индекс следующего неэкранированного ch начиная с i, или -1."""
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == ch:
            return i
        i += 1
    return -1


def _match_bracket(s: str, i: int, open_ch="[", close_ch="]") -> int:
    """s[i] == open_ch; вернуть индекс парной закрывающей скобки или -1."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "$":  # не считаем скобки внутри формул
            k = _find_closing(s, j + 1, "$")
            if k < 0:
                return -1
            j = k + 1
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def parse_inline(s: str, base: int = 0) -> list[Node]:
    """Разобрать текст абзаца/подписи/ячейки в список узлов. Смещения — от base."""
    out: list[Node] = []
    buf: list[str] = []
    buf_start = 0
    i = 0
    n = len(s)

    def flush(upto: int):
        nonlocal buf, buf_start
        if buf:
            out.append(Node("text", base + buf_start, base + upto, "".join(buf)))
            buf = []

    def start_text(at: int):
        nonlocal buf_start
        if not buf:
            buf_start = at

    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in ESCAPABLE:
            start_text(i)
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "\\" and i + 1 < n and s[i + 1].isalpha():
            # сырая команда LaTeX в тексте: оставляем как текст целиком (линтер о ней сообщит),
            # чтобы её аргумент {…} не принять за вставку величины
            m = re.match(r"\\[A-Za-z]+\*?", s[i:])
            j = i + m.end()
            while j < n and s[j] == "{":
                k = _match_bracket(s, j, "{", "}")
                if k < 0:
                    break
                j = k + 1
            start_text(i)
            buf.append(s[i:j])
            i = j
            continue
        if c == "\n":
            start_text(i)
            buf.append(" ")
            i += 1
            continue
        if c == "$":
            j = _find_closing(s, i + 1, "$")
            if j > i + 1:
                flush(i)
                out.append(Node("math", base + i, base + j + 1, s[i + 1:j]))
                i = j + 1
                continue
        if c == "{":
            m = _VAL_RE.match(s, i)
            if m:
                flush(i)
                out.append(Node("val", base + i, base + m.end(), m.group(1), m.group(2).strip()))
                i = m.end()
                continue
            m = _QKEY_RE.match(s, i)
            if m:
                flush(i)
                out.append(Node("q", base + i, base + m.end(), m.group(1)))
                i = m.end()
                continue
        if c == "[" and s.startswith("[@", i):
            j = _match_bracket(s, i)
            if j > 0:
                inner = s[i + 1:j]
                items = []
                ok = True
                for part in inner.split(";"):
                    m = _CITE_ITEM_RE.match(part)
                    if not m:
                        ok = False
                        break
                    items.append((m.group(1), (m.group(2) or "").strip()))
                if ok and items:
                    flush(i)
                    out.append(Node("cite", base + i, base + j + 1, items=items))
                    i = j + 1
                    continue
        if c == "@" and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in "@_")):
            m = _REF_RE.match(s, i)
            if m:
                flush(i)
                out.append(Node("ref", base + i, base + m.end(), f"{m.group(1)}:{m.group(2)}"))
                i = m.end()
                continue
        if c == "*":
            if s.startswith("**", i):
                j = s.find("**", i + 2)
                if j > i + 2:
                    flush(i)
                    out.append(Node("b", base + i, base + j + 2, items=parse_inline(s[i + 2:j], base + i + 2)))
                    i = j + 2
                    continue
            else:
                j = _find_closing(s, i + 1, "*")
                if j > i + 1 and not s.startswith("**", j) and not s[i + 1].isspace():
                    flush(i)
                    out.append(Node("i", base + i, base + j + 1, items=parse_inline(s[i + 1:j], base + i + 1)))
                    i = j + 1
                    continue
        start_text(i)
        buf.append(c)
        i += 1
    flush(n)
    return out


def plain_text(nodes: list[Node]) -> str:
    """Текст без разметки (вставки — их исходник); для поиска и линтера."""
    parts = []
    for nd in nodes:
        if nd.k == "text":
            parts.append(nd.v)
        elif nd.k in ("b", "i"):
            parts.append(plain_text(nd.items))
        else:
            parts.append(" ")
    return "".join(parts)


def walk(nodes: list[Node]):
    for nd in nodes:
        yield nd
        if nd.k in ("b", "i"):
            yield from walk(nd.items)


# ------------------------------------------------------------------ blocks ----

@dataclass
class Block:
    kind: str                 # heading|para|formula|figure|table|list|code|comment|pagebreak
    file: str                 # путь файла относительно проекта
    line: int                 # первая строка (с 1)
    end: int                  # последняя строка (включительно)
    src: str                  # исходный текст блока
    label: str = ""
    attrs: dict = field(default_factory=dict)
    classes: list = field(default_factory=list)
    level: int = 0
    text: str = ""            # текстовая часть (заголовок/абзац/подпись) — как в исходнике
    text_off: int = 0         # смещение text внутри src
    inl: list = field(default_factory=list)
    math: str = ""
    path: str = ""
    subfigs: list = field(default_factory=list)   # [(подпись-узлы, путь, attrs, текст)]
    items: list = field(default_factory=list)     # list: [(уровень, маркер, текст, смещение, узлы)]
    ordered: bool = False
    header: list = field(default_factory=list)    # table: ячейки заголовка [(текст, смещение, узлы)]
    rows: list = field(default_factory=list)      # table: строки
    aligns: list = field(default_factory=list)
    lang: str = ""
    errors: list = field(default_factory=list)    # ошибки разбора [(строка, сообщение)]
    unnumbered: bool = False
    index: int = 0            # порядковый номер блока в файле
    style: dict = field(default_factory=dict)     # ручное оформление (строка {style …} над блоком)
    style_len: int = 0        # длина строки {style …} с переводом строки (0 — оформление автоматическое)

    @property
    def uid(self) -> str:
        """Идентификатор для Studio: метка, если есть, иначе файл#порядковый номер."""
        return self.label or f"{self.file}#{self.index}"

    def line_of(self, off: int) -> int:
        """Номер строки файла для смещения внутри src."""
        return self.line + self.src.count("\n", 0, max(0, off))


_ATTR_TOKEN = re.compile(r'(#[^\s{}]+|\.[\w\-]+|-|[\w\-]+=(?:"[^"]*"|[^\s{}"]+))')


def parse_attrs(text: str):
    """'{#fig:x width=0.8 .appendix -}' (без скобок) → (label, attrs, classes, unnumbered) или None."""
    toks = text.split()
    if not toks:
        return None
    label, attrs, classes, unnum = "", {}, [], False
    for t in toks:
        if not _ATTR_TOKEN.fullmatch(t):
            return None
        if t.startswith("#"):
            label = t[1:]
        elif t == "-":
            unnum = True
        elif t.startswith("."):
            classes.append(t[1:])
            if t == ".unnumbered":
                unnum = True
        else:
            k, v = t.split("=", 1)
            attrs[k] = v.strip('"')
    return label, attrs, classes, unnum


_TRAIL_ATTR = re.compile(r"\s*\{([^{}]*)\}\s*$")


def split_trailing_attrs(s: str):
    """Отделить хвостовые атрибуты {…}. Вернуть (текст, (label, attrs, classes, unnum)|None)."""
    m = _TRAIL_ATTR.search(s)
    if m:
        a = parse_attrs(m.group(1))
        if a is not None:
            return s[:m.start()], a
    return s, None


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)(-|\*|\d+[.)]|[а-яa-z]\))\s+(.*)$")
_STARTS_BLOCK = re.compile(r"^(#{1,3}\s|\$\$|!\[|```|:::|<!--|\||\{style[\s}]|<<<<<<< )")


def _is_list_line(line: str) -> bool:
    m = _LIST_RE.match(line)
    return bool(m) and len(m.group(1)) < 8


def split_table_row(line: str) -> list[tuple[str, int]]:
    """Разбить '| a | b |' на ячейки с учётом $…$ и \\|. Вернуть [(текст, смещение)]."""
    s = line
    cells = []
    i = 0
    n = len(s)
    # пропустить ведущие пробелы и первый |
    while i < n and s[i] == " ":
        i += 1
    if i < n and s[i] == "|":
        i += 1
    start = i
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "$":
            j = _find_closing(s, i + 1, "$")
            if j > 0:
                i = j + 1
                continue
        if c == "|":
            cells.append((s[start:i], start))
            start = i + 1
        i += 1
    tail = s[start:]
    if tail.strip():
        cells.append((tail, start))
    out = []
    for txt, off in cells:
        lead = len(txt) - len(txt.lstrip())
        out.append((txt.strip(), off + lead))
    return out


def _parse_figure_line(line: str):
    """'![подпись](путь){attrs}' → (подпись, смещение подписи, путь, attrs-tuple) или None."""
    st = line.lstrip()
    lead = len(line) - len(st)
    if not st.startswith("!["):
        return None
    j = _match_bracket(st, 1)
    if j < 0 or j + 1 >= len(st) or st[j + 1] != "(":
        return None
    k = st.find(")", j + 2)
    if k < 0:
        return None
    caption = st[2:j]
    path = st[j + 2:k].strip()
    rest = st[k + 1:]
    attrs = None
    if rest.strip():
        m = re.fullmatch(r"\s*\{([^{}]*)\}\s*", rest)
        if not m:
            return None
        attrs = parse_attrs(m.group(1))
        if attrs is None:
            return None
    return caption, lead + 2, path, attrs


def _shift_block(blk: Block, d: int):
    """Сдвинуть смещения узлов блока на d (над блоком добавлена строка {style …})."""
    def nodes(lst):
        for nd in walk(lst):
            nd.s += d
            nd.e += d
    blk.text_off += d
    nodes(blk.inl)
    blk.items = [(lvl, mark, t, o + d, nn) for lvl, mark, t, o, nn in blk.items]
    for _, _, _, _, nn in blk.items:
        nodes(nn)
    for cells in [blk.header] + list(blk.rows):
        for c in cells:
            nodes(c[2])
    blk.header = [(t, o + d, nn) for t, o, nn in blk.header]
    blk.rows = [[(t, o + d, nn) for t, o, nn in r] for r in blk.rows]
    for sf in blk.subfigs:
        nodes(sf[0])


def parse_text(text: str, file: str = "") -> list[Block]:
    from lde import style as stylemod
    lines = text.split("\n")
    blocks: list[Block] = []
    styles: dict[int, int] = {}      # строка блока → строка {style …} над ним
    i = 0
    n = len(lines)

    def mk(kind, a, b, **kw):
        src = "\n".join(lines[a:b + 1])
        blk = Block(kind=kind, file=file, line=a + 1, end=b + 1, src=src, **kw)
        blk.index = len(blocks)
        blocks.append(blk)
        return blk

    def apply_attrs(blk: Block, a):
        if a:
            blk.label, blk.attrs, blk.classes, blk.unnumbered = a[0], a[1], a[2], a[3]

    while i < n:
        line = lines[i]
        st = line.strip()
        if not st:
            i += 1
            continue
        # --- ручное оформление следующего блока
        if stylemod.is_style_line(st):
            if i + 1 < n and lines[i + 1].strip() and not stylemod.is_style_line(lines[i + 1]):
                styles[i + 1] = i
            else:
                blk = mk("comment", i, i)
                blk.errors.append((i + 1, "строка оформления {style …} должна стоять прямо над блоком, без пустой строки"))
            i += 1
            continue
        # --- конфликт слияния (lde/merge.py): до строки «>>>>>>> …»
        if line.startswith("<<<<<<< "):
            j = i + 1
            while j < n and not lines[j].startswith(">>>>>>> "):
                j += 1
            blk = mk("conflict", i, min(j, n - 1))
            if j >= n:
                blk.errors.append((i + 1, "конфликт слияния не закрыт строкой «>>>>>>> …»"))
            i = j + 1
            continue
        # --- комментарий
        if st.startswith("<!--"):
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            j = min(j, n - 1)
            mk("comment", i, j)
            i = j + 1
            continue
        # --- листинг
        if st.startswith("```"):
            j = i + 1
            while j < n and not lines[j].strip().startswith("```"):
                j += 1
            blk = mk("code", i, min(j, n - 1), lang=st[3:].strip())
            blk.text = "\n".join(lines[i + 1:j])
            if j >= n:
                blk.errors.append((i + 1, "листинг не закрыт строкой ```"))
            i = j + 1
            continue
        # --- разрыв страницы
        if st == "::: pagebreak":
            mk("pagebreak", i, i)
            i += 1
            continue
        # --- рисунок с подрисунками
        if re.match(r"^:::\s*figure\b", st):
            j = i + 1
            while j < n and lines[j].strip() != ":::":
                j += 1
            blk = mk("figure", i, min(j, n - 1))
            _, a = split_trailing_attrs(st)
            apply_attrs(blk, a)
            cap_lines = []
            src_off = len(lines[i]) + 1
            for k in range(i + 1, min(j, n)):
                ln = lines[k]
                fig = _parse_figure_line(ln)
                if fig:
                    cap, coff, path, fa = fig
                    blk.subfigs.append((parse_inline(cap, src_off + coff), path, (fa[1] if fa else {}), cap))
                elif ln.strip():
                    cap_lines.append((ln, src_off))
                src_off += len(ln) + 1
            if cap_lines:
                blk.text = " ".join(x[0].strip() for x in cap_lines)
                blk.text_off = cap_lines[0][1] + (len(cap_lines[0][0]) - len(cap_lines[0][0].lstrip()))
                blk.inl = parse_inline(blk.text, blk.text_off) if len(cap_lines) == 1 else parse_inline(blk.text)
            if j >= n:
                blk.errors.append((i + 1, "блок ::: figure не закрыт строкой :::"))
            if not blk.subfigs:
                blk.errors.append((i + 1, "в блоке ::: figure нет ни одного изображения ![…](…)"))
            i = j + 1
            continue
        # --- выносная формула
        if st.startswith("$$"):
            joined_start = line.index("$$")
            # ищем закрывающее $$
            j = i
            close_line, close_col = -1, -1
            col = joined_start + 2
            while j < n:
                pos = lines[j].find("$$", col if j == i else 0)
                if pos >= 0:
                    close_line, close_col = j, pos
                    break
                j += 1
            if close_line < 0:
                blk = mk("formula", i, n - 1)
                blk.errors.append((i + 1, "формула не закрыта: нет второго $$"))
                blk.math = "\n".join(lines[i:])[joined_start + 2:]
                i = n
                continue
            body_lines = []
            for k in range(i, close_line + 1):
                ln = lines[k]
                a = joined_start + 2 if k == i else 0
                b = close_col if k == close_line else len(ln)
                body_lines.append(ln[a:b])
            blk = mk("formula", i, close_line)
            blk.math = "\n".join(body_lines).strip()
            tail = lines[close_line][close_col + 2:]
            if tail.strip():
                a = parse_attrs(tail.strip()[1:-1]) if re.fullmatch(r"\s*\{[^{}]*\}\s*", tail) else None
                if a is None:
                    blk.errors.append((close_line + 1, f"непонятный хвост после формулы: «{tail.strip()}» (ожидается {{#eq:метка}})"))
                apply_attrs(blk, a)
            i = close_line + 1
            continue
        # --- заголовок
        m = _HEADING_RE.match(line)
        if m:
            blk = mk("heading", i, i, level=len(m.group(1)))
            title, a = split_trailing_attrs(m.group(2))
            apply_attrs(blk, a)
            blk.text = title.strip()
            blk.text_off = line.index(m.group(2))
            blk.inl = parse_inline(blk.text, blk.text_off)
            i += 1
            continue
        # --- рисунок
        fig = _parse_figure_line(line)
        if fig:
            cap, coff, path, a = fig
            blk = mk("figure", i, i, path=path)
            apply_attrs(blk, a)
            blk.text = cap
            blk.text_off = coff
            blk.inl = parse_inline(cap, coff)
            i += 1
            continue
        # --- таблица
        if st.startswith("|") or (st.startswith(": ") and (i + 1 >= n or lines[i + 1].strip().startswith("|") or "file=" in st)):
            j = i
            while j + 1 < n and lines[j + 1].strip().startswith("|"):
                j += 1
            blk = mk("table", i, j)
            k = i
            off = 0
            if st.startswith(": "):
                cap, a = split_trailing_attrs(line.strip()[2:])
                apply_attrs(blk, a)
                blk.text = cap.strip()
                blk.text_off = line.index(": ") + 2
                blk.inl = parse_inline(blk.text, blk.text_off)
                off = len(line) + 1
                k = i + 1
            else:
                blk.errors.append((i + 1, "у таблицы нет заголовка: добавьте строку «: Заголовок {#tab:метка}» над таблицей"))
            rows = []
            for r in range(k, j + 1):
                ln = lines[r]
                cells = [(t, off + o, parse_inline(t, off + o)) for t, o in split_table_row(ln)]
                rows.append((r, cells))
                off += len(ln) + 1
            sep_idx = None
            for idx, (r, cells) in enumerate(rows):
                if cells and all(re.fullmatch(r":?-{2,}:?", c[0] or "") for c in cells):
                    sep_idx = idx
                    break
            if sep_idx is not None:
                blk.aligns = ["c" if c[0].startswith(":") and c[0].endswith(":") else "r" if c[0].endswith(":") else "l"
                              for c in rows[sep_idx][1]]
                blk.header = rows[sep_idx - 1][1] if sep_idx > 0 else []
                blk.rows = [r_[1] for r_ in rows[sep_idx + 1:]]
            else:
                blk.rows = [r_[1] for r_ in rows]
            if "file" in blk.attrs and rows:
                blk.errors.append((i + 1, "у таблицы одновременно file=… и строки | … |: оставьте что-то одно"))
            i = j + 1
            continue
        # --- перечисление
        if _is_list_line(line):
            j = i
            while j + 1 < n and lines[j + 1].strip() and not _STARTS_BLOCK.match(lines[j + 1].lstrip()):
                j += 1
            blk = mk("list", i, j)
            off = 0
            cur = None
            for r in range(i, j + 1):
                ln = lines[r]
                lm = _LIST_RE.match(ln)
                if lm and len(lm.group(1)) < 8:
                    lvl = 1 if len(lm.group(1)) < 2 else 2
                    toff = off + lm.start(3)
                    cur = [lvl, lm.group(2), lm.group(3), toff]
                    blk.items.append(cur)
                elif cur is not None:
                    cur[2] += "\n" + ln.strip()
                off += len(ln) + 1
            first = blk.items[0][1] if blk.items else "-"
            blk.ordered = first not in ("-", "*")
            blk.items = [(lvl, mark, t, o, parse_inline(t, o)) for lvl, mark, t, o in blk.items]
            i = j + 1
            continue
        # --- абзац
        j = i
        while j + 1 < n and lines[j + 1].strip() and not _STARTS_BLOCK.match(lines[j + 1].lstrip()) \
                and not _is_list_line(lines[j + 1]) and not _parse_figure_line(lines[j + 1]):
            j += 1
        blk = mk("para", i, j)
        blk.text = blk.src
        blk.text_off = 0
        blk.inl = parse_inline(blk.text, 0)
        i = j + 1
    # строка {style …} входит в блок: переносится, удаляется и откатывается вместе с ним
    for blk in blocks:
        si = styles.get(blk.line - 1)
        if si is None:
            continue
        sline = lines[si]
        st_, errs = stylemod.parse(sline, blk.kind)
        blk.style = st_
        blk.style_len = len(sline) + 1
        _shift_block(blk, blk.style_len)
        blk.src = sline + "\n" + blk.src
        blk.line = si + 1
        for e in errs:
            blk.errors.append((si + 1, e))
    return blocks


# ------------------------------------------------------------- serializing ----

def replace_block(text: str, blk: Block, new_src: str) -> str:
    """Заменить строки блока в тексте файла, не трогая остальное."""
    lines = text.split("\n")
    new_lines = new_src.split("\n") if new_src != "" else []
    lines[blk.line - 1:blk.end] = new_lines
    return "\n".join(lines)


def insert_after(text: str, blk: Block | None, new_src: str) -> str:
    """Вставить новый блок после blk (или в конец), с пустой строкой-разделителем."""
    lines = text.split("\n")
    pos = blk.end if blk else len(lines)
    ins = [""] + new_src.split("\n")
    if pos < len(lines) and lines[pos].strip():
        ins.append("")
    lines[pos:pos] = ins
    out = "\n".join(lines)
    return out
