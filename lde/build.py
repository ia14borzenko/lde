"""Сборка PDF: модель → .lde/build/main.tex → XeLaTeX → out/<name>.pdf + карта положений.

Проходы: один, если оглавление не изменилось (aux от прошлой сборки), иначе второй.
Ошибка LaTeX не портит прошлый PDF: он заменяется только удачной сборкой.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from lde import env
from lde.emit_latex import LatexEmitter
from lde.project import Project

TEX_TO_BP = 72.0 / 72.27 / 65536.0


@contextmanager
def build_lock(root: Path):
    d = root / ".lde"
    d.mkdir(exist_ok=True)
    with open(d / "build.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _annotate(emitter: LatexEmitter, proj: Project) -> str:
    """Документ с комментариями-метками блоков (для перевода ошибок LaTeX в место в исходнике)."""
    orig_block = emitter.block

    def block(b, prev):
        s = orig_block(b, prev)
        if not s:
            return s
        # без пустых строк: формула должна остаться в абзаце, после которого стоит
        return f"%\n% lde-block {b.file}:{b.line} {b.uid}\n{s}"

    emitter.block = block
    try:
        return emitter.document()
    finally:
        emitter.block = orig_block


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def parse_log(log: str, tex_lines: list[str], proj: Project) -> list[dict]:
    errs = []
    lines = log.split("\n")
    for i, l in enumerate(lines):
        fm = re.match(r"^\./main\.tex:(\d+): (.*)$", l)
        if not l.startswith("! ") and not fm:
            continue
        msg = (fm.group(2) if fm else l[2:]).strip()
        if fm:
            lines[i] = l + f"\nl.{fm.group(1)}"
        ctx = " ".join(x.strip() for x in lines[i + 1:i + 4])
        if fm:
            ctx = " ".join(x.strip() for x in lines[i + 1:i + 3])
        m = re.search(r"l\.(\d+)", "\n".join(lines[i:i + 12]))
        if fm:
            m = re.match(r"(\d+)", fm.group(1))
            m = type("M", (), {"group": lambda self, k, v=fm.group(1): v})()
        where_file, where_line, what = "", 0, ""
        if m:
            ln = int(m.group(1))
            for j in range(min(ln, len(tex_lines)) - 1, -1, -1):
                mm = re.match(r"% lde-block (\S+):(\d+) (\S+)", tex_lines[j])
                if mm:
                    where_file, where_line = mm.group(1), int(mm.group(2))
                    b = proj.block_by_uid(mm.group(3))
                    if b:
                        what = proj.title_of(b)
                    break
        errs.append({"message": humanize_tex_error(msg, ctx), "raw": msg, "file": where_file,
                     "line": where_line, "element": what})
        if len(errs) >= 10:
            break
    return errs


def humanize_tex_error(msg: str, ctx: str) -> str:
    m = re.match(r"Undefined control sequence", msg)
    if m:
        top = re.search(r"l\.\d+ (.*)", ctx)
        cs = re.findall(r"(\\[A-Za-z@]+)", top.group(1) if top else ctx)
        cs = type("C", (), {"group": lambda self, k, v=(cs[-1] if cs else ""): v})() if cs else None
        return f"неизвестная команда LaTeX {cs.group(1) if cs else ''} в формуле".strip()
    if "Missing $ inserted" in msg:
        return "в формуле символ, допустимый только в математике или только вне её (проверьте _ ^ \\ в тексте)"
    if "Extra }" in msg or "Missing } inserted" in msg or "Missing { inserted" in msg:
        return "непарные фигурные скобки в формуле"
    if "Extra \\right" in msg or "Missing \\right" in msg:
        return "непарные \\left … \\right в формуле"
    if "File" in msg and "not found" in msg:
        return "не найден файл: " + msg
    if "Misplaced alignment tab" in msg:
        return "символ & вне таблицы/выравнивания (в тексте пишите «и» или \\&)"
    return msg


def build_pdf(proj: Project, statuses=None, log=lambda *a: None, force_passes: int = 0) -> dict:
    root = proj.root
    bdir = proj.build_dir
    bdir.mkdir(parents=True, exist_ok=True)
    out_dir = root / "out"
    out_dir.mkdir(exist_ok=True)
    t0 = time.time()
    with build_lock(root):
        em = LatexEmitter(proj, statuses)
        tex = _annotate(em, proj)
        tex_path = bdir / "main.tex"
        tex_path.write_text(tex, encoding="utf-8")
        toc_before = _hash(bdir / "main.toc")
        passes = 0
        cmd = [env.tool("xelatex"), "-interaction=nonstopmode", "-halt-on-error", "-file-line-error-style",
               "main.tex"]
        ok = True
        log_text = ""
        max_passes = max(force_passes, 3)
        if not Path(cmd[0]).exists():
            return {"ok": False, "passes": 0, "duration": 0, "warnings": [],
                    "errors": [{"message": f"не найден XeLaTeX ({cmd[0]}): установите инструменты командой "
                                           "lde doctor --fix или укажите каталог тулчейна в LDE_TOOLS_DIR"}]}
        while passes < max_passes:
            passes += 1
            r = subprocess.run(cmd, cwd=bdir, env=env.tool_env(), capture_output=True, timeout=600)
            log_text = (bdir / "main.log").read_text(encoding="utf-8", errors="replace") if (bdir / "main.log").exists() else r.stdout.decode("utf-8", "replace")
            if r.returncode != 0 or not (bdir / "main.pdf").exists():
                ok = False
                break
            toc_after = _hash(bdir / "main.toc")
            if passes >= force_passes and toc_after == toc_before:
                break
            toc_before = toc_after
        dur = time.time() - t0
        res = {"ok": ok, "passes": passes, "duration": round(dur, 2), "errors": [], "warnings": []}
        if not ok:
            res["errors"] = parse_log(log_text, tex.split("\n"), proj) or [{"message": "LaTeX завершился с ошибкой (подробности — в журнале)"}]
            (out_dir / "build-error.log").write_text(log_text, encoding="utf-8")
            return res
        pdf = out_dir / f"{proj.name}.pdf"
        tmp = out_dir / f".{proj.name}.pdf.tmp"
        shutil.copy2(bdir / "main.pdf", tmp)
        tmp.replace(pdf)
        res["pdf"] = str(pdf)
        # карта положений
        marks = []
        lpos = bdir / "main.lpos"
        if lpos.exists():
            for line in lpos.read_text(encoding="utf-8", errors="replace").split("\n"):
                parts = line.split()
                if len(parts) == 4 and parts[0].isdigit():
                    mid, page, x, y = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                    if mid < len(em.marks):
                        mk = dict(em.marks[mid])
                        mk.update({"page": page, "x": round(x * TEX_TO_BP, 1), "y": round(y * TEX_TO_BP, 1)})
                        marks.append(mk)
        (bdir / "map.json").write_text(json.dumps({"marks": marks, "time": time.time()}, ensure_ascii=False),
                                       encoding="utf-8")
        for w in re.findall(r"LaTeX Warning: (.*)", log_text):
            if "Rerun" in w or "Label(s)" in w:
                continue
            res["warnings"].append(w.strip())
        miss = sorted(set(re.findall(r"Missing character: There is no (\S+) \((U\+[0-9A-F]+)\)", log_text)))
        for ch, code in miss:
            res["warnings"].append(f"в шрифте нет знака {ch} ({code}) — он не виден в PDF")
        overfull = len(re.findall(r"Overfull \\hbox \((\d+\.\d+)pt", log_text))
        res["overfull"] = overfull
        (bdir / "last-build.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        return res
