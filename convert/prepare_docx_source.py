#!/usr/bin/env python3
"""prepare_docx_source.py — построить pandoc-friendly LaTeX-источник DOCX из
семантического LaTeX-проекта и план постобработки (*.plan.json).

Конвейер:
    sections/*.tex + metadata.tex + refs.bib
        -> out/docx-source.tex   (LaTeX для pandoc; статические номера)
        -> out/docx-source.plan.json (что и как постобработать в DOCX)

Поддерживается ровно управляемый диалект шаблона (см. macros.tex /
docx-preamble.tex). Всё, что вне диалекта, проходит как есть и попадает в
warnings — qa ловит сырую вёрстку.

Использование:
    prepare_docx_source.py --project <dir> [--out <out/docx-source.tex>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Команды, которых не должно быть в section-файлах (диалект): warning конвертеру.
RAW_LAYOUT = [
    r"\vspace", r"\hspace", r"\begin{figure}", r"\begin{table}",
    r"\begin{tabular}", r"\includegraphics", r"\clearpage", r"\newpage",
    r"\caption", r"\label", r"\ref{", r"\cite{",
]


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.warnings: list[str] = []
        self.text = ""

    # ------------------------------------------------------------- utils ----
    def read(self, rel: str) -> str:
        p = self.root / rel
        if not p.exists() and not p.suffix:
            p = p.with_suffix(".tex")
        if not p.exists():
            self.warnings.append(f"нет файла: {rel}")
            return ""
        return p.read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """Убрать LaTeX-комментарии (кроме экранированных \\%)."""
    out = []
    for line in text.splitlines(keepends=True):
        has_nl = line.endswith("\n")
        body = line[:-1] if has_nl else line
        i, n = 0, len(body)
        res = []
        while i < n:
            ch = body[i]
            if ch == "\\" and i + 1 < n:
                res.append(body[i:i + 2])
                i += 2
                continue
            if ch == "%":
                break
            res.append(ch)
            i += 1
        kept = "".join(res)
        if has_nl:
            kept += "\n"
        out.append(kept)
    return "".join(out)


def skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def read_group(s: str, i: int) -> tuple[str, int]:
    """Прочитать {...} с балансировкой скобок и учётом экранирования."""
    i = skip_ws(s, i)
    if i >= len(s) or s[i] != "{":
        raise ValueError(f"ожидалась '{{' в позиции {i}: ...{s[i:i+30]!r}")
    depth = 0
    j = i
    while j < len(s):
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("не закрыта фигурная скобка")


def read_star(s: str, i: int) -> tuple[bool, int]:
    i = skip_ws(s, i)
    if i < len(s) and s[i] == "*":
        return True, i + 1
    return False, i


def parse_braced_pairs(text: str, key: str) -> dict[str, str]:
    r"""Парсит `key = {value},` из \RdeSetup{...} (простые значения без вложенных скобок)."""
    res: dict[str, str] = {}
    m = re.search(r"\\RdeSetup\s*\{", text)
    if not m:
        return res
    body, _ = read_group(text, m.end() - 1)
    for km in re.finditer(r"(\w+)\s*=\s*\{([^{}]*)\}\s*,?", body, re.S):
        res[km.group(1)] = km.group(2).strip()
    return res


def latex_escape_plain(value: str) -> str:
    """Значения из metadata.tex уже LaTeX-экранированы init-скриптом; для DOCX оставляем."""
    return value


# ---------------------------------------------------------------- scanner ---
class Event:
    __slots__ = ("kind", "data")

    def __init__(self, kind: str, **data):
        self.kind = kind
        self.data = data


class Analyzer:
    """Единый проход по файлам: события в порядке документа + таблица меток."""

    def __init__(self, project: Project):
        self.p = project
        self.events: list[Event] = []
        self.labels: dict[str, str] = {}
        self.sec = 0
        self.sub = 0
        self.subsub = 0
        self.fig = 0
        self.tab = 0
        self.eq = 0
        self.app = 0
        self.total_fig = 0
        self.total_tab = 0
        self.total_eq = 0
        self.total_app = 0
        self.in_appendix = False

    # ---- счётчики ----
    def app_letter(self) -> str:
        letters = "АБВГДЕЖИКЛМНПРСТУФХЦЧШЩЭЮЯ"
        idx = self.app - 1
        if idx < len(letters):
            return letters[idx]
        return str(self.app)

    def next_figure_label(self) -> str:
        if self.in_appendix:
            return f"{self.app_letter()}.{self.fig}"
        return str(self.fig)

    # ---- разбор ----
    def analyze_files(self, files: list[str]) -> None:
        for rel in files:
            self.analyze_text(self.p.read(rel))
            self.events.append(Event("text", text="\n"))

    def analyze_text(self, text: str) -> None:
        s = strip_comments(text)
        n = len(s)
        i = 0
        buf_start = 0

        def flush(upto: int) -> None:
            nonlocal buf_start
            chunk = s[buf_start:upto]
            if chunk.strip():
                self.events.append(Event("text", text=chunk))
            buf_start = upto

        cmd_re = re.compile(
            r"\\(RdeTitlePage|RdeToc|RdeStructural|RdeAppendix|RdeBibliography|"
            r"RdeFigure|RdeFormula|RdeDesignation|RdeRef|RdePageref|RdeCite|RdeCites|"
            r"RdeInlineCode|RdeCode|section|subsection|subsubsection|chapter|input|include|"
            r"begin\{(?:RdeCode|RdeTable)\})"
        )
        while True:
            m = cmd_re.search(s, i)
            if not m:
                break
            name = m.group(1)
            i = m.end()
            flush(m.start())

            try:
                if name == "RdeTitlePage":
                    self.events.append(Event("titlepage"))
                elif name == "RdeToc":
                    self.events.append(Event("toc"))
                elif name == "RdeBibliography":
                    self.events.append(Event("bibliography"))
                elif name == "RdeStructural":
                    title, i = read_group(s, i)
                    self.events.append(Event("heading", level=1, text=title.strip(),
                                             number=None, structural=True, toc=True))
                elif name == "RdeAppendix":
                    title, i = read_group(s, i)
                    self.app += 1
                    self.total_app += 1
                    self.in_appendix = True
                    self.fig = 0
                    self.tab = 0
                    self.eq = 0
                    letter = self.app_letter()
                    self.events.append(Event("heading", level=1,
                                             text=f"ПРИЛОЖЕНИЕ {letter}",
                                             number=None, structural=True, toc=True,
                                             appendix=True, letter=letter))
                    if title.strip():
                        self.events.append(Event("appendix_title", text=title.strip()))
                elif name == "RdeFigure":
                    f, i = read_group(s, i)
                    cap, i = read_group(s, i)
                    label, i = read_group(s, i)
                    width, i = read_group(s, i)
                    self.fig += 1
                    self.total_fig += 1
                    num = self.next_figure_label()
                    self.labels[label.strip()] = num
                    self.events.append(Event("figure", path=f.strip(), caption=cap.strip(),
                                             label=label.strip(), width=width.strip(),
                                             number=num))
                elif name == "RdeFormula":
                    star, i = read_star(s, i)
                    expr, i = read_group(s, i)
                    label = ""
                    if not star:
                        try:
                            label, i2 = read_group(s, i)
                            i = i2
                        except ValueError:
                            self.p.warnings.append("\\RdeFormula без метки (номер всё равно будет)")
                    if star:
                        self.events.append(Event("equation", expr=expr.strip(), number=None,
                                                 label=None))
                    else:
                        self.eq += 1
                        self.total_eq += 1
                        num = f"{self.app_letter()}.{self.eq}" if self.in_appendix else str(self.eq)
                        if label:
                            self.labels[label.strip()] = num
                        self.events.append(Event("equation", expr=expr.strip(),
                                                 number=num, label=label.strip() or None))
                elif name == "RdeDesignation":
                    sym, i = read_group(s, i)
                    sub, i = read_group(s, i)
                    self.events.append(Event("designation", sym=sym.strip(), sub=sub.strip()))
                elif name in ("RdeRef", "RdePageref"):
                    label, i = read_group(s, i)
                    self.events.append(Event("ref", label=label.strip(),
                                             page=(name == "RdePageref")))
                elif name in ("RdeCite", "RdeCites"):
                    keys, i = read_group(s, i)
                    self.events.append(Event("cite", keys=keys.strip()))
                elif name == "RdeInlineCode":
                    code, i = read_group(s, i)
                    self.events.append(Event("inline_code", code=code))
                elif name == "RdeCode":
                    # \begin{RdeCode}{lang} ... \end{RdeCode}
                    lang, i = read_group(s, i)
                    endm = re.search(r"\\end\{RdeCode\}", s[i:])
                    if not endm:
                        raise ValueError("не закрыто окружение RdeCode")
                    body = s[i:i + endm.start()]
                    i = i + endm.end()
                    self.events.append(Event("code", lang=lang.strip(), body=body.strip("\n")))
                elif name.startswith("begin{"):
                    env = name[len("begin{"):-1]
                    if env == "RdeCode":
                        star, i = read_star(s, i)
                        try:
                            lang, i = read_group(s, i)
                        except ValueError:
                            lang = ""
                        endm = re.search(r"\\end\{RdeCode\}", s[i:])
                        if not endm:
                            raise ValueError("не закрыто окружение RdeCode")
                        body = s[i:i + endm.start()]
                        i = i + endm.end()
                        self.events.append(Event("code", lang=str(lang).strip(),
                                                 body=body.strip("\n")))
                    elif env == "RdeTable":
                        cap, i = read_group(s, i)
                        label, i = read_group(s, i)
                        endm = re.search(r"\\end\{RdeTable\}", s[i:])
                        if not endm:
                            raise ValueError("не закрыто окружение RdeTable")
                        body = s[i:i + endm.start()]
                        i = i + endm.end()
                        self.tab += 1
                        self.total_tab += 1
                        num = f"{self.app_letter()}.{self.tab}" if self.in_appendix else str(self.tab)
                        self.labels[label.strip()] = num
                        self.events.append(Event("table", caption=cap.strip(),
                                                 label=label.strip(), body=body.strip(),
                                                 number=num))
                elif name in ("section", "subsection", "subsubsection", "chapter"):
                    level = {"chapter": 1, "section": 1, "subsection": 2,
                             "subsubsection": 3}[name]
                    star, i = read_star(s, i)
                    title, i = read_group(s, i)
                    num = None
                    if not star:
                        if level == 1:
                            self.sec += 1
                            self.sub = 0
                            self.subsub = 0
                            num = str(self.sec)
                        elif level == 2:
                            self.sub += 1
                            self.subsub = 0
                            num = f"{self.sec}.{self.sub}"
                        else:
                            self.subsub += 1
                            num = f"{self.sec}.{self.sub}.{self.subsub}"
                    # \label сразу после заголовка (если есть)
                    self.events.append(Event("heading", level=level, text=title.strip(),
                                             number=num, structural=False, toc=not star))
                    # метки разделов: ищем \label{...} до конца строки
                    lm = re.match(r"[^\n]*?\\label\{([^}]*)\}", s[i:])
                    if lm and num:
                        self.labels[lm.group(1).strip()] = num
                elif name in ("input", "include"):
                    rel, i = read_group(s, i)
                    self.analyze_text(self.p.read(rel.strip()))
                else:  # pragma: no cover
                    self.p.warnings.append(f"неизвестная команда: {name}")
            except ValueError as exc:
                self.p.warnings.append(f"ошибка разбора \\{name}: {exc}")
            buf_start = i

        flush(n)

    # ---- финализация ----
    def titlepage_events(self) -> list[Event]:
        return [e for e in self.events if e.kind == "titlepage"]


# ---------------------------------------------------------------- render ----
def inline_expand(text: str, labels: dict[str, str], warnings: list[str]) -> str:
    """Подставить inline-макросы \\RdeRef / \\RdeCite / \\RdeDesignation /
    \\RdeInlineCode / \\RdePageref внутри текстового фрагмента."""
    out = text
    # несколько проходов — макросы могут быть вложены не более одного раза
    for _ in range(3):
        changed = False
        i = 0
        res = []
        while i < len(out):
            m = re.match(r"\\(RdeRef|RdePageref|RdeDesignation|RdeInlineCode|RdeCite|RdeCites)",
                         out[i:])
            if not m:
                res.append(out[i])
                i += 1
                continue
            name = m.group(1)
            j = i + m.end()
            try:
                if name in ("RdeRef", "RdePageref", "RdeCite", "RdeCites"):
                    arg, j = read_group(out, j)
                else:
                    arg, j = read_group(out, j)
                    arg2, j = read_group(out, j)
            except ValueError:
                res.append(out[i])
                i += 1
                continue
            if name in ("RdeRef", "RdePageref"):
                label = arg.strip()
                if label in labels:
                    if name == "RdePageref":
                        warnings.append(f"\\RdePageref{{{label}}}: номера страниц в DOCX не поддерживаются — подставлено '?'")
                        res.append("?")
                    else:
                        res.append(labels[label])
                else:
                    warnings.append(f"неизвестная метка для \\{name}: {label}")
                    res.append("??")
            elif name in ("RdeCite", "RdeCites"):
                res.append("\\cite{%s}" % arg.strip())
            elif name == "RdeDesignation":
                sym = arg.strip()
                sub = arg2.strip()
                res.append("\\emph{%s}\\textsubscript{%s}" % (sym, sub))
            else:  # RdeInlineCode
                res.append("\\texttt{%s}" % arg)
            changed = True
            i = j
        out = "".join(res)
        if not changed:
            break
    return out


def render(analyzer: Analyzer, metadata: dict[str, str], project: Project,
           src_preamble: str) -> tuple[str, dict]:
    labels = analyzer.labels
    warnings = project.warnings
    out: list[str] = []
    headings: list[dict] = []
    display_math: list[dict] = []
    appendix_titles: list[dict] = []
    pending: list[str] = []

    def flush() -> None:
        chunk = "".join(pending)
        pending.clear()
        chunk = chunk.rstrip()
        if chunk.strip():
            out.append(chunk + "\n\n")

    out.append(src_preamble)
    out.append("\n")

    # ---- титульный лист: каждый реквизит — отдельный абзац ----
    titlepage: list[dict] = []

    def tp(text: str, align: str = "center", bold: bool = False,
           size: int = 14, space_before: int = 0) -> None:
        if not text:
            return
        titlepage.append({"text": text, "align": align, "bold": bold,
                          "size_pt": size, "space_before_pt": space_before})
        out.append(text)
        out.append("\n\n")

    ministry = metadata.get("ministry", "")
    org = metadata.get("org", "")
    orgshort = metadata.get("orgshort", "")
    doctype = metadata.get("doctype", "ОТЧЁТ О НАУЧНО-ИССЛЕДОВАТЕЛЬСКОЙ РАБОТЕ")
    title = metadata.get("title", "Отчёт")
    subtitle = metadata.get("subtitle", "")
    worktype = metadata.get("worktype", "Заключительный")
    author = metadata.get("author", "")
    supervisor = metadata.get("supervisor", "")
    city = metadata.get("city", "Москва")
    year = metadata.get("year", "2026")
    udc = metadata.get("udc", "")
    regnum = metadata.get("regnum", "")

    tp(ministry, bold=False, space_before=0)
    tp(org.upper(), bold=True)
    tp(f"({orgshort.upper()})" if orgshort else "", bold=False)
    tp(f"УДК {udc}" if udc else "", bold=False, space_before=36)
    tp(f"Регистрационный номер: {regnum}" if regnum else "", bold=False)
    tp(doctype.upper(), bold=True, space_before=36)
    tp(f"по теме: {subtitle}" if subtitle else "", bold=False, space_before=24)
    tp(title.upper(), bold=True, space_before=12)
    tp(f"({worktype})", bold=False, space_before=12)
    if author:
        tp(f"Исполнитель: {author}", align="right", space_before=48)
    if supervisor:
        tp(f"Руководитель НИР: {supervisor}", align="right", space_before=24)
    tp(f"{city} {year}", align="center", space_before=180)

    # ---- блоки и inline-события в порядке документа ----
    for ev in analyzer.events:
        k = ev.kind
        d = ev.data
        if k == "text":
            pre = d["text"]
            for raw in RAW_LAYOUT:
                if raw in pre:
                    warnings.append(f"сырая вёрстка в sections/: {raw}")
                    break
            pending.append(inline_expand(pre, labels, warnings))
        elif k == "ref":
            if d["label"] in labels:
                pending.append("?" if d["page"] else labels[d["label"]])
            else:
                warnings.append(f"неизвестная метка \\RdeRef: {d['label']}")
                pending.append("??")
        elif k == "cite":
            pending.append(f"\\cite{{{d['keys']}}}")
        elif k == "designation":
            pending.append(f"\\emph{{{d['sym']}}}\\textsubscript{{{d['sub']}}}")
        elif k == "inline_code":
            pending.append(f"\\texttt{{{d['code']}}}")
        elif k == "titlepage":
            continue
        elif k == "toc":
            continue
        elif k == "bibliography":
            flush()
            bib_heading = "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ"
            headings.append({"level": 1, "text": bib_heading,
                             "structural": True, "appendix": False,
                             "toc": True, "inserted": True})
        else:
            flush()
            if k == "heading":
                text = d["text"]
                number = d["number"]
                structural = d.get("structural", False)
                full = text.upper() if structural else (f"{number} {text}" if number else text)
                level = d["level"]
                cmd = {1: "section", 2: "subsection", 3: "subsubsection"}[level]
                out.append(f"\\{cmd}{{{full}}}\n\n")
                if d.get("appendix"):
                    appendix_titles.append({"heading": full, "title": None})
                headings.append({"level": level, "text": full,
                                 "structural": structural,
                                 "appendix": bool(d.get("appendix")),
                                 "toc": bool(d.get("toc"))})
            elif k == "appendix_title":
                out.append(f"\\textbf{{{d['text']}}}\n\n")
                appendix_titles.append({"heading": None, "title": d["text"]})
            elif k == "figure":
                path = project.root / d["path"]
                if not path.exists():
                    warnings.append(f"рисунок не найден: {d['path']}")
                else:
                    out.append(
                        "\\begin{figure}[htbp]\n"
                        "\\centering\n"
                        f"\\includegraphics[width={d['width']}\\textwidth]{{{path}}}\n"
                        f"\\caption{{Рисунок {d['number']} --- {d['caption']}}}\n"
                        "\\end{figure}\n\n")
            elif k == "table":
                out.append(
                    "\\begin{table}[htbp]\n"
                    f"\\caption{{Таблица {d['number']} --- {d['caption']}}}\n"
                    f"{d['body']}\n"
                    "\\end{table}\n\n")
            elif k == "equation":
                env = "equation" if d["number"] else "equation*"
                out.append(f"\\begin{{{env}}}\n{d['expr']}\n\\end{{{env}}}\n\n")
                display_math.append({"numbered": bool(d["number"]),
                                     "number": d["number"]})
            elif k == "code":
                out.append(f"\\begin{{verbatim}}\n{d['body']}\n\\end{{verbatim}}\n\n")
            else:  # pragma: no cover
                warnings.append(f"неизвестное событие: {k}")
    flush()

    section_files = []
    main_tex = project.read("main.tex")
    for m in re.finditer(r"\\(?:input|include)\{([^}]+)\}", main_tex):
        rel = m.group(1).strip()
        if rel.startswith("sections/"):
            section_files.append(rel)

    plan = {
        "version": 1,
        "generated_by": "convert/prepare_docx_source.py",
        "project": str(project.root),
        "metadata": metadata,
        "titlepage": titlepage,
        "headings": [h for h in headings if h.get("toc", True)],
        "display_math": [e for e in display_math],
        "appendix_titles": appendix_titles,
        "appendix_heading": next((h["text"] for h in headings if h.get("appendix")), None),
        "counters": {
            "sections": analyzer.sec,
            "figures": analyzer.total_fig,
            "tables": analyzer.total_tab,
            "equations": analyzer.total_eq,
            "appendices": analyzer.total_app,
        },
        "labels": labels,
        "toc_heading": "СОДЕРЖАНИЕ",
        "bibliography_heading": "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
        "warnings": warnings,
    }
    return ("".join(out).rstrip() + "\n"), plan


# ------------------------------------------------------------------ main ----
def main() -> int:
    ap = argparse.ArgumentParser(description="Build pandoc-oriented LaTeX source + plan")
    ap.add_argument("--project", required=True, help="каталог LDE-проекта (с main.tex)")
    ap.add_argument("--out", default=None, help="куда писать source (по умолчанию <proj>/out/docx-source.tex)")
    ap.add_argument("--plan", default=None, help="куда писать plan json")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not (root / "main.tex").exists():
        print(f"prepare_docx_source: нет main.tex в {root}", file=sys.stderr)
        return 2
    project = Project(root)

    main_text = project.read("main.tex")
    section_files = []
    for m in re.finditer(r"\\(?:input|include)\{([^}]+)\}", main_text):
        rel = m.group(1).strip()
        if rel.startswith("sections/"):
            section_files.append(rel)
    if not section_files:
        print("prepare_docx_source: в main.tex не найдены \\input{sections/...}", file=sys.stderr)
        return 2

    metadata = parse_braced_pairs(project.read("metadata.tex"), "RdeSetup")
    preamble_path = root / "docx-preamble.tex"
    src_preamble = preamble_path.read_text(encoding="utf-8") if preamble_path.exists() else ""

    analyzer = Analyzer(project)
    analyzer.analyze_files(section_files)
    source, plan = render(analyzer, metadata, project, src_preamble)

    out_path = Path(args.out) if args.out else root / "out" / "docx-source.tex"
    plan_path = Path(args.plan) if args.plan else out_path.with_suffix(".plan.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source, encoding="utf-8")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"docx-source: {out_path}")
    print(f"plan:        {plan_path}")
    print(f"  разделов: {plan['counters']['sections']}, рисунков: {plan['counters']['figures']}, "
          f"таблиц: {plan['counters']['tables']}, формул: {plan['counters']['equations']}, "
          f"приложений: {plan['counters']['appendices']}")
    if plan["warnings"]:
        print(f"  предупреждений: {len(plan['warnings'])}")
        for w in plan["warnings"][:10]:
            print(f"    - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
