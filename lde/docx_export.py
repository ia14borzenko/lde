"""DOCX — производный экспорт для Word из той же модели документа.

Модель → pandoc-LaTeX со статическими номерами (их считает тот же код, что и для PDF) + план →
pandoc → постобработка v0.1 (титул, СОДЕРЖАНИЕ с номерами страниц, формулы с номером справа,
ГОСТ-стили) → рендер LibreOffice → QA v0.1 (дефекты D1–D10, ГОСТ-параметры, сравнение с PDF)
→ автоматический ремонт найденных дефектов.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from lde import bib as bibmod
from lde import env
from lde.emit_latex import LatexEmitter, display_math, fix_math
from lde.fmt import latex_escape
from lde.project import Project

ROOT = env.ROOT


class DocxEmitter(LatexEmitter):
    """Тот же разбор, но LaTeX — «понятный pandoc'у», без вёрстки; номера — текстом."""

    math_mode = "text"

    def __init__(self, proj, statuses=None):
        super().__init__(proj, statuses, mark=False)
        self.headings: list[dict] = []
        self.display: list[dict] = []
        self.appendix_titles: list[dict] = []
        self.manual_styles: list[dict] = []

    def inline(self, nodes):
        s = super().inline(nodes)
        s = re.sub(r"\\hyperlink\{[^{}]*\}\{([^{}]*)\}", r"\1", s)
        s = re.sub(r"\\ldestaleval\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
        s = re.sub(r"\\ldeunknown\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
        s = re.sub(r"\\textbf\{\\ldeunknown", r"\\textbf{", s)
        s = re.sub(r"\\ensuremath\{((?:[^{}]|\{[^{}]*\})*)\}", r"$\1$", s)
        s = s.replace(r"\textminus{}", "−").replace(r"\mbox{-}", "‑")
        return s

    def body(self) -> str:
        p = self.p
        out = []
        first_app = next((i for i, b in enumerate(p.blocks) if b.kind == "heading" and "appendix" in b.classes), None)
        bib_done = False
        prev = None
        for i, b in enumerate(p.blocks):
            if i == first_app and not bib_done:
                out.append(self.bibliography())
                bib_done = True
            out.append(self.block(b, prev))
            if b.kind != "comment":
                prev = b
        if not bib_done:
            out.append(self.bibliography())
        return "\n\n".join(x for x in out if x)

    def _heading(self, level, text, structural=False, appendix=False):
        self.headings.append({"level": level, "text": text, "structural": structural, "appendix": appendix, "toc": True})
        cmd = {1: "section", 2: "subsection", 3: "subsubsection"}[min(level, 3)]
        return f"\\{cmd}{{{text}}}"

    def block(self, b, prev):
        s = self._docx_block(b, prev)
        if s and b.style:
            # ручное оформление: блок между абзацами-метками; постобработка применяет параметры
            # к абзацам между ними и убирает метки
            n = len(self.manual_styles)
            self.manual_styles.append({"kind": b.kind, **b.style})
            s = f"LDEFMTBEGIN{n}\n\n{s}\n\nLDEFMTEND{n}"
        return s

    def _docx_block(self, b, prev):
        p = self.p
        k = b.kind
        if k in ("comment", "pagebreak"):
            return ""
        if k == "conflict":
            return "[Конфликт слияния: этот фрагмент правили оба — выберите вариант в LDE Studio.]"
        if k == "heading":
            t = self.inline(b.inl)
            plain = re.sub(r"\$[^$]*\$", "", t)
            num = p.numbers.get(id(b), "")
            if "appendix" in b.classes and b.level == 1:
                head = f"ПРИЛОЖЕНИЕ {num}"
                self.appendix_titles.append({"heading": head, "title": strip_tex(plain)})
                return self._heading(1, head, True, True) + f"\n\n\\textbf{{{t}}}"
            if b.unnumbered:
                if b.level == 1:
                    txt = t.upper() if self.profile.structural_upper else t
                    return self._heading(1, txt, True)
                return self._heading(b.level, t)
            return self._heading(b.level, f"{num} {t}")
        if k == "para":
            # пояснения к формуле помечаются невидимыми знаками — постобработка ставит отступы/табуляцию
            role = self.roles.get(id(b))
            mark = {"gde": "\u2060", "cont": "\u2063"}.get(role, "")
            return mark + self.inline(b.inl).lstrip()
        if k == "formula":
            num = p.numbers.get(id(b))
            self.display.append({"numbered": bool(num), "number": num or ""})
            return f"\\[\n{display_math(self.expand(b.math))}\n\\]"
        if k == "figure":
            num = p.numbers.get(id(b), "?")
            cap = self.inline(b.inl)
            paths = [b.path] if b.path else [sf[1] for sf in b.subfigs]
            imgs = []
            for path in paths:
                full = p.root / path
                if full.exists():
                    w = b.attrs.get("width", "0.9")
                    imgs.append(f"\\includegraphics[width={w}\\textwidth]{{{full}}}")
                else:
                    imgs.append(f"[нет файла рисунка {latex_escape(path)}]")
            stale = self._figure_stale(b)
            extra = f"\n\n{latex_escape('⚠ ' + stale)}" if stale else ""
            return ("\\begin{figure}\n\\centering\n" + "\n".join(imgs) +
                    f"\n\\caption{{Рисунок {num} — {cap}}}\n\\end{{figure}}" + extra)
        if k == "table":
            num = p.numbers.get(id(b), "?")
            cap = self.inline(b.inl)
            data, err = self.table_cells(b)
            if err:
                return f"Таблица {num} — {cap}\n\n{latex_escape(err)}"
            header, rows, aligns = data
            ncol = max([len(header)] + [len(r) for r in rows] + [1])
            spec = "|" + "|".join((aligns[j] if j < len(aligns) else "l") for j in range(ncol)) + "|"
            lines = [f"\\begin{{table}}\n\\caption{{Таблица {num} — {cap}}}", f"\\begin{{tabular}}{{{spec}}}", "\\hline"]
            for r in ([header] if header else []) + rows:
                cells = [self.inline(c[2]) for c in r] + [""] * (ncol - len(r))
                lines.append(" & ".join(cells[:ncol]) + r" \\ \hline")
            lines += ["\\end{tabular}", "\\end{table}"]
            return "\n".join(lines)
        if k == "list":
            s = self.list_block(b)
            return s.replace("\\ldemark{}", "")
        if k == "code":
            return f"\\begin{{verbatim}}\n{b.text}\n\\end{{verbatim}}"
        return ""

    def bibliography(self) -> str:
        p = self.p
        if not p.doc_cfg.get("bibliography", True) or not p.cite_order:
            return ""
        out = [self._heading(1, self.profile.heading("bibliography"), True)]
        for i, key in enumerate(p.cite_order, 1):
            e = p.bib.get(key)
            text = bibmod.format_entry(e) if e else f"[нет записи «{key}» в refs.bib]"
            out.append(f"{i}{'.' if self.profile.bib_number_dot else ''} {latex_escape(text)}")
        self.bib_count = len(p.cite_order)
        return "\n\n".join(out)

    def abbreviations(self) -> str:
        p = self.p
        parts = []
        if p.doc_cfg.get("abbreviations", True) and p.abbreviations:
            items = list(p.abbreviations.items())
            if self.profile.abbr_sort:
                items.sort(key=lambda kv: kv[0].lower())
            parts.append(self._heading(1, self.profile.heading("abbreviations"), True))
            parts += [f"{latex_escape(k)} – {latex_escape(v)}" for k, v in items]
        if p.doc_cfg.get("symbols", False) and p.quantities:
            if not parts:
                parts.append(self._heading(1, self.profile.heading("symbols"), True))
            for q in p.quantities.values():
                unit = f", {latex_escape(q.unit)}" if q.unit else ""
                parts.append(f"${fix_math(q.tex)}$ – {latex_escape(q.meaning)}{unit}")
        return "\n\n".join(parts)

    def source(self) -> tuple[str, dict]:
        p = self.p
        title = self.profile.titlepage_lines(p) if p.doc_cfg.get("title_page", True) else []
        tp = []
        space = 0
        for ln in title:
            if ln["space"] == -1:
                space = 8
            tp.append({"text": ln["text"], "align": ln["align"], "bold": ln["bold"], "size_pt": 14,
                       "space_before_pt": (space or max(0, ln["space"])) * 18})
            space = 0
        parts = [latex_escape(t["text"]) for t in tp]
        ab = self.abbreviations()
        if ab:
            parts.append(ab)
        parts.append(self.body())
        src = "\n\n".join(parts) + "\n"
        plan = {
            "version": 2, "generated_by": "lde/docx_export.py", "project": str(p.root),
            "metadata": {"title": p.title, "author": str(p.config.get("title", {}).get("author", ""))},
            "titlepage": tp,
            "headings": [h for h in self.headings if p.doc_cfg.get("toc", True)],
            "display_math": self.display, "appendix_titles": self.appendix_titles,
            "appendix_heading": next((h["text"] for h in self.headings if h.get("appendix")), None),
            "counters": {"figures": sum(1 for b in p.blocks if b.kind == "figure"),
                         "tables": sum(1 for b in p.blocks if b.kind == "table"),
                         "equations": sum(1 for d in self.display if d["numbered"])},
            "toc_heading": self.profile.heading("toc"),
            "bibliography_heading": self.profile.heading("bibliography"),
            "bibliography_inline": True, "bibliography_count": getattr(self, "bib_count", 0),
            "structural_upper": self.profile.structural_upper,
            "block_spacing": p.doc_cfg.get("block_spacing", "none"),
            "hyphenation": bool(p.doc_cfg.get("hyphenation", True)),
            "manual_styles": self.manual_styles,
            "warnings": [],
        }
        if not p.doc_cfg.get("toc", True):
            plan["headings"] = []
        return src, plan


def strip_tex(s: str) -> str:
    s = re.sub(r"\\[A-Za-z]+\{([^{}]*)\}", r"\1", s)
    return s.replace("\\", "")


def _render(docx: Path, pdf: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="lde-lo-") as td:
        prof = Path(td) / "profile"
        r = subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf",
                            "--outdir", td, str(docx)], capture_output=True, timeout=600)
        out = Path(td) / (docx.stem + ".pdf")
        if not out.exists():
            return False
        shutil.copy2(out, pdf)
        return True


def _py(script: str, *args, timeout=900):
    return subprocess.run([env.venv_python(), str(ROOT / script), *args], capture_output=True, text=True,
                          timeout=timeout, env=env.tool_env())


def export_docx(proj: Project, log=lambda *a: None, qa=True) -> dict:
    t0 = time.time()
    root = proj.root
    out = root / "out"
    work = out / f".docx-build-{proj.name}"
    work.mkdir(parents=True, exist_ok=True)
    name = proj.name
    em = DocxEmitter(proj)
    src, plan = em.source()
    (work / "source.tex").write_text(src, encoding="utf-8")
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    mode = proj.doc_cfg.get("block_spacing", "none")
    ref = ROOT / "convert" / f"reference-{mode}.docx"
    script = ROOT / "toolchain" / "build_reference_docx.py"
    if not ref.exists() or ref.stat().st_mtime < script.stat().st_mtime:
        r = _py("toolchain/build_reference_docx.py", "--out", str(ref), "--block-spacing", mode)
        if r.returncode != 0:
            return {"ok": False, "error": "не удалось подготовить reference.docx: " + r.stderr[-300:]}
    log("[1/5] pandoc: LaTeX → DOCX")
    r = subprocess.run([env.tool("pandoc"), str(work / "source.tex"), "-f", "latex", "-t", "docx",
                        f"--reference-doc={ref}", f"--resource-path={root}", "-o", str(work / "raw.docx")],
                       capture_output=True, text=True, env=env.tool_env(), timeout=300)
    if r.returncode != 0:
        return {"ok": False, "error": "pandoc не смог собрать DOCX: " + r.stderr[-400:]}
    log("[2/5] постобработка (титул, заголовки, формулы, таблицы)")
    r = _py("convert/postprocess_docx.py", "--docx", str(work / "raw.docx"), "--plan", str(work / "plan.json"),
            "--out", str(work / "pass1.docx"), "--report", str(work / "pass1.json"))
    if r.returncode != 0:
        return {"ok": False, "error": "постобработка DOCX упала: " + (r.stderr or r.stdout)[-400:]}
    log("[3/5] номера страниц СОДЕРЖАНИЯ (рендер LibreOffice)")
    final = out / f"{name}.docx"
    if _render(work / "pass1.docx", work / "pass1.pdf"):
        _py("convert/find_toc_pages.py", "--pdf", str(work / "pass1.pdf"), "--plan", str(work / "plan.json"),
            "--out", str(work / "toc-pages.json"))
        r = _py("convert/postprocess_docx.py", "--docx", str(work / "raw.docx"), "--plan", str(work / "plan.json"),
                "--toc-pages", str(work / "toc-pages.json"), "--out", str(work / "final.docx"),
                "--report", str(work / "pass2.json"))
        if r.returncode != 0:
            return {"ok": False, "error": "постобработка DOCX (2-й проход) упала: " + (r.stderr or r.stdout)[-400:]}
    else:
        shutil.copy2(work / "pass1.docx", work / "final.docx")
    shutil.copy2(work / "final.docx", final)
    res = {"ok": True, "path": str(final), "seconds": None}
    if qa:
        log("[4/5] проверка DOCX (дефекты, ГОСТ, сравнение с PDF)")
        res["qa"] = qa_docx(proj, final)
        if res["qa"].get("fixable"):
            log("[5/5] ремонт найденных дефектов")
            fr = _py("qa/fix_docx.py", "--project", str(root), "--docx", str(final), "--in-place")
            res["fixed"] = fr.returncode == 0
            res["qa"] = qa_docx(proj, final)
    res["seconds"] = round(time.time() - t0, 1)
    rep = res.get("qa", {})
    res["summary"] = (f"DOCX готов: {final.name}. " + (f"Проверка: {rep.get('errors', 0)} ошибок, "
                      f"{rep.get('warnings', 0)} замечаний; текст совпадает с PDF на {rep.get('coverage', '?')}."
                      if rep else ""))
    (out / f"{name}.docx.report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return res


def qa_docx(proj: Project, docx: Path) -> dict:
    root = proj.root
    out = root / "out"
    pdf_docx = out / f"{proj.name}.docx.pdf"
    _render(docx, pdf_docx)
    _py("qa/check_docx.py", "--project", str(root), "--docx", str(docx), "--pdf", str(pdf_docx),
        "--out", str(out / "qa-check.json"), "--quiet")
    from lde.profiles import get_profile
    prof = get_profile(proj.doc_cfg.get("profile", "gost-7.32"))
    pj = json.dumps({"required": list(prof.required_structure), "bib_dot": prof.bib_number_dot}, ensure_ascii=False)
    _py("qa/gost_check.py", "--project", str(root), "--docx", str(docx), "--out", str(out / "qa-gost.json"), "--quiet",
        "--profile-json", pj)
    _py("qa/compare_pdf.py", "--project", str(root), "--latex-pdf", str(out / f"{proj.name}.pdf"),
        "--docx-pdf", str(pdf_docx), "--out", str(out / "qa-compare.json"), "--quiet")

    def rj(n):
        try:
            return json.loads((out / n).read_text(encoding="utf-8"))
        except Exception:
            return {}

    chk, gost, cmp_ = rj("qa-check.json"), rj("qa-gost.json"), rj("qa-compare.json")
    issues = []
    cmp_items = [dict(i, severity="error") for i in cmp_.get("errors", []) if isinstance(i, dict)] + \
                [dict(i, severity="warning") for i in cmp_.get("warnings", []) if isinstance(i, dict)]
    for src, lst in (("DOCX", chk.get("issues", [])), ("ГОСТ", gost.get("issues", [])), ("сравнение с PDF", cmp_items)):
        for i in lst or []:
            sev = i.get("severity", "warning")
            if sev in ("manual", "info", "ok"):
                continue
            issues.append({"source": src, "severity": "error" if sev in ("error", "fail") else "warning",
                           "code": i.get("code") or i.get("id") or "", "message": i.get("message") or i.get("title") or str(i)})
    cov = (cmp_.get("counts") or {}).get("word_coverage")
    res = {"errors": sum(i["severity"] == "error" for i in issues), "warnings": sum(i["severity"] == "warning" for i in issues),
           "issues": issues[:100], "coverage": f"{cov:.0%}" if isinstance(cov, (int, float)) else cov,
           "pages": {"pdf": (cmp_.get("counts") or {}).get("latex_pages"), "docx": (cmp_.get("counts") or {}).get("docx_pages")},
           "fixable": any(i["code"].startswith("D") and i["severity"] == "error" for i in issues if i["source"] == "DOCX")}
    (out / "qa.json").write_text(json.dumps({"check": chk, "gost": gost, "compare": cmp_, "summary": res},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
    return res
