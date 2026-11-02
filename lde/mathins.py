"""Вставки внутри формул: \\q{R_ц} — величина, \\val{csa.PRF} / \\val{csa.R0 км} — число расчёта.

Хранятся в исходнике как есть; раскрываются при сборке PDF/DOCX и в Studio (для MathLive —
в \\htmlData{lde=q:…}{…}, чтобы вставка оставалась связанной при визуальной правке).
"""
from __future__ import annotations

import re

from lde.fmt import FormatError, format_value

RE = re.compile(r"\\(q|val)\{([^{}]*)\}")


def find(tex: str):
    """[(kind, arg, start, end)]"""
    return [(m.group(1), m.group(2).strip(), m.start(), m.end()) for m in RE.finditer(tex or "")]


def value_text(proj, arg: str, mode: str) -> str | None:
    path, _, opts = arg.partition(" ")
    v = proj.value(path)
    if v is None:
        return None
    try:
        f = format_value(v["value"], v.get("unit", ""), v.get("digits"), opts.strip())
    except FormatError:
        return None
    return f["latex"] if mode == "latex" else f["text"]


def expand(tex: str, proj, mode: str = "latex", stale_calcs=()) -> str:
    """mode: latex (PDF) | text (DOCX: юникод внутри \\text) | ml (MathLive: \\htmlData)."""
    def rep(m):
        kind, arg = m.group(1), m.group(2).strip()
        if kind == "q":
            q = proj.quantities.get(arg)
            body = q.tex if q else r"\text{??}"
            return f"\\htmlData{{lde=q:{arg}}}{{{body}}}" if mode == "ml" else "{" + body + "}"
        txt = value_text(proj, arg, "text" if mode in ("text", "ml") else "latex")
        if txt is None:
            txt = "??"
        body = r"\text{" + txt + "}"
        if mode == "latex" and arg.split(".", 1)[0] in stale_calcs:
            body = r"\textcolor{red}{" + body + "}"
        return f"\\htmlData{{lde=v:{arg}}}{{{body}}}" if mode == "ml" else "{" + body + "}"
    return RE.sub(rep, tex or "")


def from_ml(tex: str) -> str:
    """\\htmlData{lde=q:R_ц}{…} → \\q{R_ц}; \\htmlData{lde=v:…}{…} → \\val{…} (содержимое отбрасывается)."""
    out, i = [], 0
    key = "\\htmlData{lde="
    while True:
        j = tex.find(key, i)
        if j < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:j])
        k = tex.find("}", j)
        tag = tex[j + len(key):k]
        # пропустить {…} с балансом скобок
        p, depth = k + 1, 0
        while p < len(tex):
            if tex[p] == "{":
                depth += 1
            elif tex[p] == "}":
                depth -= 1
                if depth == 0:
                    break
            p += 1
        kind, _, arg = tag.partition(":")
        out.append(("\\q{" if kind == "q" else "\\val{") + arg + "}")
        i = p + 1
    return "".join(out)
