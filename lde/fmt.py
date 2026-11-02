"""Форматирование чисел из расчётов: десятичная запятая, округление, приставки СИ, единица.

Опции вставки {=calc.путь опции…}:
    км, МГц, мкс …   перевести в эту единицу (только приставки СИ к той же единице)
    .2               два знака после запятой
    .3g              три значащие цифры
    -                без единицы
    e                всегда в виде m·10^n
"""
from __future__ import annotations

import math
import re

PREFIXES = {"п": 1e-12, "н": 1e-9, "мк": 1e-6, "м": 1e-3, "с": 1e-2, "к": 1e3, "М": 1e6, "Г": 1e9, "Т": 1e12}
AUTO_PREFIX = {-12: "п", -9: "н", -6: "мк", -3: "м", 3: "к", 6: "М", 9: "Г", 12: "Т"}
BASES = ["Гц", "Вт", "Па", "Дж", "Ом", "бит", "Гн", "Кл", "Тл", "Вб", "См", "м", "с", "В", "А", "Ф", "Н", "г", "Б"]
NBSP = " "
THIN = " "
MINUS = "−"


class FormatError(ValueError):
    pass


def split_unit(u: str):
    """'МГц' → (1e6, 'Гц'); 'м' → (1, 'м'); неизвестное → (1, u)."""
    for b in BASES:
        if u == b:
            return 1.0, b
    for b in BASES:
        if u.endswith(b):
            pre = u[: -len(b)]
            if pre in PREFIXES:
                if b == "с" and pre == "с":
                    continue
                return PREFIXES[pre], b
    return 1.0, u


def parse_opts(opts: str) -> dict:
    o = {"unit": None, "decimals": None, "sig": None, "nounit": False, "sci": False}
    for t in opts.split():
        if t == "-":
            o["nounit"] = True
        elif t == "e":
            o["sci"] = True
        elif re.fullmatch(r"\.\d+g", t):
            o["sig"] = int(t[1:-1])
        elif re.fullmatch(r"\.\d+", t):
            o["decimals"] = int(t[1:])
        else:
            o["unit"] = t
    return o


def _group_int(s: str, sep: str) -> str:
    if len(s) <= 4:
        return s
    out = []
    while len(s) > 3:
        out.insert(0, s[-3:])
        s = s[:-3]
    out.insert(0, s)
    return sep.join(out)


def _num_str(x: float, decimals: int | None, sig: int | None):
    """Строка мантиссы без знака, с точкой; без экспоненты."""
    if decimals is not None:
        s = f"{abs(x):.{decimals}f}"
    else:
        sig = sig or 4
        if x == 0:
            return "0"
        mag = math.floor(math.log10(abs(x)))
        dec = max(0, sig - 1 - mag)
        s = f"{abs(x):.{dec}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    return s


def format_value(v, unit: str = "", digits=None, opts: str = "") -> dict:
    """Вернуть {'text': юникод для показа/DOCX-плана, 'latex': для XeLaTeX, 'unit': итоговая единица}."""
    o = parse_opts(opts)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        s = str(v)
        return {"text": s, "latex": latex_escape(s), "unit": unit}
    x = float(v)
    is_int = isinstance(v, int) or (x.is_integer() and abs(x) < 1e15 and digits in (None, 0)
                                    and o["decimals"] is None and o["sig"] is None)
    target = o["unit"]
    u = unit or ""
    if target is not None and target != u:
        f_src, b_src = split_unit(u) if u else (1.0, "")
        f_dst, b_dst = split_unit(target)
        if not u:
            u = target            # у числа нет единицы — просто подпись
        elif b_src == b_dst and b_src in BASES:
            x = x * f_src / f_dst
            u = target
            is_int = x.is_integer() and abs(x) < 1e15 and o["decimals"] is None and o["sig"] is None
        else:
            raise FormatError(f"не могу перевести «{u}» в «{target}»")
    elif target is None and u and not o["sci"] and x != 0:
        # авто-приставка: 3e9 Гц → 3 ГГц, 5e-6 с → 5 мкс (только для единиц СИ без приставки)
        f_src, b_src = split_unit(u)
        if f_src == 1.0 and b_src in BASES and b_src not in ("г", "Б", "бит"):
            e3 = int(math.floor(math.log10(abs(x)) / 3) * 3)
            if (e3 >= 6 or e3 <= -3) and e3 in AUTO_PREFIX and not (b_src == "м" and e3 == -3 and abs(x) >= 1e-2):
                x = x / 10 ** e3
                u = AUTO_PREFIX[e3] + b_src
                is_int = abs(x - round(x)) < 1e-9 * max(1, abs(x))
                if is_int:
                    x = float(round(x))
    decimals = o["decimals"] if o["decimals"] is not None else (digits if isinstance(digits, int) else None)
    neg = x < 0
    exp = 0
    if o["sci"] or (not is_int and x != 0 and (abs(x) >= 1e6 or abs(x) < 1e-3)) or (is_int and abs(x) >= 1e9):
        exp = int(math.floor(math.log10(abs(x)))) if x != 0 else 0
        x = x / 10 ** exp
        is_int = False
    if is_int and decimals is None:
        mant = str(int(round(abs(x))))
    else:
        mant = _num_str(x, decimals, o["sig"])
    ip, _, fp = mant.partition(".")
    text = _group_int(ip, THIN) + ("," + fp if fp else "")
    latex = _group_int(ip, r"\,") + ("{,}" + fp if fp else "")
    if neg:
        text = MINUS + text
        latex = r"\textminus{}" + latex
    if exp:
        sup = str(exp).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
        if mant == "1":
            text, latex = ("−" if neg else "") + "10" + sup, (r"\textminus{}" if neg else "") + rf"\ensuremath{{10^{{{exp}}}}}"
        else:
            text += "·10" + sup
            latex += rf"\ensuremath{{\cdot 10^{{{exp}}}}}"
    if u and not o["nounit"]:
        text += NBSP + u
        latex += "~" + latex_escape(u)
    return {"text": text, "latex": latex, "unit": "" if o["nounit"] else u}


_TEX_SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
                "#": r"\#", "%": r"\%", "_": r"\_", "^": r"\^{}", "~": r"\textasciitilde{}",
                " ": "~", " ": r"\,"}

_TEX_SPECIAL.update({"\u2011": r"\mbox{-}", "\u00ad": r"\-", "\u2009": r"\,"})


def latex_escape(s: str) -> str:
    return "".join(_TEX_SPECIAL.get(c, c) for c in s)
