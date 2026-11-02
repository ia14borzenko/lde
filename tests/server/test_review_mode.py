"""Режим разбора правок рецензента в Studio (как сравнение в VS Code), настоящими кликами.

    toolchain/venv/bin/python tests/server/test_review_mode.py

Рецензент (Studio в роли рецензента) правит три места и оставляет замечание; автор после выдачи ссылки
правит одно из них же. В Studio автора: две колонки страниц, навигация F7, «Принять», «Отклонить»,
«Совместить…» (конфликт), обычная правка во время разбора, ответ на замечание, «Завершить» → версия.
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
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import Chrome, StudioServer, free_port  # noqa: E402
from lde import crypto  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


with tempfile.TemporaryDirectory(prefix="lde-rvmode-") as td:
    td = Path(td)
    cfg = td / "cfg"
    cfg.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
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
                         capture_output=True, text=True).stdout.split()[0].split("#")[1]
    salt = crypto.b64e(os.urandom(16))
    auth, enc = crypto.password_keys("пароль-автора-1", salt)
    priv, pub = crypto.gen_keypair()
    urllib.request.urlopen(urllib.request.Request(url + "/api/register", data=json.dumps({
        "invite": inv, "username": "author", "kdf_salt": salt, "auth": crypto.b64e(auth), "pubkey": crypto.b64e(pub),
        "enc_priv": crypto.b64e(crypto.seal(enc, priv, "user-priv", compress=False))}).encode(),
        headers={"Content-Type": "application/json"}))
    from lde import server_client as sc, share
    sc.login(url, "author", "пароль-автора-1")
    root = td / "proj"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    rec = share.create(root, label="Сидоров")

    rv = Chrome(rec["link"])
    st = StudioServer(str(root), str(cfg))
    c = None
    try:
        # --- рецензент: три правки и замечание (через его Studio)
        rv.wait("document.querySelector('#rv-name')", 40)
        rv.eval("document.querySelector('#rv-name').value='Сидоров С. С.'; document.querySelector('#rv-name-ok').click()")
        rv.wait("document.querySelectorAll('.page img[src]').length >= 3", 20)

        def rv_edit(needle, old, new):
            bid = rv.eval(f"LDE_BACKEND.B.snap.blocks.find(b => b.kind === 'para' && JSON.stringify(b.tokens).includes({json.dumps(needle)})).id")
            toks = rv.eval(f"LDE_BACKEND.api('/api/element?uid={bid}').then(d => JSON.stringify(d.tokens))")
            toks = json.loads(rv.send("Runtime.evaluate", {"expression": f"LDE_BACKEND.api('/api/element?uid={bid}').then(d => JSON.stringify(d.tokens))",
                                                           "awaitPromise": True, "returnByValue": True})["result"]["value"])
            for t in toks:
                if t["t"] == "text" and old in t["v"]:
                    t["v"] = t["v"].replace(old, new, 1)
                    break
            rv.send("Runtime.evaluate", {"expression": f"LDE_BACKEND.api('/api/element/save', {{uid: '{bid}', tokens: {json.dumps(toks, ensure_ascii=False)}}})",
                                         "awaitPromise": True})
            return bid
        rv_edit("Модуль коэффициента", "Модуль", "Модуль комплексного")
        rv_edit("Частота среза зависит", "зависит", "определяется")
        rv_edit("Параметры расчёта приведены", "Параметры расчёта", "Исходные данные")
        rv.send("Runtime.evaluate", {"expression": "LDE_BACKEND.api('/api/comment', {uid: null, quote: '', text: 'Добавьте вывод о полосе пропускания'})", "awaitPromise": True})
        rv.wait("document.querySelector('#build-state').textContent === 'сохранено'", 20, "копия рецензента сохранена")

        # --- автор после выдачи ссылки правит то же место, что и рецензент (будет конфликт)
        z = root / "text" / "02-model.md"
        z.write_text(z.read_text(encoding="utf-8").replace("Параметры расчёта приведены", "Значения параметров приведены"), encoding="utf-8")
        subprocess.run([str(REPO / "bin" / "lde"), "save", "-m", "правка автора", "-C", str(root)], capture_output=True)

        # --- Studio автора: режим разбора правок
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        c.click("#brand")
        c.wait("document.querySelector('#proj-menu')", 5)
        c.eval("[...document.querySelectorAll('#proj-menu .mi')].find(e => e.textContent.startsWith('Ссылки и правки')).click()")
        c.wait("document.querySelector('[data-share]')", 30, "список ссылок")
        c.click("[data-share]")
        c.wait("document.querySelector('.rv-card .diff')", 60, "режим разбора")
        step("режим разбора: 3 правки, решено 0", "Решено 0 из 3" in c.eval("document.querySelector('.rv-head').textContent"))
        c.wait("document.querySelectorAll('#pages-r .page img').length >= 3", 180, "версия рецензента")
        step("две колонки: «Сейчас у вас» и «У рецензента»", c.eval("document.body.classList.contains('reviewing')")
             and c.eval("getComputedStyle(document.querySelector('.rv-label.right')).display") != "none")
        time.sleep(1.0)
        step("подсвечено место правки слева и справа", c.eval("document.querySelectorAll('#pages .hl').length") >= 1
             and c.eval("document.querySelectorAll('#pages-r .hl').length") >= 1)
        if os.environ.get("LDE_SCREENS"):        # снимок для документации
            c.screenshot(str(Path(os.environ["LDE_SCREENS"]) / "light-11-review-mode.png"))
        diff = c.eval("document.querySelector('.rv-card .diff').innerHTML")
        step("разница отрисована (вставки — как в документе, без LaTeX-разметки)", "<ins>" in diff and "\\left" not in diff and "{=" not in diff, diff[:300])
        # навигация F7 / Shift+F7
        first = c.eval("S.rv.i")
        c.key("F7", "F7", 0, 118)
        time.sleep(0.8)
        second = c.eval("S.rv.i")
        c.key("F7", "F7", 8, 118)
        time.sleep(0.8)
        step("F7 / Shift+F7 — следующая / предыдущая правка", (first, second, c.eval("S.rv.i")) == (0, 1, 0), (first, second))
        # принять первую
        c.click("#rv-accept")
        time.sleep(2.0)
        t = z.read_text(encoding="utf-8")
        step("«Принять»: правка в тексте", "Модуль комплексного коэффициента" in t)
        # отклонить вторую
        c.wait("document.querySelector('#rv-reject')", 10)
        c.click("#rv-reject")
        time.sleep(1.5)
        t = z.read_text(encoding="utf-8")
        step("«Отклонить»: текст автора не изменился", "Частота среза зависит" in t)
        step("«Завершить» недоступна, пока не решены все", c.eval("document.querySelector('#rv-finish').disabled") is True)
        # обычная правка документа во время разбора
        c.eval("openElement('text/03-zakl.md#1')")
        c.wait("document.querySelector('#panel .chips')", 10)
        c.eval("const e=document.querySelector('#panel .chips'); e.focus(); placeEnd(e)")
        c.keys(" Правка во время разбора.")
        c.click("#save-btn")
        time.sleep(1.5)
        step("во время разбора можно править документ как обычно", "Правка во время разбора." in (root / "text" / "03-zakl.md").read_text(encoding="utf-8"))
        # третья — конфликт → «Совместить…»
        c.wait("document.querySelector('#rv-merge')", 10, "конфликт")
        step("конфликт показан: «вы тоже правили это место»", "вы тоже правили" in c.eval("document.querySelector('.rv-card').textContent"))
        c.click("#rv-merge")
        c.wait("document.querySelector('#dialog .chips')", 10, "окно «Совместить»")
        c.eval("""(() => { const t = document.querySelector('#dialog .chips'); t.focus(); t.textContent = 'Исходные значения параметров приведены в таблице '; })()""")
        c.click("#rv-own-save")
        time.sleep(2.0)
        t = z.read_text(encoding="utf-8")
        step("«Совместить»: записан свой вариант", "Исходные значения параметров приведены в таблице" in t, [l for l in t.splitlines() if "приведены" in l])
        # ответ на замечание
        c.eval("[...document.querySelectorAll('.rv-card')].find(e => e.textContent.includes('полосе пропускания')).querySelector('textarea').value = 'Добавил в заключение'")
        c.eval("[...[...document.querySelectorAll('.rv-card')].find(e => e.textContent.includes('полосе пропускания')).querySelectorAll('button')].find(b => b.textContent === 'Выполнено').click()")
        time.sleep(1.5)
        # завершить
        c.wait("!document.querySelector('#rv-finish').disabled", 10, "все правки решены")
        c.click("#rv-finish")
        time.sleep(2.5)
        vs = subprocess.run([str(REPO / "bin" / "lde"), "versions", "-C", str(root)], capture_output=True, text=True).stdout
        step("«Завершить» → версия «Правки: Сидоров С. С. — учтены», выход из режима", "учтены" in vs and not c.eval("document.body.classList.contains('reviewing')"), vs)
        # рецензент видит решения и ответ
        rv.send("Page.reload", {})
        time.sleep(2.0)
        rv.wait("document.querySelectorAll('.page img[src]').length >= 3", 30)
        rv.click('[data-tab="mine"]')
        time.sleep(0.8)
        if os.environ.get("LDE_SCREENS"):
            rv.screenshot(str(Path(os.environ["LDE_SCREENS"]) / "light-12-reviewer.png"))
        mine = rv.eval("document.querySelector('#side-list').textContent")
        step("рецензент видит «автор принял», «автор отклонил», ответ", "автор принял" in mine and "автор отклонил" in mine and "Добавил в заключение" in mine, mine[:400])
        step("ошибок JS нет", not c.errors and not rv.errors, str(c.errors + rv.errors))
    finally:
        if c:
            c.close()
        rv.close()
        st.close()
        srv.terminate()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
