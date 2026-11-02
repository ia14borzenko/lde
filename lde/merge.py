"""Слияние по блокам (абзац, формула, рисунок…) — для веток, синхронизации и приёма правок рецензента.

Трёхстороннее слияние делает сам git (`git merge-file`), но не по строкам, а по блокам: каждый блок
LDE-текста кодируется одной «строкой». Если оба изменили один и тот же блок — второй проход пословно
(вставки {R}, {=x}, $…$, [@k] — неделимые слова): правки в разных местах абзаца сливаются сами.
Неразрешимое — конфликтный блок прямо в тексте:

    <<<<<<< моё
    …
    ||||||| было
    …
    =======
    …
    >>>>>>> рецензент

Такой блок виден в Studio как конфликт (окно «моё / было / чужое»), `lde check` — ошибка
`merge-conflict`, сборка печатает на его месте красную пометку.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from lde.textfmt import parse_text

OURS_MARK, BASE_MARK, SEP_MARK, THEIRS_MARK = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "
_WORD_RE = re.compile(r"\{[^{}\n]*\}|\$[^$\n]*\$|\[@[^\]\n]*\]|@(?:fig|tab|eq|sec|app):[\w:.\-]+|\*\*|\w+|\s+|.", re.S)


def _enc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def _dec(s: str) -> str:
    return re.sub(r"\\(\\|n)", lambda m: "\n" if m.group(1) == "n" else "\\", s)


def _merge_file(ours: list[str], base: list[str], theirs: list[str]) -> tuple[list, int]:
    """git merge-file над списками «строк». Вернуть [(«ok», [строки]) | («conflict», o, b, t)], число конфликтов."""
    with tempfile.TemporaryDirectory(prefix="lde-merge-") as td:
        paths = []
        for name, lines in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(td) / name
            p.write_text("".join(_enc(x) + "\n" for x in lines), encoding="utf-8")
            paths.append(str(p))
        r = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "o", "-L", "b", "-L", "t", *paths],
                           capture_output=True, text=True)
        if r.returncode < 0 or r.returncode > 127:
            raise RuntimeError("git merge-file: " + r.stderr)
    out, cur, state, o, b, t, n = [], [], None, [], [], [], 0
    for ln in r.stdout.split("\n")[:-1] if r.stdout.endswith("\n") else r.stdout.split("\n"):
        if state is None and ln.startswith("<<<<<<< "):
            if cur:
                out.append(("ok", cur))
                cur = []
            state, o, b, t = "o", [], [], []
        elif state == "o" and ln.startswith("||||||| "):
            state = "b"
        elif state in ("o", "b") and ln == "=======":
            state = "t"
        elif state == "t" and ln.startswith(">>>>>>> "):
            out.append(("conflict", [_dec(x) for x in o], [_dec(x) for x in b], [_dec(x) for x in t]))
            n += 1
            state = None
        elif state == "o":
            o.append(ln)
        elif state == "b":
            b.append(ln)
        elif state == "t":
            t.append(ln)
        else:
            cur.append(_dec(ln))
    if cur:
        out.append(("ok", cur))
    return out, n


def merge_words(base: str, ours: str, theirs: str) -> str | None:
    """Пословное трёхстороннее слияние одного блока; None — если правки пересекаются."""
    if ours == theirs or theirs == base:
        return ours
    if ours == base:
        return theirs
    parts, n = _merge_file(_WORD_RE.findall(ours), _WORD_RE.findall(base), _WORD_RE.findall(theirs))
    if n:
        return None
    return "".join(x for kind, lines in ((p[0], p[1]) for p in parts) for x in lines)


def merge_block(base: str, ours: str, theirs: str) -> dict:
    """Слияние одного блока: {"ok": True, "text": …} или {"ok": False, "ours", "base", "theirs"}."""
    m = merge_words(base, ours, theirs)
    if m is not None:
        return {"ok": True, "text": m}
    return {"ok": False, "ours": ours, "base": base, "theirs": theirs}


def split_units(text: str) -> list[str]:
    """LDE-текст → исходники блоков (строки {style …} — вместе со своим блоком)."""
    lines = text.split("\n")
    return ["\n".join(lines[b.line - 1:b.end]) for b in parse_text(text)]


def conflict_text(ours: str, base: str, theirs: str, l_ours="моё", l_theirs="чужое") -> str:
    return f"{OURS_MARK}{l_ours}\n{ours}\n{BASE_MARK}было\n{base}\n{SEP_MARK}\n{theirs}\n{THEIRS_MARK}{l_theirs}"


def _side(base: list[str], side: list[str]) -> tuple[list, list]:
    """Как сторона изменила базу: val[i] — блок вместо base[i] (None — удалён), ins[i] — вставки перед base[i]."""
    import difflib
    n = len(base)
    val: list = list(base)
    ins: list[list[str]] = [[] for _ in range(n + 1)]
    sm = difflib.SequenceMatcher(a=base, b=side, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        k = min(a2 - a1, b2 - b1) if op == "replace" else 0
        for j in range(k):                       # пары «изменён» по порядку
            val[a1 + j] = side[b1 + j]
        for j in range(a1 + k, a2):              # лишние блоки базы — удалены
            val[j] = None
        ins[a2] += side[b1 + k:b2]               # лишние блоки стороны — вставлены
    return val, ins


def merge_units(base: list[str], ours: list[str], theirs: list[str], l_ours="моё", l_theirs="чужое") -> tuple[list[str], int]:
    """Трёхстороннее слияние списков блоков: каждый блок базы решается отдельно (правки соседних
    абзацев — не конфликт), один блок, изменённый обоими, — пословно."""
    ov, oi = _side(base, ours)
    tv, ti = _side(base, theirs)
    out, conflicts = [], 0

    def conflict(o: str, b: str, t: str):
        nonlocal conflicts
        conflicts += 1
        out.append(conflict_text(o, b, t, l_ours, l_theirs))

    for i in range(len(base) + 1):
        a, c = oi[i], ti[i]
        if a == c or not c:
            out += a
        elif not a:
            out += c
        else:                                     # обе стороны вставили разное в одно место
            conflict("\n\n".join(a), "", "\n\n".join(c))
        if i == len(base):
            break
        b, o, t = base[i], ov[i], tv[i]
        if o == t:
            if o is not None:
                out.append(o)
        elif o == b:
            if t is not None:
                out.append(t)
        elif t == b:
            if o is not None:
                out.append(o)
        elif o is None or t is None:              # одна сторона удалила, другая изменила
            conflict(o or "", b, t or "")
        else:
            m = merge_words(b, o, t)
            if m is None:
                conflict(o, b, t)
            else:
                out.append(m)
    return out, conflicts


def merge_text(base: str, ours: str, theirs: str, l_ours="моё", l_theirs="чужое") -> tuple[str, int]:
    """Трёхстороннее слияние файла по блокам. Вернуть (текст, число конфликтов)."""
    units, n = merge_units(split_units(base), split_units(ours), split_units(theirs), l_ours, l_theirs)
    return "\n\n".join(units) + "\n", n


def parse_conflict(src: str) -> dict:
    """Конфликтный блок → {"ours", "base", "theirs", "l_ours", "l_theirs"}."""
    m = re.match(r"<<<<<<< ?(.*)\n([\s\S]*?)\n?\|\|\|\|\|\|\| ?.*\n([\s\S]*?)\n?=======\n([\s\S]*?)\n?>>>>>>> ?(.*)$", src)
    if not m:
        m2 = re.match(r"<<<<<<< ?(.*)\n([\s\S]*?)\n?=======\n([\s\S]*?)\n?>>>>>>> ?(.*)$", src)
        if not m2:
            return {}
        return {"l_ours": m2.group(1), "ours": m2.group(2), "base": "", "theirs": m2.group(3), "l_theirs": m2.group(4)}
    return {"l_ours": m.group(1), "ours": m.group(2), "base": m.group(3), "theirs": m.group(4), "l_theirs": m.group(5)}


# --------------------------------------------------------- драйвер git ----
def driver(base_path: str, ours_path: str, theirs_path: str, label: str = "") -> int:
    """`git merge` вызывает: lde merge-driver %O %A %B %P — результат пишется в %A."""
    rd = lambda p: Path(p).read_text(encoding="utf-8", errors="replace")
    text, n = merge_text(rd(base_path), rd(ours_path), rd(theirs_path), "моё", "чужое")
    Path(ours_path).write_text(text, encoding="utf-8")
    return 1 if n else 0


def install_driver(root: Path):
    """Подключить слияние по блокам к git проекта (локально: .git/config и .git/info/attributes)."""
    from lde import gitops
    lde_bin = str(Path(__file__).resolve().parents[1] / "bin" / "lde")
    want = f'"{lde_bin}" merge-driver %O %A %B %P'
    if gitops.git(root, "config", "--get", "merge.lde.driver", check=False).strip() != want:
        gitops.git(root, "config", "merge.lde.name", "LDE: слияние по блокам")
        gitops.git(root, "config", "merge.lde.driver", want)
    attrs = root / ".git" / "info" / "attributes"
    line = "*.md merge=lde"
    cur = attrs.read_text(encoding="utf-8") if attrs.exists() else ""
    if line not in cur.split("\n"):
        attrs.parent.mkdir(parents=True, exist_ok=True)
        attrs.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + line + "\n", encoding="utf-8")
