"""Всплывающие окна: выделение текста в поле, законченное мышью за краем окна, окно не закрывает;
обычный щелчок по фону — закрывает.

    toolchain/venv/bin/python tests/ui/test_dialog_drag.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tests" / "ui"), str(REPO)]
from cdp import Chrome, StudioServer  # noqa: E402
from lde.project import Project  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def drag(c, x0, y0, x1, y1, n=8):
    """ЛКМ нажата в (x0, y0), протащена и отпущена в (x1, y1) — как рукой."""
    c.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x0, "y": y0})
    c.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x0, "y": y0, "button": "left", "clickCount": 1})
    for i in range(1, n + 1):
        c.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x0 + (x1 - x0) * i / n, "y": y0 + (y1 - y0) * i / n,
                                            "button": "left", "buttons": 1})
    c.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x1, "y": y1, "button": "left", "clickCount": 1})


def drag_out_of(c, dlg, inp):
    """Из правой части поля влево за край окна (выделение до начала строки)."""
    x, y, w, h = c.rect(inp)
    dx, dy, dw, dh = c.rect(dlg)
    drag(c, x + w * 0.8, y + h / 2, max(5, dx - 40), y + h / 2)


with tempfile.TemporaryDirectory(prefix="lde-ui-drag-") as td:
    root = Path(td) / "r"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    blk = next(b for b in Project(root).blocks if b.kind == "para" and "@fig:afc" in b.src)
    st = StudioServer(str(root), str(Path(td) / "cfg"))
    c = None
    try:
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        # окно первого уровня: «Настройки документа», поле титула
        c.click("#settings-btn")
        c.wait("document.querySelector('#dialog input.inp:not([type])')", 10, "окно настроек")
        c.eval("document.querySelector('#dialog input.inp:not([type])').id = 't-inp'")
        c.eval("document.querySelector('#t-inp').value = 'Организация, которую надо стереть'")
        drag_out_of(c, "#dialog", "#t-inp")
        step("выделение с выходом за край окна — окно настроек не закрылось", c.eval("!document.querySelector('#dialog-wrap').hidden"))
        sel = c.eval("(() => { const i = document.querySelector('#t-inp'); return [i.selectionStart, i.selectionEnd]; })()")
        step("текст в поле выделен — можно удалить", sel and sel[1] > sel[0], sel)
        c.click_xy(8, 300)
        step("обычный щелчок по фону — окно закрылось", c.eval("document.querySelector('#dialog-wrap').hidden"))

        # окно второго уровня: «Вставить» поверх редактора абзаца
        c.eval(f"openElement({json.dumps(blk.uid)})")
        c.wait("document.querySelector('#panel .toolbar .btn')", 10, "редактор абзаца")
        c.eval("[...document.querySelectorAll('#panel .toolbar .btn')].find(b => b.textContent === '+ Величина').click()")
        c.wait("document.querySelector('#dialog2 input.inp')", 10, "окно «Вставить»")
        c.eval("const i = document.querySelector('#dialog2 input.inp'); i.id = 'p-inp'; i.value = 'частота среза'")
        drag_out_of(c, "#dialog2", "#p-inp")
        step("окно «Вставить»: выделение за край — не закрылось", c.eval("!document.querySelector('#dialog-wrap2').hidden"))
        c.click_xy(8, 300)
        step("окно «Вставить»: щелчок по фону — закрылось, редактор на месте",
             c.eval("document.querySelector('#dialog-wrap2').hidden && !!document.querySelector('#panel .chips')"))
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        if c:
            c.close()
        st.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
