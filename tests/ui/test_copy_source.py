"""Окно правки блока → «Копировать исходник»: исходный текст LDE поля со вставками, как запишет «Сохранить».

    toolchain/venv/bin/python tests/ui/test_copy_source.py
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "ui"))
sys.path.insert(0, str(REPO))
from cdp import Chrome, StudioServer  # noqa: E402
from lde.project import Project  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


PARA = "Рассчитанная по формуле @eq:k"

with tempfile.TemporaryDirectory(prefix="lde-ui-copy-") as td:
    root = Path(td) / "r"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    f = root / "text" / "02-model.md"
    blk = next(b for b in Project(root).blocks if b.kind == "para" and b.src.startswith(PARA))
    st = StudioServer(str(root), str(Path(td) / "cfg"))
    c = None
    try:
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        # буфер обмена headless-Chrome недоступен — перехватываем запись в него
        c.eval("window.__copied = null; navigator.clipboard.writeText = async (s) => { window.__copied = s; }")
        c.eval(f"openElement({json.dumps(blk.uid)})")
        c.wait("document.querySelector('#panel .chips .chip') && document.querySelector('#panel .copy-src')", 10, "редактор абзаца")
        n_chips = c.eval("document.querySelectorAll('#panel .chips .chip').length")
        c.click("#panel .copy-src")
        c.wait("window.__copied !== null", 5, "копирование")
        got = c.eval("window.__copied")
        step(f"без правок — ровно исходник абзаца из файла ({n_chips} вставок)", got == blk.src.strip(), (got, blk.src))
        step("в окне — уведомление", c.eval("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('скопирован'))"))
        # правка: текст со служебными знаками LDE — экранируется так же, как при сохранении
        c.eval("window.__copied = null")
        c.click("#panel .chips", 0.99, 0.9)
        c.eval("placeEnd(document.querySelector('#panel .chips'))")
        c.keys(" Цена {x} $5, *итог* и @fig:afc.")
        time.sleep(0.3)
        c.click("#panel .copy-src")
        c.wait("window.__copied !== null", 5, "копирование")
        got = c.eval("window.__copied")
        step("после правки — новые знаки экранированы", got.endswith(r" Цена \{x\} \$5, \*итог\* и \@fig:afc.") and got.startswith(PARA), got[-80:])
        step("выделение и каретка в поле не сбиты кнопкой", c.eval("document.activeElement === document.querySelector('#panel .chips')"))
        c.click("#save-btn")
        t0 = time.time()
        while "Цена" not in f.read_text(encoding="utf-8") and time.time() - t0 < 20:
            time.sleep(0.3)
        saved = next(b for b in Project(root).blocks if b.kind == "para" and b.src.startswith(PARA)).src.strip()
        step("скопированное = то, что записало «Сохранить»", got == saved, (got, saved))
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        if c:
            c.close()
        st.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
