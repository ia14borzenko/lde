"""MathType (OLE Equation.DSMT4, MTEF v5) → LaTeX.

Порт RDE `crates/rde-doc/src/mtef` (Rust), который сам — порт mtef-go (Apache-2.0), с
дополнениями RDE (интегралы, суммы, дроби «слэшем», дуги и т.п.). Чтение — по проверяемым
границам: битые данные дают MtefError, а не падение импорта.
"""
from __future__ import annotations

import io
import re
import struct

from lde.importers.mtef_chars import CHARS, CHARS_MATH, SPECIAL


class MtefError(Exception):
    pass


# ----------------------------------------------------------------- OLE ----
def equation_native(ole_bytes: bytes) -> bytes:
    import olefile
    try:
        ole = olefile.OleFileIO(io.BytesIO(ole_bytes))
    except Exception:
        raise MtefError("не OLE Compound File")
    for name in ("Equation Native", "EquationNative", "Equation"):
        if ole.exists(name):
            data = ole.openstream(name).read()
            break
    else:
        raise MtefError("в OLE-объекте нет стрима Equation Native")
    if len(data) < 28:
        raise MtefError("битый заголовок EQNOLEFILEHDR")
    hdr_len = struct.unpack_from("<H", data, 0)[0]
    if hdr_len != 28:
        raise MtefError("битый заголовок EQNOLEFILEHDR")
    size = struct.unpack_from("<I", data, 8)[0]
    body = data[hdr_len:]
    if len(body) < size:
        raise MtefError("обрезанный MTEF")
    return body[:size]


# ------------------------------------------------------------- records ----
class _R:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def rem(self):
        return len(self.d) - self.p

    def u8(self):
        if self.p >= len(self.d):
            raise MtefError("битая запись MTEF")
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        if self.p + 2 > len(self.d):
            raise MtefError("битая запись MTEF")
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

    def i16(self):
        if self.p + 2 > len(self.d):
            raise MtefError("битая запись MTEF")
        v = struct.unpack_from("<h", self.d, self.p)[0]
        self.p += 2
        return v

    def skip(self, n):
        if self.rem() < n:
            raise MtefError("битая запись MTEF")
        self.p += n

    def nul_string(self):
        while self.u8() != 0:
            pass

    def nudge(self):
        x, y = self.u8(), self.u8()
        if x == 128 or y == 128:
            self.i16()
            self.i16()


def _skip_dims(r: _R, count: int):
    done = 0
    unit_pending = False
    while done < count:
        b = r.u8()
        for nib in (b >> 4, b & 0x0F):
            if unit_pending:
                unit_pending = False
            elif nib == 0x0F:
                unit_pending = True
                done += 1


def parse_records(data: bytes) -> list:
    r = _R(data)
    ver = r.u8()
    if ver != 5:
        raise MtefError(f"неподдерживаемая версия MTEF: {ver} (нужна v5)")
    for _ in range(4):
        r.u8()
    r.nul_string()
    r.u8()
    nodes = []
    while r.rem() > 0:
        tag = r.u8()
        if tag >= 100:
            r.skip(r.u8())
            continue
        if tag == 0:
            nodes.append(("end",))
        elif tag == 1:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            if o & 0x04:
                r.u8()
            if o & 0x02:
                for _ in range(r.u8()):
                    r.u8()
                    r.u16()
            nodes.append(("line", bool(o & 0x01)))
        elif tag == 2:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            tf = r.u8()
            mt = r.u16() if not (o & 0x20) else 0
            if o & 0x04:
                r.u8()
            if o & 0x10:
                r.u16()
            nodes.append(("char", tf, mt))
        elif tag == 3:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            sel = r.u8()
            first = r.u8()
            if first & 0x80:
                var = (first & 0x7F) | (r.u8() << 8)
            else:
                var = first
            r.u8()
            nodes.append(("tmpl", sel, var))
        elif tag == 4:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            r.u8()
            r.u8()
            nodes.append(("pile",))
        elif tag == 5:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            for _ in range(4):
                r.u8()
            cols = r.u8()
            nodes += [("matrix", cols), ("line", False), ("line", False)]
        elif tag == 6:
            o = r.u8()
            if o & 0x08:
                r.nudge()
            nodes.append(("embell", r.u8()))
        elif tag == 8:
            r.u8()
            r.nul_string()
        elif tag == 9:
            r.u8()
            r.u8()
        elif tag == 15:
            r.u8()
        elif tag == 16:
            o = r.u8()
            for _ in range(4 if o & 0x01 else 3):
                r.u16()
            if o & 0x04:
                r.nul_string()
        elif tag == 17:
            r.u8()
            r.nul_string()
        elif tag == 18:
            r.u8()
            _skip_dims(r, r.u8())
            _skip_dims(r, r.u8())
            for _ in range(r.u8()):
                if r.u8() != 0:
                    r.u8()
        elif tag == 19:
            r.nul_string()
        elif 10 <= tag <= 14:
            pass
        else:
            raise MtefError("неизвестная запись MTEF")
    return nodes


# ---------------------------------------------------------------- tree ----
def build_tree(nodes: list):
    pos = [0]

    def lst(depth):
        if depth > 64:
            raise MtefError("слишком глубокая вложенность MTEF")
        out = []
        while pos[0] < len(nodes):
            nd = nodes[pos[0]]
            pos[0] += 1
            k = nd[0]
            if k == "end":
                return out
            if k == "char":
                out.append(nd)
            elif k == "line":
                out.append(("line", []) if nd[1] else ("line", lst(depth + 1)))
            elif k == "tmpl":
                out.append(("tmpl", nd[1], nd[2], lst(depth + 1)))
            elif k == "pile":
                out.append(("pile", lst(depth + 1)))
            elif k == "matrix":
                out.append(("matrix", nd[1], lst(depth + 1)))
            elif k == "embell":
                e = ("embell", nd[1], lst(depth + 1))
                if nd[1] in (2, 9, 17) and out:
                    prev = out.pop()
                    out += [e, prev]
                    continue
                out.append(e)
        return out

    return lst(0)


# ---------------------------------------------------------------- LaTeX ----
FN_TEXT, FN_FUNCTION, FN_MTEXTRA, FN_SPACE = 1, 2, 11, 24


def _char(tf: int, mt: int) -> str:
    style = (tf - 128) & 0xFF
    table = CHARS_MATH if style in (FN_MTEXTRA, FN_SPACE) else CHARS
    s = table.get(mt)
    if s is None:
        ch = chr(mt) if mt else "�"
        s = SPECIAL.get(ch)
        if s is None:
            if ch == "\\":
                s = "\\backslash "
            elif ch in "#$%&_{}":
                s = "\\" + ch
            elif ch == "~":
                s = "\\sim "
            elif ch == "^":
                s = "\\hat{}"
            elif ord(ch) < 32:
                s = ""
            else:
                s = ch
    if style in (FN_TEXT, FN_FUNCTION) and s.strip():
        s = "\\mathrm{" + s + "}"
    return s


def _emit_list(nodes) -> str:
    return "".join(_emit(n) for n in nodes)


def _slot(children, i) -> str:
    return _emit(children[i]) if i < len(children) else ""


EMBELL = {2: "\\dot ", 3: "\\ddot ", 4: "\\dddot ", 5: "'", 6: "''", 7: "'", 8: "\\tilde ", 9: "\\hat ",
          10: "\\not ", 11: "\\overset{\\rightarrow}{}", 12: "\\overset{\\leftarrow}{}",
          13: "\\overset{\\leftrightarrow}{}", 16: "\\bar ", 17: "\\bar ", 18: "'''"}


def _fence(sel, given, left):
    if not given:
        return "."
    m = {"<": "\\langle", ">": "\\rangle", "|": "|", "||": "\\|", "/": "/"}
    if given in m:
        return m[given]
    d = {(0, 1): "\\langle", (0, 0): "\\rangle", (1, 1): "(", (1, 0): ")", (2, 1): "\\{", (2, 0): "\\}",
         (3, 1): "[", (3, 0): "]", (6, 1): "\\lfloor", (6, 0): "\\rfloor", (7, 1): "\\lceil", (7, 0): "\\rceil",
         (8, 1): "[", (8, 0): "]"}
    if sel == 4:
        return "|"
    if sel == 5:
        return "\\|"
    return d.get((sel, 1 if left else 0), ".")


def _emit(n) -> str:
    k = n[0]
    if k == "line":
        return _emit_list(n[1])
    if k == "char":
        return _char(n[1], n[2])
    if k == "pile":
        rows = [_emit(c) for c in n[1]]
        if len(rows) <= 1:
            return rows[0] if rows else ""
        return "\\begin{array}{l} " + " \\\\ ".join(rows) + " \\end{array}"
    if k == "matrix":
        cols = max(1, n[1])
        cells = [_emit(c) for c in n[2][1:]]
        s = f"\\begin{{array}}{{{'l' if cols == 1 else 'c' * cols}}} "
        for i, c in enumerate(cells):
            s += c
            if i % cols == cols - 1 and i != len(cells) - 1:
                s += " \\\\ "
            elif i % cols != cols - 1:
                s += " & "
        return s + " \\end{array}"
    if k == "embell":
        pre = EMBELL.get(n[1], "")
        inner = _emit_list(n[2])
        return pre + ("{" + inner + "}" if inner.strip() else "")
    sel, var, ch = n[1], n[2], n[3]
    if sel <= 9:
        main, l, r = _slot(ch, 0), _slot(ch, 1).strip(), _slot(ch, 2).strip()
        L, R = _fence(sel, l, True), _fence(sel, r, False)
        if sel == 2:
            return f"\\left{L} \\begin{{array}}{{l}} {main} \\end{{array}} \\right{R}"
        return f"\\left{L} {main} \\right{R}"
    if sel == 10:
        main, idx = _slot(ch, 0), _slot(ch, 1)
        return f"\\sqrt[{idx}]{{{main}}}" if idx.strip() else f"\\sqrt{{{main}}}"
    if sel == 11:
        a, b = _slot(ch, 0), _slot(ch, 1)
        return f"{{{a}}}/{{{b}}}" if var & 0x0002 else f"\\frac{{{a}}}{{{b}}}"
    if sel == 12:
        return f"\\underline{{{_slot(ch, 0)}}}"
    if sel == 13:
        return f"\\overline{{{_slot(ch, 0)}}}"
    if sel == 14:
        top, bot = _slot(ch, 0), _slot(ch, 1)
        arrow = "\\leftarrow" if (var & 0x10 and not var & 0x20) else "\\rightarrow"
        return f"\\overset{{{top}}}{{{arrow}}}" if not bot.strip() else f"\\overset{{{top}}}{{\\underset{{{bot}}}{{{arrow}}}}}"
    if 15 <= sel <= 22:
        main, lo, up, op = _slot(ch, 0), _slot(ch, 1), _slot(ch, 2), _slot(ch, 3)
        o = {15: "\\int", 16: "\\sum", 17: "\\prod", 18: "\\coprod", 19: "\\bigcup", 20: "\\bigcap"}.get(sel)
        if o is None:
            o = op.strip() or "\\int"
        s = o
        if lo.strip():
            s += f"_{{{lo}}}"
        if up.strip():
            s += f"^{{{up}}}"
        return s + " " + main
    if sel == 23:
        main, lo, up = _slot(ch, 0), _slot(ch, 1), _slot(ch, 2)
        return f"\\underset{{{lo}}}{{{main}}}" if not up.strip() else f"\\overset{{{up}}}{{\\underset{{{lo}}}{{{main}}}}}"
    if sel in (24, 25):
        over = bool(var & 1)
        cmd = {(24, True): "overbrace", (24, False): "underbrace", (25, True): "overbracket", (25, False): "underbracket"}[(sel, over)]
        return f"\\{cmd}{{{_slot(ch, 0)}}}"
    if sel == 26:
        return _slot(ch, 0)
    if sel in (27, 28, 29):
        sub, sup = _slot(ch, 0), _slot(ch, 1)
        s = "{}" if var & 1 else ""
        if sel in (27, 29) and sub.strip():
            s += f"_{{{sub}}}"
        if sel in (28, 29) and sup.strip():
            s += f"^{{{sup}}}"
        return s
    if sel == 30:
        c = _emit_list(ch)
        s = "\\langle " if var & 1 else ""
        s += c
        if var & 2 or var & 1:
            s += " \\rangle"
        return s
    if sel == 31:
        main = _slot(ch, 0)
        harp, left = bool(var & 8), bool(var & 1 and not var & 2)
        arrow = {(False, False): "\\rightarrow", (False, True): "\\leftarrow",
                 (True, False): "\\rightharpoonup", (True, True): "\\leftharpoonup"}[(harp, left)]
        return f"{'\\underset' if var & 4 else '\\overset'}{{{arrow}}}{{{main}}}"
    if sel == 32:
        return f"\\tilde{{{_slot(ch, 0)}}}"
    if sel == 33:
        main, acc = _slot(ch, 0), _slot(ch, 1)
        return f"\\hat{{{main}}}" if not acc.strip() else f"\\overset{{{acc}}}{{{main}}}"
    if sel == 34:
        return f"\\overparen{{{_slot(ch, 0)}}}"
    if sel == 35:
        return _emit_list(ch)
    if sel == 36:
        return f"\\overline{{{_slot(ch, 0)}}}"
    if sel == 37:
        return f"\\fbox{{{_slot(ch, 0)}}}"
    raise MtefError(f"шаблон MTEF не поддержан (selector {sel}, variation 0x{var:04x})")


FUNC_NAMES = {"sin", "cos", "tan", "cot", "exp", "lg", "ln", "log", "lim", "max", "min", "sh", "ch", "th",
              "arcsin", "arccos", "det", "arg"}


def _merge_mathrm(s: str) -> str:
    """\\mathrm{c}\\mathrm{o}\\mathrm{s} → \\cos (функции MathType); прочее — одним \\mathrm{…}."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\\mathrm\{([^{}]*)\}\\mathrm\{([^{}]*)\}", r"\\mathrm{\1\2}", s)
    def fn(m):
        w = m.group(1)
        if w in FUNC_NAMES:
            return "\\" + w + " "
        if w in ("tg", "ctg", "arctg", "sign", "sinc"):
            return "\\operatorname{" + w + "}"
        return m.group(0)
    return re.sub(r"\\mathrm\{([A-Za-z]+)\}", fn, s)


def latex_from_mtef(data: bytes) -> str:
    s = _merge_mathrm(_emit_list(build_tree(parse_records(data)))).strip()
    if not s:
        raise MtefError("пустая формула")
    return s


def latex_from_ole(ole_bytes: bytes) -> str:
    return latex_from_mtef(equation_native(ole_bytes))
