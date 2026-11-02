"""OMML → LaTeX (пакетно, одним вызовом pandoc) и нормализация LaTeX из импорта."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from lde import env
from lde.quantities import GREEK

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

SYMBOLS = {
    "≈": r"\approx ", "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "·": r"\cdot ", "⋅": r"\cdot ", "×": r"\times ",
    "−": "-", "∞": r"\infty ", "∂": r"\partial ", "→": r"\to ", "←": r"\leftarrow ", "±": r"\pm ",
    "∓": r"\mp ", "∑": r"\sum ", "∫": r"\int ", "∏": r"\prod ", "√": r"\surd ", "∈": r"\in ",
    "⊗": r"\otimes ", "∗": "*", "′": "'", "″": "''", "…": r"\ldots ", "⋯": r"\cdots ", "°": r"^{\circ}",
    "∠": r"\angle ", "∆": r"\Delta ", "ℱ": r"\mathcal{F}", "∇": r"\nabla ", "∝": r"\propto ",
}
FUNCS = ["arcsin", "arccos", "arctg", "arcctg", "sinh", "cosh", "tanh", "sin", "cos", "tan", "cot", "tg", "ctg",
         "exp", "lg", "ln", "log", "lim", "max", "min", "sh", "ch", "th", "sign", "sinc", "Re", "Im", "det", "arg"]
STD_FUNCS = {"arcsin", "arccos", "sinh", "cosh", "tanh", "sin", "cos", "tan", "cot", "exp", "lg", "ln", "log",
             "lim", "max", "min", "sh", "ch", "th", "det", "arg"}


def normalize_latex(tex: str) -> str:
    """Юникод → команды, имена функций → \\cos и т.п. (внутри \\text{} не трогаем)."""
    out = []
    i = 0
    s = tex.replace("\u00a0", " ").replace("\u2009", " ")
    # команды, которых нет в LaTeX (встречаются в экспорте RDE/MathType)
    s = re.sub(r"\\thinsp(?![A-Za-z])", r"\\,", s)
    s = re.sub(r"\\medsp(?![A-Za-z])", r"\\:", s)
    s = re.sub(r"\\thicksp(?![A-Za-z])", r"\\;", s)
    in_text = 0
    depth_stack = []
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)\s*", s[i:])
            if m:
                name = m.group(1)
                out.append(s[i:i + len(m.group(0))])
                i += len(m.group(0))
                if name in ("text", "mathrm", "operatorname", "textrm", "mbox") and i < len(s) and s[i] == "{":
                    depth_stack.append(True)
                    in_text += 1
                    out.append("{")
                    i += 1
                continue
            out.append(s[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth_stack.append(False)
            out.append(c)
            i += 1
            continue
        if c == "}":
            if depth_stack and depth_stack.pop():
                in_text -= 1
            out.append(c)
            i += 1
            continue
        if not in_text:
            if c in GREEK:
                out.append(GREEK[c] + " ")
                i += 1
                continue
            if c in SYMBOLS:
                out.append(SYMBOLS[c])
                i += 1
                continue
            if c.isalpha() and c.isascii() and (i == 0 or not s[i - 1].isalpha()):
                m = re.match(r"[A-Za-z]+", s[i:])
                word = m.group(0)
                if word in FUNCS:
                    out.append(("\\" + word + " ") if word in STD_FUNCS else (r"\operatorname{" + word + "}"))
                    i += len(word)
                    continue
        out.append(c)
        i += 1
    r = "".join(out)
    r = re.sub(r" +", " ", r)
    # двойной индекс L_{max}_{21} (MathType, OMML) → L_{max21}
    for _ in range(3):
        r = re.sub(r"_\{([^{}]*)\}\s*_\{([^{}]*)\}", r"_{\1\2}", r)
        r = re.sub(r"\^\{([^{}]*)\}\s*\^\{([^{}]*)\}", r"^{\1\2}", r)
    r = re.sub(r" ([}_^),=+\-])", r"\1", r)
    return r.strip()


def omml_to_latex(docx_path: Path, elements: list) -> list[str | None]:
    """Перевести список OMML-элементов (m:oMath / m:oMathPara) в LaTeX одним вызовом pandoc."""
    if not elements:
        return []
    with zipfile.ZipFile(docx_path) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
        names = z.namelist()
        data = {n: z.read(n) for n in names}
    root = etree.Element(doc.tag, nsmap=doc.nsmap)
    body = etree.SubElement(root, f"{{{W}}}body")
    for k, el in enumerate(elements):
        p = etree.SubElement(body, f"{{{W}}}p")
        r = etree.SubElement(p, f"{{{W}}}r")
        t = etree.SubElement(r, f"{{{W}}}t")
        t.text = f"LDEMATH{k}Z "
        p.append(copy.deepcopy(el))
    data["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "m.docx"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in data.items():
                z.writestr(n, b)
        r = subprocess.run([env.tool("pandoc"), str(tmp), "-f", "docx", "-t", "json"],
                           capture_output=True, text=True, env=env.tool_env())
    if r.returncode != 0:
        return [None] * len(elements)
    ast = json.loads(r.stdout)
    res: list[str | None] = [None] * len(elements)
    for blk in ast.get("blocks", []):
        if blk.get("t") not in ("Para", "Plain"):
            continue
        cur = None
        maths = []
        for inl in blk["c"]:
            if inl["t"] == "Str" and inl["c"].startswith("LDEMATH"):
                m = re.match(r"LDEMATH(\d+)Z", inl["c"])
                if m:
                    cur = int(m.group(1))
            elif inl["t"] == "Math":
                maths.append(inl["c"][1])
        if cur is not None and maths and cur < len(res):
            res[cur] = " ".join(maths)
    return res
