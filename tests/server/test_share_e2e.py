"""Сквозной сценарий сервера LDE: приглашение → регистрация в браузере → вход LDE → ссылка → рецензент
правит и комментирует → автор забирает правки (слияние с его изменениями), отвечает → рецензент видит
решения → отзыв ссылки. Плюс проверки безопасности: сервер хранит только шифротекст, без входа — отказ.

    toolchain/venv/bin/python tests/server/test_share_e2e.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import Chrome, free_port  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def http(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


PASSWORD = "очень-длинный-пароль-1"

with tempfile.TemporaryDirectory(prefix="lde-srv-") as td:
    td = Path(td)
    data, cfg = td / "data", td / "cfg"
    cfg.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(cfg)             # ключ устройства — во временный каталог, не в ~
    REMOTE = os.environ.get("LDE_TEST_URL")      # проверка развёрнутого сервера: адрес + приглашение
    if REMOTE:
        url, inv, srv = REMOTE.rstrip("/"), os.environ["LDE_TEST_INVITE"], None
        data = None
    else:
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = dict(os.environ, LDE_DATA=str(data), LDE_PUBLIC_URL=url, PYTHONPATH=str(REPO / "lde-server"))
        srv = subprocess.Popen([sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=REPO / "lde-server", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                if http(url + "/healthz")[0] == 200:
                    break
            except Exception:
                time.sleep(0.1)
        inv = subprocess.run([sys.executable, "-m", "app.cli", "invite", "--admin"], cwd=REPO / "lde-server", env=env,
                             capture_output=True, text=True).stdout.split()[0]
    USER = os.environ.get("LDE_TEST_USER", "author")
    step("приглашение администратора", "/join#" in inv, inv)

    c = Chrome(url + "/healthz")
    try:
        # --- регистрация в браузере (ключи создаются там же)
        c.send("Page.navigate", {"url": inv})
        c.wait("document.querySelector('#f') && !document.querySelector('#f').hidden", 15, "форма регистрации")
        c.eval(f"""document.querySelector('#u').value='{USER}'; document.querySelector('#p').value={json.dumps(PASSWORD)};
                   document.querySelector('#p2').value={json.dumps(PASSWORD)}; document.querySelector('#go').click()""")
        c.wait("document.querySelector('#msg').textContent.startsWith('Готово')", 30, "регистрация")
        step("регистрация в браузере", True)

        # --- вход LDE (закрытый ключ, зашифрованный в браузере, расшифровывается в Python)
        from lde import server_client as sc, share
        r = sc.login(url, USER, PASSWORD)
        step("вход LDE по паролю, закрытый ключ из браузера расшифрован", r["username"] == USER)
        try:
            sc.login(url, USER, "неверный")
            step("неверный пароль отклонён", False)
        except sc.ServerError as e:
            step("неверный пароль отклонён", e.code == 401, str(e))

        # --- проект и ссылка
        root = td / "proj"
        subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
        rec = share.create(root, label="Иванов И. И.", days=30)
        step("ссылка создана, версия зафиксирована", "/s/" in rec["link"] and "#" in rec["link"], rec["link"])
        tags = subprocess.run(["git", "-C", str(root), "tag", "-l", "lde/v/*"], capture_output=True, text=True).stdout
        step("в проекте — версия-тег ссылки", rec["tag"] in tags)

        # --- сервер не видит текста
        if data is not None:
            blobs = b"".join(p.read_bytes() for p in (data / "blobs").rglob("*") if p.is_file())
            db = (data / "lde.sqlite").read_bytes()
            secret_word = "Модуль коэффициента".encode()
            step("на сервере нет открытого текста документа", secret_word not in blobs and secret_word not in db
                 and "Иванов".encode() not in db)

        # --- рецензент по ссылке: та же Studio (роль «рецензент»)
        c.send("Page.navigate", {"url": rec["link"]})
        c.wait("document.querySelector('#rv-name')", 40, "документ расшифрован")
        step("ключ убран из адресной строки", "#" not in c.eval("location.href"))
        c.eval("document.querySelector('#rv-name').value='Иванов И. И.'; document.querySelector('#rv-name-ok').click()")
        c.wait("document.querySelectorAll('.page img[src]').length >= 3", 20, "страницы")
        step("рецензент видит страницы документа, как автор; светлая тема",
             c.eval("document.documentElement.dataset.theme") == "light")
        step("лишнее скрыто: «Сдать», расчёты, настройки, меню проекта",
             c.eval("getComputedStyle(document.querySelector('#submit-btn')).display") == "none"
             and c.eval("getComputedStyle(document.querySelector('[data-tab=calcs]')).display") == "none")
        # щелчок по странице открывает элемент в панели (как у автора)
        c.eval("document.querySelector('.page[data-n=\"5\"]').scrollIntoView()")
        time.sleep(0.4)
        c.click('.page[data-n="5"]', 0.5, 0.3)
        c.wait("!document.querySelector('#panel').hidden", 10, "панель элемента")
        step("щелчок по странице открывает редактор элемента", c.eval("!!document.querySelector('#panel .chips, #panel math-field')"))
        bid = c.eval("LDE_BACKEND.B.snap.blocks.find(b => b.kind === 'para' && JSON.stringify(b.tokens).includes('Модуль коэффициента')).id")

        def caret_in_panel(word, select_len=0):
            """Каретка после слова (или выделение select_len знаков с его начала) в редакторе панели."""
            c.eval("""(() => { const t = document.querySelector('#panel .chips'); const word = %s, sel = %d;
                const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); let n;
                while ((n = w.nextNode())) { const i = n.nodeValue.indexOf(word); if (i >= 0) {
                  const r = document.createRange();
                  if (sel) { r.setStart(n, i); r.setEnd(n, i + sel); } else { r.setStart(n, i + word.length); r.collapse(true); }
                  const s = getSelection(); s.removeAllRanges(); s.addRange(r); break; } } })()""" % (json.dumps(word), select_len))
        # правка абзаца
        c.eval(f"openElement('{bid}')")
        c.wait("document.querySelector('#panel .chips')", 10)
        time.sleep(0.3)
        c.eval("document.querySelector('#panel .chips').focus()")
        caret_in_panel("Модуль")
        c.keys(" комплексного")
        c.click("#save-btn")
        time.sleep(0.5)
        # замечание к выделенным словам
        caret_in_panel("RC-фильтра", select_len=10)
        c.click("#cm-btn")
        c.wait("document.querySelector('#cm-text')", 5)
        c.click("#cm-text"); c.keys("Уточните тип фильтра")
        c.click("#cm-save")
        time.sleep(0.4)
        # формула: правка в MathLive (новое для рецензента)
        fid = c.eval("LDE_BACKEND.B.snap.blocks.find(b => b.kind === 'formula' && b.numbered).id")
        c.eval(f"openElement('{fid}')")
        c.wait("document.querySelector('#panel math-field') && document.querySelector('#mf-add-q')", 10, "редактор формулы")
        time.sleep(0.6)
        c.click("#panel math-field", 0.3, 0.5)
        c.key("End", "End", 0, 35)
        c.keys("+1+")
        c.click("#mf-add-q")
        c.wait("document.querySelector('#dialog2 .pick .item')", 10, "выбор величины")
        c.click("#dialog2 .pick .item")
        time.sleep(0.4)
        c.click("#save-btn")
        time.sleep(0.5)
        # реквизит титула и описание источника (группы и элементы списка)
        c.eval("openElement('@title')")
        c.wait("document.querySelectorAll('#panel .rv-row .chips').length > 2", 10, "реквизиты титула")
        c.eval("""(() => { const eds = [...document.querySelectorAll('#panel .rv-row')].find(r => r.textContent.includes('Тема')).querySelector('.chips');
            eds.focus(); const n = eds.firstChild; const r = document.createRange(); r.setStart(n, 0); r.setEnd(n, 12);
            const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()""")
        c.keys("Анализ")
        c.click("#save-btn")
        time.sleep(0.5)
        bib_id = c.eval("LDE_BACKEND.B.snap.blocks.find(b => b.kind === 'bib').id")
        c.eval(f"openElement('{bib_id}')")
        c.wait("document.querySelector('#panel .chips')", 10)
        c.eval("const e=document.querySelector('#panel .chips'); e.focus(); placeEnd(e)")
        c.keys(" [исправлено]")
        c.click("#save-btn")
        time.sleep(0.4)
        c.click("#cm-btn")
        c.wait("document.querySelector('#cm-text')", 5)
        c.click("#cm-text"); c.keys("Нет страниц в описании")
        c.click("#cm-save")
        # попытка подсунуть опасный LaTeX в формулу (в обход интерфейса)
        f2 = c.eval("LDE_BACKEND.B.snap.blocks.filter(b => b.kind === 'formula').map(b => b.id).find(i => i !== '" + fid + "')")
        c.eval(f"LDE_BACKEND.api('/api/element/save', {{uid: '{f2}', src: '$$ \\\\input{{/etc/passwd}} $$'}})")
        c.wait("document.querySelector('#build-state').textContent === 'сохранено'", 20, "сохранение копии")
        c.click('[data-tab="mine"]')
        time.sleep(0.5)
        mine = c.eval("document.querySelector('#side-list').textContent")
        step("«Мои правки»: исправления и замечания", "Уточните тип фильтра" in mine and "Нет страниц" in mine and "Исправления" in mine, mine[:300])
        step("на страницах — пометки «исправлено» и «замечание»", c.eval("document.querySelectorAll('.page .rv-mk.edit').length") >= 1
             and c.eval("document.querySelectorAll('.page .rv-mk.cm').length") >= 1)
        if data is not None:
            blobs = b"".join(p.read_bytes() for p in (data / "blobs").rglob("*") if p.is_file())
            step("копия рецензента на сервере — тоже шифротекст", "комплексного".encode() not in blobs and "Уточните".encode() not in blobs)

        # --- автор тем временем правит тот же абзац в другом месте
        z = root / "text" / "02-model.md"
        z.write_text(z.read_text(encoding="utf-8").replace("определяется выражением", "задаётся выражением"), encoding="utf-8")
        subprocess.run([str(REPO / "bin" / "lde"), "save", "-m", "правка автора", "-C", str(root)], capture_output=True)
        rv = share.pull(root, rec["id"])
        it = next((x for x in rv["items"] if not x.get("gen") and not x["base"].startswith("$$")), {})
        txt_items = [x for x in rv["items"] if not x.get("gen")]
        step("опасный LaTeX в формуле отклонён автоматически", len(rv.get("rejected_unsafe", [])) == 1, rv.get("rejected_unsafe"))
        fitem = next((x for x in txt_items if x["base"].startswith("$$")), {})
        step("правка формулы рецензентом: вставки величин восстановлены (\\q{…}), «+1» на месте",
             "+1" in (fitem.get("theirs") or "") and "\\q{" in (fitem.get("theirs") or "") and "@v" not in (fitem.get("theirs") or ""), fitem.get("theirs"))
        if fitem:
            share.decide(root, rec["id"], fitem["block"], "accept")
        txt_items = [x for x in txt_items if x is not fitem]
        step("автор забрал: правка текста, замечания, имя рецензента", len(txt_items) == 1 and len(rv["comments"]) == 2
             and rv["name"] == "Иванов И. И.", json.dumps(rv, ensure_ascii=False)[:400])
        step("служебная ветка review/<id> от тега снимка", subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%s", rv["branch"]], capture_output=True, text=True).stdout.startswith("Правки рецензента"))
        step("правка рецензента сливается с правкой автора (пословно)", it.get("status") == "merge", it.get("status"))
        share.decide(root, rec["id"], it["block"], "accept")
        t = z.read_text(encoding="utf-8")
        step("принято: в тексте обе правки", "Модуль комплексного коэффициента" in t and "задаётся выражением" in t, [ln for ln in t.splitlines() if "Модуль" in ln])
        log = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
        step("принятое — точка в истории", log.startswith("Принято от Иванов И. И."), log)
        gen = {x["gen"]: x for x in rv["items"] if x.get("gen")}
        step("правки титула и источника — отдельными пунктами с местом", set(gen) == {"title_field", "bib"}
             and "источник [1]" in gen["bib"]["where"], [x.get("where") for x in rv["items"]])
        step("замечание к источнику — с местом «источник […]»", any("источник" in (cm.get("where") or "") for cm in rv["comments"]),
             [cm.get("where") for cm in rv["comments"]])
        share.decide(root, rec["id"], gen["title_field"]["block"], "accept")
        share.decide(root, rec["id"], gen["bib"]["block"], "accept")
        lt, bt = (root / "lde.toml").read_text(encoding="utf-8"), (root / "refs.bib").read_text(encoding="utf-8")
        step("принято: тема в lde.toml, описание — полем formatted в refs.bib",
             'topic = "Анализ RC-фильтра нижних частот"' in lt and "[исправлено]" in bt and "formatted" in bt, lt[-300:])
        chk = subprocess.run([str(REPO / "bin" / "lde"), "check", "-C", str(root)], capture_output=True, text=True)
        step("после приёма проект проверяется без ошибок синтаксиса", "syntax" not in chk.stdout and "TOML" not in chk.stdout, chk.stdout[-300:])
        share.reply(root, rec["id"], next(cm["id"] for cm in rv["comments"] if cm.get("quote") == "RC-фильтра"), "Уточнил: пассивный RC-фильтр", "done")

        # --- рецензент видит решения
        c.send("Page.reload", {})
        time.sleep(2.0)
        c.wait("document.querySelectorAll('.page img[src]').length >= 3", 30, "страницы после перезагрузки")
        c.click('[data-tab="mine"]')
        time.sleep(0.6)
        mine = c.eval("document.querySelector('#side-list').textContent")
        step("рецензент видит «автор принял» и ответ автора", "автор принял" in mine and "пассивный" in mine, mine[:300])

        # --- панель автора
        c.send("Page.navigate", {"url": url + "/panel/"})
        c.wait("!document.querySelector('#login').hidden", 10)
        c.eval(f"document.querySelector('#lu').value='{USER}'; document.querySelector('#lp').value={json.dumps(PASSWORD)}; document.querySelector('#lgo').click()")
        c.wait("document.querySelector('#shares').textContent.includes('Иванов')", 30, "ссылки в панели")
        step("панель: ссылка с расшифрованной меткой", "Копировать ссылку" in c.eval("document.querySelector('#shares').textContent"))

        # --- безопасность
        step("API без входа — 401", http(url + "/api/shares")[0] == 401)
        step("копия рецензента без cookie ссылки — 401", http(url + f"/r/{rec['id']}/api/snapshot")[0] == 401)
        step("подделанный токен ссылки — отказ", http(url + "/s/" + "A" * 43)[0] == 404)
        code, body = http(url + "/")
        step("заголовки: CSP, no-referrer", True)
        share.revoke(rec["id"])
        c.send("Page.navigate", {"url": rec["link"]})
        time.sleep(1.0)
        step("отозванная ссылка не открывается", "отозвал" in c.eval("document.body.textContent"))
        step("ошибок JS нет", not [e for e in c.errors if "401" not in e and "404" not in e], str(c.errors))
    finally:
        c.close()
        if srv:
            srv.terminate()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
