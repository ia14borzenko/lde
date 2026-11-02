"""Studio: «Ручное оформление» элемента — флажок, параметры, PDF и исходник; выделение слов Ж/К.

    toolchain/venv/bin/python tests/ui/test_manual_style.py
"""
import os
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


def src(root):
    return (root / "text" / "02-model.md").read_text(encoding="utf-8")


def pdf_font_sizes(pdf, needle):
    import pymupdf as fitz
    sizes = set()
    with fitz.open(pdf) as d:
        for pg in d:
            for b in pg.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    t = "".join(s["text"] for s in ln["spans"])
                    if needle in t:
                        sizes |= {round(s["size"]) for s in ln["spans"] if s["text"].strip()}
    return sizes


with tempfile.TemporaryDirectory(prefix="lde-ui-style-") as td:
    root = Path(td) / "r"
    subprocess.run([LDE, "init", str(root)], capture_output=True, check=True)
    srv = StudioServer(str(root))
    c = Chrome(srv.url)
    built = "document.querySelector('#build-state').innerText.includes('обновлён')"
    try:
        c.wait(built, 120, "сборка")
        uid = "text/02-model.md#2"     # абзац «Модуль коэффициента передачи…»
        c.eval(f"openElement('{uid}')")
        c.wait("document.querySelector('#style-on')", 10, "раздел оформления")
        step("по умолчанию выключено, поля скрыты",
             c.eval("!document.querySelector('#style-on').checked && document.querySelector('.style-fields').hidden"))
        c.click("#style-on")
        c.wait("!document.querySelector('.style-fields').hidden", 5, "поля видны")
        time.sleep(1.0)
        step("включение → строка {style} над абзацем", "\n{style}\nМодуль коэффициента" in src(root), src(root)[:300])
        # выравнивание по центру и кегль 10
        c.eval("const s=document.querySelector('.style-fields select[data-key=align]'); s.value='center'; s.dispatchEvent(new Event('change'))")
        c.click(".style-fields input[data-key=size]")
        c.keys("10")
        c.wait("document.querySelector('#style-box .muted').textContent.includes('применено')", 10, "применено")
        time.sleep(1.5)
        c.wait(built, 60, "пересборка")
        line = next(l for l in src(root).splitlines() if l.startswith("{style"))
        if os.environ.get("LDE_SCREENS"):       # снимок для документации
            c.eval("S.hl=null; document.querySelector('.page[data-n=\"5\"]').scrollIntoView()")
            time.sleep(1.5)
            c.screenshot(str(Path(os.environ["LDE_SCREENS"]) / "light-10-manual-style.png"))
        step("в исходнике align=center size=10", line == "{style align=center size=10}", line)
        time.sleep(1.0)
        sizes = pdf_font_sizes(root / "out" / "report.pdf", "Модуль коэффициента")
        step("в PDF абзац кеглем 10", sizes == {10}, sizes)
        # правка текста сохраняет оформление
        c.eval("const e=document.querySelector('#panel .chips'); e.focus(); placeEnd(e)")
        c.keys(" Добавлено.")
        c.click("#save-btn")
        c.wait("!S.dirty", 10, "сохранено")
        time.sleep(1.0)
        s = src(root)
        step("после правки текста строка оформления на месте",
             "{style align=center size=10}\nМодуль коэффициента" in s and "Добавлено." in s, s[:400])
        # Ж: выделить слово «Модуль» и сделать полужирным
        c.eval(f"openElement('{uid}')")
        c.wait("document.querySelector('#panel .chips')", 10)
        time.sleep(0.5)
        c.eval("""(() => { const e=document.querySelector('#panel .chips'); const t=e.firstChild;
                  const r=document.createRange(); r.setStart(t, 0); r.setEnd(t, 6);
                  const s=getSelection(); s.removeAllRanges(); s.addRange(r); })()""")
        c.key("b", "KeyB", 2, 66)        # Ctrl+B
        time.sleep(0.2)
        step("Ctrl+B делает чип «жирный»", c.eval("!!document.querySelector('#panel .chips .chip.b')"))
        c.click("#save-btn")
        c.wait("!S.dirty", 10, "сохранено")
        time.sleep(1.0)
        step("в исходнике **Модуль**", "**Модуль** коэффициента" in src(root), src(root)[:400])
        # выключить → оформление снова автоматическое
        c.eval(f"openElement('{uid}')")
        c.wait("document.querySelector('#style-on') && document.querySelector('#style-on').checked", 10, "флажок включён")
        c.click("#style-on")
        time.sleep(1.5)
        step("выключение убирает строку {style}", "{style" not in src(root), src(root)[:300])
        chk = subprocess.run([LDE, "check", "-C", str(root)], capture_output=True, text=True).stdout
        step("lde check: ошибок нет", "ошибок 0" in chk or "Замечаний нет" in chk, chk[-300:])
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
