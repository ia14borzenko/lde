"""Ручное оформление блока: строка `{style …}` прямо над блоком.

    {style align=center indent=0 before=6 size=12 bold=no}
    Абзац, который нужно оформить не так, как ставит LDE.

Без такой строки блок оформляется автоматически (ГОСТ/профиль). Строка — часть блока (входит в
его исходник), поэтому переносится, удаляется и откатывается вместе с ним. Одни и те же параметры
применяются в PDF и в DOCX.

Значения: размеры — числа (запятая или точка), `before`/`after` — добавка к обычному интервалу в pt
(может быть отрицательной), `line` — множитель как в Word (1; 1,15; 1,5; 2), `size` — кегль в pt.
"""
from __future__ import annotations

import re

# ключ: (тип, допустимое, подпись)
KEYS: dict[str, tuple] = {
    "align": ("choice", ("justify", "left", "center", "right"), "выравнивание"),
    "indent": ("num", (-5.0, 10.0), "отступ первой строки, см"),
    "left": ("num", (0.0, 10.0), "отступ слева, см"),
    "right": ("num", (0.0, 10.0), "отступ справа, см"),
    "before": ("num", (-60.0, 200.0), "интервал перед, pt (добавка)"),
    "after": ("num", (-60.0, 200.0), "интервал после, pt (добавка)"),
    "line": ("num", (0.5, 4.0), "межстрочный интервал, множитель"),
    "size": ("num", (6.0, 40.0), "размер шрифта, pt"),
    "bold": ("bool", None, "полужирный"),
    "italic": ("bool", None, "курсив"),
    "newpage": ("bool", None, "с новой страницы"),
    "keep": ("bool", None, "не отрывать от следующего"),
}
ORDER = list(KEYS)

_ALL_TEXT = ["align", "indent", "left", "right", "before", "after", "line", "size", "bold", "italic", "newpage", "keep"]
ALLOWED: dict[str, list[str]] = {
    "para": _ALL_TEXT,
    "heading": _ALL_TEXT,
    "list": ["indent", "before", "after", "line", "size", "bold", "italic", "newpage", "keep"],
    "formula": ["before", "after", "size"],
    "figure": ["align", "before", "after", "size", "newpage"],
    "table": ["before", "after", "size", "newpage"],
    "code": ["before", "after", "size", "newpage"],
}

LINE_RE = re.compile(r"^\{style(?:\s+([^{}]*))?\}\s*$")
_TRUE = {"yes", "true", "1", "да", "on"}
_FALSE = {"no", "false", "0", "нет", "off"}


def is_style_line(line: str) -> bool:
    return bool(LINE_RE.match(line.strip()))


def parse(line: str, kind: str | None = None) -> tuple[dict, list[str]]:
    """'{style align=center bold}' → ({'align': 'center', 'bold': True}, [ошибки])."""
    m = LINE_RE.match(line.strip())
    if not m:
        return {}, ["не строка оформления {style …}"]
    st, errs = {}, []
    for tok in (m.group(1) or "").split():
        k, _, v = tok.partition("=")
        k = k.strip().lower()
        if k not in KEYS:
            errs.append(f"неизвестный параметр оформления «{k}» (есть: {', '.join(ORDER)})")
            continue
        typ, rng, _title = KEYS[k]
        if typ == "bool":
            if v == "" or v.lower() in _TRUE:
                st[k] = True
            elif v.lower() in _FALSE:
                st[k] = False
            else:
                errs.append(f"{k}={v}: ожидается yes или no")
        elif typ == "choice":
            if v in rng:
                st[k] = v
            else:
                errs.append(f"{k}={v}: ожидается одно из {', '.join(rng)}")
        else:
            try:
                x = float(v.replace(",", "."))
            except ValueError:
                errs.append(f"{k}={v}: ожидается число")
                continue
            if not rng[0] <= x <= rng[1]:
                errs.append(f"{k}={v}: вне допустимого ({_num(rng[0])} … {_num(rng[1])})")
                continue
            st[k] = x
    if kind and kind in ALLOWED:
        for k in list(st):
            if k not in ALLOWED[kind]:
                errs.append(f"параметр «{k}» не действует для этого блока (можно: {', '.join(ALLOWED[kind])})")
                st.pop(k)
    return st, errs


def _num(x: float) -> str:
    return (f"{x:.3f}".rstrip("0").rstrip(".")) if x != int(x) else str(int(x))


def clean(style: dict | None, kind: str | None = None) -> dict:
    """Проверить словарь из Studio: только известные ключи с допустимыми значениями."""
    if not style:
        return {}
    parts = []
    for k in ORDER:
        if k in style and style[k] is not None and style[k] != "":
            v = style[k]
            typ = KEYS[k][0]
            if typ == "bool":
                parts.append(f"{k}={'yes' if v in (True, 'yes', 'true', 1) else 'no'}")
            elif typ == "num":
                parts.append(f"{k}={_num(float(str(v).replace(',', '.')))}")
            else:
                parts.append(f"{k}={v}")
    st, errs = parse("{style " + " ".join(parts) + "}", kind)
    if errs:
        raise ValueError("; ".join(errs))
    return st


def format_line(style: dict) -> str:
    """Словарь → каноническая строка {style …} (пустой словарь → '')."""
    if not style:
        return ""
    parts = []
    for k in ORDER:
        if k not in style:
            continue
        v = style[k]
        if KEYS[k][0] == "bool":
            parts.append(f"{k}={'yes' if v else 'no'}")
        elif KEYS[k][0] == "num":
            parts.append(f"{k}={_num(v)}")
        else:
            parts.append(f"{k}={v}")
    return "{style " + " ".join(parts) + "}"


ALIGN_RU = {"justify": "по ширине", "left": "по левому краю", "center": "по центру", "right": "по правому краю"}


def describe_ru(style: dict) -> str:
    def val(k, v):
        if isinstance(v, bool):
            return "да" if v else "нет"
        if isinstance(v, float):
            return _num(v)
        return ALIGN_RU.get(v, v)
    return ", ".join(f"{KEYS[k][2]} {val(k, v)}" for k, v in style.items())


# ------------------------------------------------------------------ LaTeX ----
_ALIGN_TEX = {"center": r"\centering", "right": r"\raggedleft", "left": r"\raggedright",
              "justify": r"\leftskip=0pt\rightskip=0pt\parfillskip=0pt plus 1fil\relax"}


def latex_align(st: dict, default: str = "") -> str:
    """Выравнивание + отступы слева/справа + отступ первой строки (порядок важен: \\centering обнуляет
    \\parindent; в Word у абзаца по центру отступа первой строки тоже нет)."""
    out = _ALIGN_TEX.get(st.get("align", ""), default)
    if st.get("align") in ("left", "justify") and "indent" not in st:
        out += r"\setlength{\parindent}{\ldeparindent}"
    if "left" in st:
        out += rf"\addtolength{{\leftskip}}{{{_num(st['left'])}cm}}"
    if "right" in st:
        out += rf"\addtolength{{\rightskip}}{{{_num(st['right'])}cm}}"
    if "indent" in st:
        out += rf"\setlength{{\parindent}}{{{_num(st['indent'])}cm}}"
    return out


def latex_font(st: dict, keep_baseline: bool = False) -> str:
    out = ""
    # \setstretch вызывает \@currsize (\normalsize) — сбросил бы кегль, поэтому раньше \fontsize;
    # \selectfont после \fontsize применяет новый \baselinestretch
    if "line" in st:
        out += rf"\setstretch{{\fpeval{{\ldebasestretch*{_num(st['line'])}/1.5}}}}"
    if "size" in st:
        s = st["size"]
        if keep_baseline:
            out += (rf"\edef\ldesavedbs{{\the\baselineskip}}\fontsize{{{_num(s)}pt}}{{{_num(s * 1.2)}pt}}\selectfont"
                    r"\baselineskip=\ldesavedbs\relax")
        else:
            out += rf"\fontsize{{{_num(s)}pt}}{{{_num(s * 1.2)}pt}}\selectfont"
    if st.get("bold") is True:
        out += r"\bfseries"
    elif st.get("bold") is False:
        out += r"\mdseries\renewcommand{\ldeheadfont}{\mdseries}"
    if st.get("italic") is True:
        out += r"\itshape"
    elif st.get("italic") is False:
        out += r"\upshape"
    return out
