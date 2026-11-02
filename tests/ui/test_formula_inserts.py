"""Studio: вставка величины и числа расчёта в формулу кнопками (настоящие клики, headless Chrome).

    toolchain/venv/bin/python tests/ui/test_formula_inserts.py
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome, StudioServer  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


with tempfile.TemporaryDirectory(prefix="lde-ui-fins-") as td:
    root = Path(td) / "r"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    srv = StudioServer(str(root))
    c = Chrome(srv.url)
    try:
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        # выносная формула (2): f_ср = 1/(2πRC)
        c.click('#tabs button[data-tab="objects"]')
        c.click('#side-list .item[data-uid="eq:fc"]')
        c.wait("document.querySelector('#panel math-field') && document.querySelector('#mf-add-q')", 10, "редактор формулы")
        time.sleep(0.6)
        c.click("#panel math-field", 0.3, 0.5)
        c.key("End", "End", 0, 35)
        c.keys("=")
        c.click("#mf-add-val")
        c.wait("document.querySelector('#dialog2 .pick .item')", 10, "выбор числа")
        c.click("#dialog2 input.inp")
        c.keys("f_c")
        time.sleep(0.5)
        c.wait("document.querySelector('#dialog2 .pick').dataset.q === 'val:f_c' && document.querySelector('#dialog2 .pick .item')", 10, "число найдено (фильтр применён)")
        c.click("#dialog2 .pick .item")
        time.sleep(0.3)
        c.click("#mf-add-q")
        c.wait("document.querySelector('#dialog2 .pick .item')", 10, "выбор величины")
        c.click("#dialog2 input.inp")
        c.keys("ёмкость")
        c.wait("document.querySelector('#dialog2 .pick').dataset.q === 'q:ёмкость' && document.querySelector('#dialog2 .pick .item')", 10, "величина найдена")
        c.click("#dialog2 .pick .item")
        time.sleep(0.3)
        ml = c.eval("document.querySelector('#panel math-field').value")
        step("в MathLive — связанные вставки", "lde=v:afc.f_c" in ml and "lde=q:C" in ml, ml)
        c.click("#save-btn")
        c.wait("!S.dirty && document.querySelector('#build-state').innerText.includes('обновлён')", 30, "сохранение и сборка")
        time.sleep(1.5)
        src = (root / "text" / "02-model.md").read_text(encoding="utf-8")
        line = next(l for l in src.splitlines() if "eq:fc" in l)
        step("в исходнике \\val{afc.f_c} и \\q{C}", "\\val{afc.f_c}" in line and "\\q{C}" in line and "htmlData" not in line, line)
        pdf = subprocess.run(["pdftotext", str(root / "out" / "report.pdf"), "-"], capture_output=True, text=True).stdout
        step("в PDF формула с числом расчёта («= 1592 Гц»)", "1592 Гц" in pdf.replace(" ", " "), "")
        chk = subprocess.run([str(REPO / "bin" / "lde"), "check", "-C", str(root)], capture_output=True, text=True).stdout
        step("lde check: замечаний нет", "Замечаний нет" in chk, chk)
        # повторное открытие: вставки снова связанные
        c.key("Escape")
        c.click('#side-list .item[data-uid="eq:fc"]')
        c.wait("document.querySelector('#panel math-field')", 10)
        time.sleep(0.6)
        step("при повторном открытии вставки остаются связанными", "lde=q:C" in c.eval("document.querySelector('#panel math-field').value"))
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
