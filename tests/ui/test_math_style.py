"""Studio: дроби в редакторе формул — полного размера, как в MathType и в PDF LDE.

    toolchain/venv/bin/python tests/ui/test_math_style.py

Проверяется: показ \\frac как \\dfrac, новая дробь (клавиша «/») тоже полная и каретка остаётся в
знаменателе, в исходнике после сохранения — \\frac; в PDF дробь в тексте и интеграл — по правилам LDE.
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


with tempfile.TemporaryDirectory(prefix="lde-ui-math-") as td:
    root = Path(td) / "r"
    subprocess.run([LDE, "init", str(root)], capture_output=True, check=True)
    srv = StudioServer(str(root))
    c = Chrome(srv.url)
    try:
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        c.click('#tabs button[data-tab="objects"]')
        c.click('#side-list .item[data-uid="eq:fc"]')
        c.wait("document.querySelector('#panel math-field')", 10, "редактор формулы")
        time.sleep(0.6)
        v = c.eval("document.querySelector('#panel math-field').value")
        tex = c.eval("document.querySelector('#panel textarea').value")
        step("в редакторе дробь показана полной (\\dfrac)", "\\dfrac" in v, v)
        step("в поле LaTeX — исходный \\frac", "\\frac" in tex and "\\dfrac" not in tex, tex)
        ratio = c.eval("""(() => { const mf = document.querySelector('#panel math-field');
            const sc = mf.shadowRoot.querySelector('[style*="font-size: 70%"]');
            const base = mf.shadowRoot.querySelector('.ML__latex') || mf.shadowRoot.querySelector('.ML__content');
            return parseFloat(getComputedStyle(sc).fontSize) / parseFloat(getComputedStyle(base).fontSize); })()""")
        step("индекс в редакторе — 58 % (как в MathType)", abs(ratio - 0.58) < 0.02, ratio)
        ratio2 = c.eval("""(() => { const sc = document.querySelector('#side-list .mathline [style*="font-size: 70%"]');
            const base = sc.closest('.ML__latex');
            return parseFloat(getComputedStyle(sc).fontSize) / parseFloat(getComputedStyle(base).fontSize); })()""")
        step("индекс в списке объектов — 58 %", abs(ratio2 - 0.58) < 0.02, ratio2)
        c.click("#panel math-field", 0.3, 0.5)
        c.key("End", "End", 0, 35)
        c.keys("+a/b")
        time.sleep(0.3)
        c.keys("c")
        time.sleep(0.3)
        v = c.eval("document.querySelector('#panel math-field').value")
        step("новая дробь «/» — тоже полная, каретка осталась в знаменателе", "\\dfrac{a}{bc}" in v, v)
        c.click("#save-btn")
        c.wait("!S.dirty", 10, "сохранено")
        time.sleep(1.0)
        line = next(ln for ln in (root / "text" / "02-model.md").read_text(encoding="utf-8").splitlines() if "eq:fc" in ln)
        step("в исходнике \\frac{a}{bc}, без \\dfrac", "\\frac{a}{bc}" in line and "dfrac" not in line, line)
        import pymupdf as fitz
        sizes = set()
        with fitz.open(root / "out" / "report.pdf") as d:
            for pg in d:
                for b in pg.get_text("dict")["blocks"]:
                    for ln in b.get("lines", []):
                        sizes |= {round(sp["size"], 1) for sp in ln["spans"] if "Math" in sp["font"]}
        step("в PDF индексы формул 8,3 pt (58 % от 14,3)", 8.3 in sizes and 10.6 not in sizes, sizes)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
