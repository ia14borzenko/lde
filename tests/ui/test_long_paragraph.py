"""Длинный абзац в окне правки: текст не уходит под кнопки — поле прокручивается, кнопки на виду.

    toolchain/venv/bin/python tests/ui/test_long_paragraph.py
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


GEOM = """(() => {
  const e = document.querySelector('#panel .chips'), t = document.querySelector('#panel .toolbar');
  const b = document.querySelector('#panel-body'), f = document.querySelector('#panel-foot');
  const last = [...e.childNodes].pop(), rg = document.createRange(); rg.selectNodeContents(last);
  const lr = [...rg.getClientRects()].pop(), r = e.getBoundingClientRect();
  return {client: e.clientHeight, scroll: e.scrollHeight, top: e.scrollTop, bottom: r.bottom, tbTop: t.getBoundingClientRect().top,
          tbBottom: t.getBoundingClientRect().bottom, footTop: f.getBoundingClientRect().top,
          lastBottom: lr.bottom, bodyOverflow: b.scrollHeight - b.clientHeight};
})()"""

with tempfile.TemporaryDirectory(prefix="lde-ui-long-") as td:
    root = Path(td) / "r"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    f = root / "text" / "01-vvedenie.md"
    f.write_text(f.read_text(encoding="utf-8") + "\nДЛИННЫЙ " + " ".join(
        ["Частота среза {f_ср} определяется значениями {R} и {C}, что показано на рисунке @fig:afc."] * 25) + " КОНЕЦ.\n",
        encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "длинный абзац"], capture_output=True)
    blk = next(b for b in Project(root).blocks if b.src.startswith("ДЛИННЫЙ"))
    st = StudioServer(str(root), str(Path(td) / "cfg"))
    c = None
    try:
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        c.eval(f"openElement({json.dumps(blk.uid)})")
        c.wait("document.querySelector('#panel .copy-src')", 10, "редактор абзаца")
        g = c.eval(GEOM)
        step("текст длиннее поля — поле прокручивается", g["scroll"] > g["client"] + 100
             and c.eval("getComputedStyle(document.querySelector('#panel .chips')).overflowY") == "auto", g)
        step("поле не налезает на кнопки вставок", g["bottom"] <= g["tbTop"], g)
        step("кнопки вставок и «Сохранить» на виду", g["tbBottom"] <= g["footTop"] and g["bodyOverflow"] <= 1, g)
        c.eval("document.querySelector('#panel .chips').scrollTop = 1e6")
        g = c.eval(GEOM)
        step("прокруткой виден конец текста — внутри поля", g["top"] > 0 and g["lastBottom"] <= g["bottom"] + 1, g)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        if c:
            c.close()
        st.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
