"""lde fix — применить исправления, предложенные линтером (только по явной команде).

Каждая правка — точечная замена в файле; перед заменой проверяется, что на месте стоит ровно тот
текст, который видел линтер. Пересекающиеся правки пропускаются (повторный запуск их доделает).
"""
from __future__ import annotations

from lde.check import run_check
from lde.project import Project

AUTO_RULES = ("qty-as-text", "qty-as-math", "number-typed", "ref-as-text", "unit-duplicated", "symbol-in-where")


def plan_fixes(proj: Project, rules=None, only=None):
    issues = run_check(proj)
    out = []
    for i in issues:
        if not i.fix or "start" not in i.fix:
            continue
        if rules and i.rule not in rules:
            continue
        if not rules and i.rule not in AUTO_RULES:
            continue
        if only and (i.fix["file"], i.fix["start"]) not in only:
            continue
        out.append(i)
    return out


def _declare(proj: Project, decls: list[dict]):
    """Объявить величины (из пояснений «где X – смысл, единица») в quantities.toml."""
    import json
    path = proj.root / "quantities.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    add = []
    for d in decls:
        if d["key"] in proj.quantities or f'["{d["key"]}"]' in text or f"[{d['key']}]" in text:
            continue
        add.append(f'[{json.dumps(d["key"], ensure_ascii=False)}]\nmeaning = {json.dumps(d["meaning"], ensure_ascii=False)}\n'
                   f'unit = {json.dumps(d["unit"], ensure_ascii=False)}\n')
    if add:
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        path.write_text(text + sep + "\n".join(add), encoding="utf-8")


def apply_fixes(proj: Project, issues) -> list:
    decls = [i.fix["declare"] for i in issues if i.fix.get("declare")]
    if decls:
        _declare(proj, decls)
    by_file: dict[str, list] = {}
    for i in issues:
        by_file.setdefault(i.fix["file"], []).append(i)
    applied = []
    for f, lst in by_file.items():
        p = proj.root / f
        text = p.read_text(encoding="utf-8")
        lst.sort(key=lambda i: -i.fix["start"])
        last_start = len(text) + 1
        for i in lst:
            fx = i.fix
            if fx["end"] > last_start:
                continue
            if text[fx["start"]:fx["end"]] != fx["old"]:
                continue
            text = text[:fx["start"]] + fx["new"] + text[fx["end"]:]
            last_start = fx["start"]
            applied.append(i)
        tmp = p.with_suffix(p.suffix + ".lde-tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    return applied


def fix_all(root, rules=None, rounds=3, log=print, dry=False, doc=None):
    total = []
    for _ in range(rounds):
        proj = Project(root, doc)
        plan = plan_fixes(proj, rules)
        if not plan:
            break
        if dry:
            return plan
        done = apply_fixes(proj, plan)
        total += done
        if not done:
            break
    return total
