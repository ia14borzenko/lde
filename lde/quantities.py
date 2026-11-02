"""Величины (обозначения): объявление, типографика, текстовые «двойники» для линтера.

quantities.toml:

    ["R_ц"]
    meaning = "наклонная дальность до цели на траверзе"
    unit = "м"
    # latex = 'R_{\\text{ц}}'   — необязательно: явный набор перекрывает правило
    # value = "csa.R0"          — необязательно: значение из расчёта (или число)

Правило набора (русская типографика, ГОСТ 2.321 / традиция): основа латинская или греческая —
курсив; кириллический индекс — прямой; многобуквенный латинский индекс (max, ref) — прямой;
цифры — прямые; штрих — ′.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi", "ϕ": r"\phi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
GREEK_REV = {v: k for k, v in GREEK.items()}
GREEK_REV[r"\phi"] = "φ"
GREEK_REV[r"\epsilon"] = "ε"
PRIMES = "′'"
SUB_DIGITS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def is_cyr(ch: str) -> bool:
    return "\u0400" <= ch <= "\u04ff"


def is_greek(ch: str) -> bool:
    return "\u0370" <= ch <= "\u03ff"


def is_latin(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def script_of(ch: str) -> str:
    if is_cyr(ch):
        return "cyr"
    if is_latin(ch) or is_greek(ch):
        return "lat"
    if ch.isdigit():
        return "digit"
    return "other"


def _runs(s: str):
    """Разбить на прогоны: кириллица (с точками внутри), латиница, цифры, греческие, прочее."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if is_cyr(c):
            j = i
            while j < len(s) and (is_cyr(s[j]) or (s[j] == "." and j + 1 < len(s) and is_cyr(s[j + 1]))):
                j += 1
            out.append(("cyr", s[i:j]))
            i = j
        elif is_latin(c):
            j = i
            while j < len(s) and is_latin(s[j]):
                j += 1
            out.append(("lat", s[i:j]))
            i = j
        elif c.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            out.append(("dig", s[i:j]))
            i = j
        elif is_greek(c):
            out.append(("gr", c))
            i += 1
        elif c in PRIMES:
            out.append(("prime", c))
            i += 1
        else:
            out.append(("other", c))
            i += 1
    return out


def _latex_part(s: str, index: bool) -> str:
    parts = []
    for kind, t in _runs(s):
        if kind == "cyr":
            parts.append(r"\text{" + t + "}")
        elif kind == "lat":
            if index and len(t) > 1:
                parts.append(r"\mathrm{" + t + "}")
            else:
                parts.append(t)
        elif kind == "dig":
            parts.append(t)
        elif kind == "gr":
            parts.append(GREEK[t] + " ")
        elif kind == "prime":
            parts.append("'")
        else:
            parts.append(t)
    out = "".join(parts).replace(" '", "'")
    return out.rstrip()


def latex_from_key(key: str) -> str:
    base, _, idx = key.partition("_")
    lb = _latex_part(base, index=False)
    if not idx:
        return lb
    return f"{lb}_{{{_latex_part(idx, index=True)}}}"


@dataclass
class Quantity:
    key: str
    meaning: str = ""
    unit: str = ""
    latex: str = ""
    value: object = None      # число или "calc.path"
    line: int = 0             # строка объявления в quantities.toml

    @property
    def tex(self) -> str:
        return self.latex or latex_from_key(self.key)

    @property
    def base(self) -> str:
        return self.key.partition("_")[0]

    @property
    def index(self) -> str:
        return self.key.partition("_")[2]

    def text_variants(self) -> list[str]:
        """Как эту величину набирают обычным текстом (для линтера)."""
        b, ix = self.base, self.index
        bases = {b, b.replace("′", "'"), b.replace("'", "′")}
        out = set()
        for bb in bases:
            if not ix:
                out.add(bb)
                continue
            ixs = {ix, ix.replace("′", "'")}
            for x in ixs:
                out.update({bb + x, f"{bb}_{x}", f"{bb}_{{{x}}}", f"{bb}_({x})"})
                if x.isdigit():
                    out.add(bb + x.translate(SUB_DIGITS))
        return sorted(out, key=len, reverse=True)


def norm_math(tex: str) -> str:
    """Нормализовать LaTeX короткой формулы для сравнения с величиной."""
    t = tex
    t = re.sub(r"\\(text|mathrm|textrm|mathit|operatorname|mbox|textit)\s*\{([^{}]*)\}", r"\2", t)
    t = re.sub(r"\\(left|right|,|;|!|quad)", "", t)
    for cmd, ch in sorted(GREEK_REV.items(), key=lambda x: -len(x[0])):
        t = re.sub(re.escape(cmd) + r"(?![A-Za-z])", ch, t)
    t = t.replace("{", "").replace("}", "").replace(" ", "").replace("′", "'")
    return t


def key_norm(q: Quantity) -> str:
    return norm_math(q.tex)


def load_quantities(path) -> tuple[dict[str, Quantity], list[str]]:
    """Прочитать quantities.toml. Вернуть ({ключ: Quantity}, ошибки)."""
    import tomllib
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}, []
    raw = p.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        return {}, [f"{p.name}: ошибка TOML: {e}"]
    lines = raw.split("\n")
    out: dict[str, Quantity] = {}
    errs = []
    for key, d in data.items():
        if not isinstance(d, dict):
            errs.append(f"{p.name}: «{key}» должна быть таблицей [\"{key}\"] с полями meaning/unit")
            continue
        ln = 0
        for i, l in enumerate(lines):
            s = l.strip()
            if s in (f'["{key}"]', f"[{key}]", f"['{key}']"):
                ln = i + 1
                break
        out[key] = Quantity(key=unicodedata.normalize("NFC", key), meaning=str(d.get("meaning", "")),
                            unit=str(d.get("unit", "")), latex=str(d.get("latex", "")),
                            value=d.get("value"), line=ln)
    return out, errs
