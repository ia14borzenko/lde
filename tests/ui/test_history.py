"""Studio: история и версии — сохранить версию, отменить/повторить, сравнить, вернуть элемент и всё.

    toolchain/venv/bin/python tests/ui/test_history.py
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


def text(root):
    return (root / "text" / "02-model.md").read_text(encoding="utf-8")


with tempfile.TemporaryDirectory(prefix="lde-ui-hist-") as td:
    root = Path(td) / "r"
    subprocess.run([LDE, "init", str(root)], capture_output=True, check=True)
    srv = StudioServer(str(root))
    c = Chrome(srv.url)
    built = "document.querySelector('#build-state').innerText.includes('обновлён')"
    try:
        c.wait(built, 120, "сборка")
        # сохранить версию через диалог
        c.click("#history-btn")
        c.wait("[...document.querySelectorAll('#dialog .btn')].some(b => b.textContent.startsWith('Сохранить версию'))", 10)
        c.eval("[...document.querySelectorAll('#dialog .btn')].find(b => b.textContent.startsWith('Сохранить версию')).click()")
        c.wait("document.querySelector('#ver-save')", 5)
        c.click("#dialog input.inp")
        c.keys("Отправлено руководителю")
        c.click("#ver-save")
        time.sleep(1.0)
        vs = subprocess.run([LDE, "versions", "-C", str(root)], capture_output=True, text=True).stdout
        step("версия сохранена", "Отправлено руководителю" in vs, vs)
        # правка абзаца в Studio
        uid = "text/02-model.md#2"
        c.eval(f"openElement('{uid}')")
        c.wait("document.querySelector('#panel .chips')", 10)
        c.eval("const e=document.querySelector('#panel .chips'); e.focus(); placeEnd(e)")
        c.keys(" Проверка истории.")
        c.click("#save-btn")
        c.wait("!S.dirty", 10)
        time.sleep(1.0)
        step("правка записана", "Проверка истории." in text(root))
        # отменить и повторить
        c.eval("closePanel()")
        c.click("#undo-btn")
        time.sleep(1.5)
        step("«Отменить» убрал правку", "Проверка истории." not in text(root))
        c.key("z", "KeyZ", 2 | 1 | 8, 90)      # Ctrl+Alt+Shift+Z — повторить
        time.sleep(1.5)
        step("повтор вернул правку", "Проверка истории." in text(root))
        # сравнить с версией и вернуть элемент
        c.click("#history-btn")
        c.wait("document.querySelector('#dialog .ver')", 10, "список версий")
        step("в окне нет текста «null» (пустые кнопки пропускаются)", not c.eval("/(^|\\s)null(\\s|$)/.test(document.querySelector('#dialog').innerText)"),
             c.eval("document.querySelector('#dialog .dlg-foot').innerText"))
        c.eval("[...document.querySelectorAll('#dialog .ver .btn')].find(b => b.textContent.startsWith('Сравнить')).click()")
        c.wait("document.querySelector('#dialog .chg')", 10, "сравнение")
        ins = c.eval("[...document.querySelectorAll('#dialog .diff.words ins')].map(e => e.textContent).join('|')")
        step("пословная разница: вставка выделена", "Проверка истории." in ins, ins)
        c.eval("[...document.querySelectorAll('#dialog .chg .btn')].find(b => b.textContent.startsWith('Вернуть')).click()")
        time.sleep(1.5)
        step("элемент возвращён к версии", "Проверка истории." not in text(root))
        # вернуть всё к версии
        c.eval("document.querySelector('#dialog-wrap').hidden = true")
        subprocess.run(["sed", "-i", "s/Частота среза зависит/Частота среза ЗАВИСИТ/", str(root / "text" / "02-model.md")])
        time.sleep(1.5)
        c.click("#history-btn")
        c.wait("document.querySelector('#dialog .ver')", 10)
        c.eval("window.confirm = () => true; [...document.querySelectorAll('#dialog .ver .btn')].find(b => b.textContent.startsWith('Вернуть')).click()")
        time.sleep(2.0)
        step("весь проект возвращён к версии", "ЗАВИСИТ" not in text(root) and "Частота среза зависит" in text(root))
        log = subprocess.run(["git", "-C", str(root), "log", "-3", "--format=%s"], capture_output=True, text=True).stdout
        step("откат — новой точкой, внешняя правка сохранена в истории",
             log.startswith("Возвращено к версии «Отправлено руководителю»") and "Изменения вне Studio" in log, log)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
