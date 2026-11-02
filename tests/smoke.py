"""Сквозной smoke на шаблоне `lde init` — через CLI, как человек/агент. Без окон на рабочем столе.

    toolchain/venv/bin/python tests/smoke.py

Шаги: init (с расчётом) → check чисто → build → правка текста с величиной «текстом» → check ловит →
fix → чисто → смена параметра → status «устарело» → update → submit «можно сдавать» → undo.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LDE = str(REPO / "bin" / "lde")
ok = True


def run(*a, cwd=None):
    return subprocess.run([LDE, *a], cwd=cwd, capture_output=True, text=True, timeout=900)


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    ok = ok and cond


with tempfile.TemporaryDirectory(prefix="lde-smoke-") as td:
    p = Path(td) / "report"
    r = run("init", str(p))
    step("init: проект создан, расчёт выполнен, PDF собран", r.returncode == 0 and (p / "out" / "report.pdf").exists(), r.stdout + r.stderr)
    r = run("check", "-C", str(p))
    step("check на чистом шаблоне: замечаний нет", "Замечаний нет" in r.stdout, r.stdout)
    f = p / "text" / "02-model.md"
    f.write_text(f.read_text(encoding="utf-8") + "\nПри fср = 1592 Гц и R = 1000 Ом фильтр работает, см. рисунок 1.\n", encoding="utf-8")
    r = run("check", "--json", "-C", str(p))
    rules = {i["rule"] for i in json.loads(r.stdout)["issues"]}
    step("check ловит величину текстом, число руками, ссылку текстом",
         {"qty-as-text", "number-typed", "ref-as-text"} <= rules, str(rules))
    r = run("fix", "-C", str(p))
    txt = f.read_text(encoding="utf-8")
    step("fix: вставки вместо текста", "{f_ср} = {=afc.f_c}" in txt and "@fig:afc" in txt, txt[-200:])
    r = run("check", "-C", str(p))
    step("после fix — замечаний нет", "Замечаний нет" in r.stdout, r.stdout)
    # число из литературы (в предложении со ссылкой) и грубая оценка — не «число расчёта»
    f.write_text(f.read_text(encoding="utf-8") + "\nПо справочнику частота среза таких фильтров составляет 1592 Гц [@horowitz2014, с. 45]. "
                 "Порядок частоты среза — около 2 кГц.\n", encoding="utf-8")
    r = run("check", "--json", "-C", str(p))
    nt = [i["message"] for i in json.loads(r.stdout)["issues"] if i["rule"] == "number-typed"]
    step("число со ссылкой на источник и грубая оценка («2 кГц» при 1,59 кГц) — без замечаний", not nt, nt)
    f.write_text(f.read_text(encoding="utf-8").replace("Порядок частоты среза — около 2 кГц.", "Частота среза — 1,59 кГц."), encoding="utf-8")
    r = run("check", "--json", "-C", str(p))
    nt = [i["message"] for i in json.loads(r.stdout)["issues"] if i["rule"] == "number-typed"]
    step("точное округление («1,59 кГц») по-прежнему ловится", len(nt) == 1 and "1,59" in nt[0], nt)
    run("fix", "-C", str(p))
    t = (p / "calc" / "afc" / "calc.toml").read_text(encoding="utf-8")
    (p / "calc" / "afc" / "calc.toml").write_text(t.replace("value = 1000.0", "value = 2000.0"), encoding="utf-8")
    r = run("status", "-C", str(p))
    step("status: причина устаревания названа", "изменён параметр R: 1000 → 2000" in r.stdout, r.stdout)
    r = run("submit", "-C", str(p))
    step("submit: пересчёт, PDF, DOCX, «можно сдавать»", r.returncode == 0 and "Можно сдавать" in r.stdout
         and (p / "out" / "report.docx").exists(), r.stdout[-800:])
    pdf = subprocess.run(["pdftotext", str(p / "out" / "report.pdf"), "-"], capture_output=True, text=True).stdout
    step("в PDF новое значение (795,8 Гц)", "795,8" in pdf)
    run("save", "-m", "smoke", "-C", str(p))
    r = run("undo", "-C", str(p))
    step("undo отменяет последнюю точку", r.returncode == 0 and "Отменено" in r.stdout, r.stdout + r.stderr)

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
