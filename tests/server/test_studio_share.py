"""Studio ↔ сервер LDE настоящими кликами: подключение, «Поделиться», рецензент правит, «Забрать правки» → «Принять».

    toolchain/venv/bin/python tests/server/test_studio_share.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import Chrome, StudioServer, free_port  # noqa: E402

ok = True
PASSWORD = "длинный-пароль-студии-2"


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def menu_item(c, text):
    c.click("#brand")
    c.wait("document.querySelector('#proj-menu')", 5, "меню")
    c.eval(f"[...document.querySelectorAll('#proj-menu .mi')].find(e => e.textContent.startsWith({json.dumps(text)})).click()")


with tempfile.TemporaryDirectory(prefix="lde-srv-studio-") as td:
    td = Path(td)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, LDE_DATA=str(td / "data"), LDE_PUBLIC_URL=url)
    srv = subprocess.Popen([sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=REPO / "lde-server", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=2)
            break
        except Exception:
            time.sleep(0.1)
    inv = subprocess.run([sys.executable, "-m", "app.cli", "invite"], cwd=REPO / "lde-server", env=env,
                         capture_output=True, text=True).stdout.split()[0]
    root = td / "proj"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    cfg = td / "cfg"
    cfg.mkdir()
    st = StudioServer(str(root), str(cfg))
    rv = Chrome(inv)
    c = Chrome(st.url)
    try:
        # регистрация рецензентским браузером (страница join)
        rv.wait("document.querySelector('#f') && !document.querySelector('#f').hidden", 15)
        rv.eval(f"""document.querySelector('#u').value='author'; document.querySelector('#p').value={json.dumps(PASSWORD)};
                    document.querySelector('#p2').value={json.dumps(PASSWORD)}; document.querySelector('#go').click()""")
        rv.wait("document.querySelector('#msg').textContent.startsWith('Готово')", 30, "регистрация")
        # Studio: подключить сервер
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        menu_item(c, "Сервер LDE")
        c.wait("document.querySelector('#srv-login')", 5)
        c.click("#srv-url"); c.keys(url)
        c.click("#srv-user"); c.keys("author")
        c.click("#srv-pw"); c.keys(PASSWORD)
        c.click("#srv-login")
        c.wait("document.querySelector('#dialog-wrap').hidden", 30, "вход")
        step("Studio: сервер подключён", json.loads((cfg / "lde" / "servers.json").read_text())["default"] == url)
        # «Поделиться»
        menu_item(c, "Поделиться документом")
        c.wait("document.querySelector('#sh-go')", 5)
        c.click("#sh-label"); c.keys("Петров П. П.")
        c.click("#sh-go")
        c.wait("document.querySelector('#sh-link')", 60, "ссылка")
        link = c.eval("document.querySelector('#sh-link').value")
        step("Studio: ссылка создана", link.startswith(url + "/s/") and "#" in link, link)
        c.eval("document.querySelector('#dialog-wrap').hidden = true")
        # рецензент
        rv.send("Page.navigate", {"url": link})
        rv.wait("document.querySelector('#rv-name')", 30, "документ у рецензента")
        rv.eval("document.querySelector('#rv-name').value='Петров П. П.'; document.querySelector('#rv-name-ok').click()")
        rv.wait("document.querySelectorAll('.page img[src]').length >= 3", 20, "страницы у рецензента")
        bid = rv.eval("LDE_BACKEND.B.snap.blocks.find(b => b.kind === 'para' && JSON.stringify(b.tokens).includes('Частота среза зависит')).id")
        rv.eval(f"openElement('{bid}')")
        rv.wait("document.querySelector('#panel .chips')", 10)
        time.sleep(0.3)
        rv.eval("""(() => { const t = document.querySelector('#panel .chips'); t.focus();
            const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); let n;
            while ((n = w.nextNode())) { const i = n.nodeValue.indexOf('зависит'); if (i >= 0) {
              const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 7);
              const s = getSelection(); s.removeAllRanges(); s.addRange(r); break; } } })()""")
        rv.keys("определяется")
        rv.click("#save-btn")
        rv.wait("document.querySelector('#build-state').textContent === 'сохранено'", 15, "сохранение")
        step("рецензент (Studio): правка сохранена", rv.eval("Object.keys(LDE_BACKEND.B.copy.edits).length") == 1)
        # Studio: забрать и принять
        menu_item(c, "Ссылки и правки")
        c.wait("document.querySelector('[data-share]')", 30, "список ссылок")
        step("Studio: в списке ссылка «Петров»", "Петров" in c.eval("document.querySelector('#dialog').textContent"))
        c.click("[data-share]")
        c.wait("document.querySelector('#rv-accept')", 60, "режим разбора правок")
        ins = c.eval("[...document.querySelectorAll('.rv-card .diff ins')].map(e => e.textContent).join('|')")
        step("Studio: правка показана отрисованной, пословно", "определяется" in ins, ins)
        c.wait("document.querySelectorAll('#pages-r .page img').length >= 3", 120, "версия рецензента собрана")
        step("справа — страницы «у рецензента»", True)
        c.click("#rv-accept")
        time.sleep(2.5)
        t = (root / "text" / "02-model.md").read_text(encoding="utf-8")
        step("принято в текст проекта", "Частота среза определяется от сопротивления" in t, [l for l in t.splitlines() if "Частота среза" in l])
        c.wait("!document.querySelector('#rv-finish').disabled", 10, "все решены")
        c.click("#rv-finish")
        time.sleep(2.0)
        vs = subprocess.run([str(REPO / "bin" / "lde"), "versions", "-C", str(root)], capture_output=True, text=True).stdout
        step("«Завершить» зафиксировал версию «Правки: … — учтены»", "учтены" in vs, vs)
        step("ошибок JS нет (Studio, рецензент)", not c.errors and not rv.errors, str(c.errors + rv.errors))
    finally:
        c.close()
        rv.close()
        st.close()
        srv.terminate()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
