#!/usr/bin/env python3
"""qa.py — единая приёмка LDE-проекта: структура DOCX + ГОСТ + сравнение PDF
+ статическая проверка диалекта sections/*.tex.

Запускается из `bin/lde qa [--strict]`. Пишет сводный отчёт out/qa.json.
Код возврата: 0 — ok; 1 — есть ошибки (а со --strict — и предупреждения).
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# Запрещённая «сырая вёрстка» в sections/*.tex (docs/agent-rules.md).
FORBIDDEN = [
    (r"\\vspace", "вертикальный отступ — используйте структуру/макросы"),
    (r"\\hspace", "горизонтальный отступ — используйте макросы"),
    (r"\\clearpage|\\newpage|\\pagebreak", "управление страницами — за это отвечают макросы/стили"),
    (r"\\begin\{figure\}", "рисунок напрямую — используйте \\RdeFigure"),
    (r"\\begin\{table\}", "таблица напрямую — используйте окружение RdeTable"),
    (r"\\includegraphics", "вставка картинки напрямую — используйте \\RdeFigure"),
    (r"\\caption\b", "подпись напрямую — задаётся макросом"),
    (r"\\label\s*\{", "метка напрямую — задаётся макросом"),
    (r"\\ref\s*\{", "ссылка напрямую — используйте \\RdeRef"),
    (r"\\cite\s*\{", "цитирование напрямую — используйте \\RdeCite"),
    (r"\\begin\{center\}|\\begin\{flushleft\}|\\begin\{flushright\}", "выравнивание вручную — не нужно"),
    (r"\\centering", "\\\\centering вручную — не нужно"),
    (r"\\bfseries|\\itshape|\\large|\\Large|\\footnotesize", "прямое изменение шрифта — используйте стили"),
    (r"\\setlength|\\renewcommand|\\newcommand|\\def\\", "изменение настроек в разделе — только preamble/macros"),
    (r"\\documentclass|\\usepackage", "преамбула в разделе — недопустимо"),
]
# \begin{tabular} разрешён только внутри RdeTable (проверяется отдельно).


def static_check(project: Path) -> dict:
    issues: list[dict] = []
    sections = sorted((project / "sections").glob("*.tex")) if (project / "sections").exists() else []
    if not sections:
        issues.append({"severity": "error", "code": "STATIC",
                       "message": "нет каталога sections/ с .tex файлами", "file": ""})
    for f in sections:
        text = f.read_text(encoding="utf-8")
        # убираем комментарии
        lines = []
        for line in text.splitlines():
            out, esc = [], False
            for ch in line:
                if ch == "\\" and not esc:
                    esc = True
                    out.append(ch)
                    continue
                if ch == "%" and not esc:
                    break
                esc = False
                out.append(ch)
            lines.append("".join(out))
        clean = "\n".join(lines)
        for pattern, why in FORBIDDEN:
            for m in re.finditer(pattern, clean):
                lineno = clean[:m.start()].count("\n") + 1
                issues.append({"severity": "error", "code": "STATIC-DIALECT",
                               "message": f"сырая вёрстка {m.group(0)!r} ({why})",
                               "file": f.name, "line": lineno})
        # tabular только внутри RdeTable
        depth = 0
        for m in re.finditer(r"\\begin\{(RdeTable|tabular)\}|\\end\{(RdeTable|tabular)\}", clean):
            beg, end = m.group(1), m.group(2)
            if beg:
                if beg == "RdeTable":
                    depth += 1
                elif depth == 0:
                    lineno = clean[:m.start()].count("\n") + 1
                    issues.append({"severity": "error", "code": "STATIC-DIALECT",
                                   "message": "\\begin{tabular} вне окружения RdeTable",
                                   "file": f.name, "line": lineno})
            else:
                if end == "RdeTable" and depth > 0:
                    depth -= 1
    return {"files": [f.name for f in sections], "issues": issues,
            "errors": sum(1 for i in issues if i["severity"] == "error"),
            "warnings": sum(1 for i in issues if i["severity"] != "error")}


def run(cmd: list[str], label: str) -> tuple[int, dict | None]:
    print(f"[qa] {label}...", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout.strip():
        print(proc.stdout.strip(), file=sys.stderr)
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode, None


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--docx", default=None, help="проверяемый DOCX (по умолчанию out/main.docx)")
    ap.add_argument("--docx-pdf", default=None, help="рендер DOCX->PDF (out/main.docx.pdf)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    proj = Path(args.project).resolve()
    if not (proj / "main.tex").exists():
        print(f"qa: не LDE-проект (нет main.tex): {proj}", file=sys.stderr)
        return 2

    out_dir = proj / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []
    warnings: list[dict] = []
    checks: dict = {}

    # 1) статическая проверка диалекта
    st = static_check(proj)
    checks["static"] = st
    for i in st["issues"]:
        item = {"source": "static", **i}
        (errors if i["severity"] == "error" else warnings).append(item)

    # 2) артефакты
    pdf = out_dir / "main.pdf"
    docx = Path(args.docx) if args.docx else out_dir / "main.docx"
    docx_pdf = Path(args.docx_pdf) if args.docx_pdf else out_dir / "main.docx.pdf"
    if not pdf.exists():
        errors.append({"source": "artifacts", "severity": "error",
                       "message": "нет out/main.pdf — выполните lde build"})
    if not docx.exists():
        errors.append({"source": "artifacts", "severity": "error",
                       "message": "нет out/main.docx — выполните lde docx"})

    # 3) проверки DOCX, ГОСТ, сравнение PDF
    if docx.exists():
        rc, _ = run([PY, str(ROOT / "qa" / "check_docx.py"), "--project", str(proj),
                     "--docx", str(docx),
                     "--pdf", str(docx_pdf) if docx_pdf.exists() else "",
                     "--out", str(out_dir / "qa-check.json"), "--quiet"], "check_docx")
        checks["docx"] = read_json(out_dir / "qa-check.json")
        for i in checks["docx"].get("issues", []):
            item = {"source": "docx", "severity": i.get("severity", "error"), **i}
            (errors if item["severity"] == "error" else warnings).append(item)

        rc, _ = run([PY, str(ROOT / "qa" / "gost_check.py"), "--project", str(proj),
                     "--docx", str(docx),
                     "--out", str(out_dir / "qa-gost.json"), "--quiet"], "gost_check")
        checks["gost"] = read_json(out_dir / "qa-gost.json")
        for i in checks["gost"].get("issues", []):
            item = {"source": "gost", "severity": i.get("severity", "error"), **i}
            (errors if item["severity"] == "error" else warnings).append(item)

    if pdf.exists() and docx_pdf.exists():
        run([PY, str(ROOT / "qa" / "compare_pdf.py"), "--project", str(proj),
             "--docx-pdf", str(docx_pdf),
             "--out", str(out_dir / "qa-compare.json"), "--quiet"], "compare_pdf")
        checks["compare"] = read_json(out_dir / "qa-compare.json")
        for i in checks["compare"].get("errors", []):
            errors.append({"source": "compare", "severity": "error", **i})
        for i in checks["compare"].get("warnings", []):
            warnings.append({"source": "compare", "severity": "warning", **i})

    # 4) отчёт конвертера
    conv = read_json(out_dir / "main.docx.report.json")
    if conv:
        checks["converter"] = {"status": conv.get("status"),
                               "math_mode": conv.get("math_mode"),
                               "counters": conv.get("counters"),
                               "warnings": conv.get("converter_warnings", [])
                               + conv.get("postprocess", {}).get("warnings", [])}
        if conv.get("status") == "warnings":
            for w in checks["converter"]["warnings"]:
                warnings.append({"source": "converter", "severity": "warning",
                                 "message": str(w)[:300]})

    status = "errors" if errors else ("warnings" if warnings else "ok")
    report = {
        "project": str(proj),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "strict": bool(args.strict),
        "status": status,
        "artifacts": {
            "latex_pdf": str(pdf) if pdf.exists() else None,
            "docx": str(docx) if docx.exists() else None,
            "docx_pdf": str(docx_pdf) if docx_pdf.exists() else None,
        },
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "summary": {
            "errors": len(errors), "warnings": len(warnings),
            "editor_manual": (checks.get("gost", {}).get("manual", [])),
        },
    }
    qa_path = out_dir / "qa.json"
    qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 66, file=sys.stderr)
    print(f"QA: {status.upper()}  (ошибок {len(errors)}, предупреждений {len(warnings)})",
          file=sys.stderr)
    for e in errors[:15]:
        print(f"  [ОШИБКА] {e.get('source')}/{e.get('id', e.get('code', ''))}: "
              f"{e.get('message', '')[:200]}", file=sys.stderr)
    for w in warnings[:15]:
        print(f"  [warning] {w.get('source')}: {w.get('message', '')[:200]}",
              file=sys.stderr)
    if checks.get("compare"):
        c = checks["compare"].get("counts", {})
        print(f"  PDF: покрытие текста {c.get('word_coverage')}, страниц LaTeX "
              f"{c.get('latex_pages')} / DOCX {c.get('docx_pages')}", file=sys.stderr)
    print(f"отчёт: {qa_path}", file=sys.stderr)
    print("=" * 66, file=sys.stderr)
    print("Пояснение: ошибки блокируют приёмку; предупреждения T3 (вёрстка Word ≠ TeX) "
          "допустимы, см. docs/conversion.md", file=sys.stderr)

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
