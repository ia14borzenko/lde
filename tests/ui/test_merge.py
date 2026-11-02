"""Слияние по блокам: настоящий `git merge` двух веток → автослияние + конфликт → решение в Studio.

    toolchain/venv/bin/python tests/ui/test_merge.py
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome, StudioServer  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LDE = str(REPO / "bin" / "lde")
ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def git(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)


BASE = """# Заключение {-}

Первый абзац: фильтр подавляет высокие частоты и пропускает низкие.

Второй абзац правят обе ветки одинаково плохо.
"""

with tempfile.TemporaryDirectory(prefix="lde-ui-merge-") as td:
    root = Path(td) / "r"
    subprocess.run([LDE, "init", str(root), "--no-build"], capture_output=True, check=True)
    z = root / "text" / "03-zakl.md"
    z.write_text(BASE, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "база")
    main = git(root, "branch", "--show-current").stdout.strip()
    git(root, "checkout", "-qb", "a")
    z.write_text(BASE.replace("фильтр", "RC-фильтр").replace("одинаково плохо", "по-своему (А)"), encoding="utf-8")
    git(root, "commit", "-qam", "ветка А")
    git(root, "checkout", "-q", main)
    z.write_text(BASE.replace("низкие.", "низкие частоты.").replace("одинаково плохо", "по-своему (Б)"), encoding="utf-8")
    git(root, "commit", "-qam", "основная")
    r = git(root, "merge", "a", "-m", "слияние")
    t = z.read_text(encoding="utf-8")
    step("git merge: драйвер LDE подключён, пословное автослияние абзаца",
         "RC-фильтр подавляет высокие частоты и пропускает низкие частоты." in t, t)
    step("пересекающаяся правка — конфликтный блок", "<<<<<<< моё" in t and t.count("<<<<<<<") == 1, t)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "слияние с конфликтом")
    chk = subprocess.run([LDE, "check", "-C", str(root)], capture_output=True, text=True).stdout
    step("lde check: ошибка merge-conflict", "конфликт слияния" in chk, chk[-300:])

    srv = StudioServer(str(root))
    c = Chrome(srv.url)
    try:
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён') || document.querySelector('#build-state').innerText.includes('не обновлён')", 180, "сборка")
        time.sleep(1.0)
        step("PDF собран, несмотря на конфликт", (root / "out" / "report.pdf").exists())
        uid = c.send("Runtime.evaluate", {"expression": "fetch('/api/issues').then(r => r.json()).then(a => (a.find(i => i.rule === 'merge-conflict') || {}).element)",
                                          "awaitPromise": True, "returnByValue": True})["result"].get("value")
        step("конфликт в списке замечаний Studio", bool(uid), uid)
        c.eval(f"openElement({uid!r})")
        c.wait("document.querySelector('#cf-theirs')", 10, "окно конфликта")
        cols = c.eval("[...document.querySelectorAll('.cf-col')].map(e => e.textContent).join(' | ')")
        step("окно показывает оба варианта", "(А)" in cols and "(Б)" in cols, cols)
        c.click("#cf-theirs")
        time.sleep(1.5)
        t = z.read_text(encoding="utf-8")
        step("«Взять чужое» решило конфликт", "<<<<<<<" not in t and "по-своему (А)" in t and "по-своему (Б)" not in t, t)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
