"""Studio: тема и позиция переживают перезапуск (новый порт), меню проекта, второй документ.

    toolchain/venv/bin/python tests/ui/test_project_menu.py

Настройки интерфейса пишутся во временный XDG_CONFIG_HOME (не в ~), окна не открываются.
"""
import json
import shutil
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


def built(c, timeout=120):
    c.wait("document.querySelector('#build-state').innerText.includes('обновлён') && S.pages.length", timeout, "сборка")


with tempfile.TemporaryDirectory(prefix="lde-ui-menu-") as td:
    root = Path(td) / "r"
    cfg = Path(td) / "cfg"
    cfg.mkdir()
    subprocess.run([LDE, "init", str(root)], capture_output=True, check=True)
    r = subprocess.run([LDE, "-C", str(root), "new", "document", "pz", "--title", "Пояснительная записка"],
                       capture_output=True, text=True)
    step("lde new document", r.returncode == 0 and (root / "documents" / "pz" / "document.toml").exists(), r.stderr)
    q = next(iter(subprocess.run([LDE, "-C", str(root), "list", "quantities"], capture_output=True, text=True)
                  .stdout.split("\t")[:1]))
    (root / "documents" / "pz" / "01-текст.md").write_text(
        f"# Пояснительная записка {{-}}\n\nВеличина из общей базы: {q}.\n", encoding="utf-8")
    ls = subprocess.run([LDE, "-C", str(root), "list", "documents"], capture_output=True, text=True).stdout
    step("lde list documents — два документа", "main" in ls and "pz" in ls, ls)

    # 1-й запуск: тёмная тема, листаем на 3-ю страницу
    srv = StudioServer(str(root), str(cfg))
    c = Chrome(srv.url)
    try:
        built(c)
        c.eval("document.documentElement.dataset.theme = 'light'")
        c.click("#theme-btn")
        n = c.eval("S.pages.length")
        target = min(3, n)
        c.eval(f"document.querySelector('.page[data-n=\"{target}\"]').scrollIntoView()")
        time.sleep(1.6)   # дебаунс сохранения позиции
        prefs = json.loads((cfg / "lde" / "prefs.json").read_text(encoding="utf-8"))
        step("тема записана в ~/.config/lde/prefs.json", prefs.get("theme") == "dark", prefs)
        view = json.loads((root / ".lde" / "studio-view.json").read_text(encoding="utf-8"))
        step("позиция записана в .lde/studio-view.json", view.get("docs", {}).get("main", {}).get("page") == target, view)
    finally:
        c.close()
        srv.close()

    # 2-й запуск: другой порт (и новое хранилище браузера) — тема и страница те же
    srv = StudioServer(str(root), str(cfg))
    c = Chrome(srv.url)
    try:
        built(c)
        c.wait("S.viewRestored", 20, "восстановление позиции")
        time.sleep(0.3)
        step("тема сохранилась после перезапуска", c.eval("document.documentElement.dataset.theme") == "dark")
        cur = c.eval("currentView().page")
        step(f"открылось на странице {target}", cur == target, f"страница {cur}")

        # меню проекта: документы, переключение
        c.click("#brand")
        c.wait("document.querySelector('#proj-menu')", 5, "меню")
        items = c.eval("[...document.querySelectorAll('#proj-menu .mi .t')].map(e => e.textContent)")
        step("в меню — документы, открыть, новый, импорт, закрыть",
             all(any(x.startswith(w) for x in items) for w in ("Пояснительная записка", "+ Новый документ", "Открыть проект",
                                                                "Новый отчёт", "Импорт из Word", "Закрыть проект")), items)
        c.eval("[...document.querySelectorAll('#proj-menu .mi')].find(e => e.textContent.startsWith('Пояснительная')).click()")
        c.wait("S.state.project.doc === 'pz'", 10, "переключение документа")
        built(c)
        step("собран PDF второго документа (out/pz.pdf)", (root / "out" / "pz.pdf").exists())
        outline = c.eval("S.state.outline.map(o => o.text)")
        step("в разделах — текст второго документа", outline == ["Пояснительная записка"], outline)
        step("общая база: величины те же", c.eval("S.state.quantities.length") > 0)
        step("метка документа в заголовке", c.eval("document.querySelector('#proj-doc').textContent") == "pz")
        view = json.loads((root / ".lde" / "studio-view.json").read_text(encoding="utf-8"))
        step("запомнен текущий документ", view.get("doc") == "pz", view)

        # закрыть проект → стартовый экран
        c.click("#brand")
        c.wait("document.querySelector('#proj-menu')", 5, "меню")
        c.eval("[...document.querySelectorAll('#proj-menu .mi')].find(e => e.textContent.startsWith('Закрыть')).click()")
        c.wait("!document.querySelector('#start').hidden", 10, "стартовый экран")
        step("проект закрыт — стартовый экран", c.eval("S.state.project") is None)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        c.close()
        srv.close()
    shutil.rmtree(cfg, ignore_errors=True)

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
