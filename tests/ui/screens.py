"""Скриншоты Studio для документации (docs/screens/) — на демо-проекте из шаблона, в чистом окружении.

    toolchain/venv/bin/python tests/ui/screens.py [каталог-вывода]

Проект создаётся заново (`lde init` + несколько правок и версия), настройки Studio — во временном
каталоге: на снимках нет ни путей, ни недавних проектов, ни данных этой машины. Ключ доступа
в окне «Подключить агента» маскируется.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tests" / "ui"), str(REPO)]
from cdp import Chrome, StudioServer  # noqa: E402

LDE = str(REPO / "bin" / "lde")
# автор правок на снимках — условный, не пользователь этой машины
os.environ.update(GIT_AUTHOR_NAME="Иван Иванов", GIT_AUTHOR_EMAIL="student@example.org",
                  GIT_COMMITTER_NAME="Иван Иванов", GIT_COMMITTER_EMAIL="student@example.org")
out = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "docs" / "screens")
out.mkdir(parents=True, exist_ok=True)


def lde(*a):
    return subprocess.run([LDE, *a], capture_output=True, text=True, timeout=600)


def demo_project(base: Path) -> Path:
    root = base / "rc-filter"
    lde("init", str(root))
    f = root / "text" / "02-model.md"
    t = f.read_text(encoding="utf-8")
    f.write_text(t.replace("Частота среза зависит от сопротивления {R} и ёмкости {C}:",
                           "Частота среза определяется сопротивлением {R} и ёмкостью {C} цепи:"), encoding="utf-8")
    lde("save", "-m", "Уточнено определение частоты среза", "-C", str(root))
    lde("version", "Черновик для руководителя", "-C", str(root))
    f.write_text(f.read_text(encoding="utf-8") + "\nПри увеличении {R} вдвое частота среза уменьшается вдвое, "
                 "что видно по формуле @eq:fc.\n", encoding="utf-8")
    lde("save", "-m", "Добавлен вывод о влиянии R", "-C", str(root))
    return root


def mask_token(c):
    c.eval("""document.querySelectorAll('#dialog input, #dialog textarea, #dialog pre, #dialog code').forEach(e => {
      const f = (s) => s.replace(/token=[A-Za-z0-9_\\-]+/g, 'token=••••••••').replace(/Bearer [A-Za-z0-9_\\-]+/g, 'Bearer ••••••••');
      if ('value' in e && e.value) e.value = f(e.value); else e.textContent = f(e.textContent); })""")


def ready(c):
    c.wait("document.querySelectorAll('.page img[src]').length > 0", 120, "страницы PDF")
    c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка PDF")
    c.wait("[...document.querySelectorAll('.page img')].slice(0,3).every(i => i.complete && i.naturalWidth > 0)", 30)


with tempfile.TemporaryDirectory(prefix="lde-demo-") as td:
    td = Path(td)
    cfg = td / "cfg"
    (cfg / "lde").mkdir(parents=True)
    root = demo_project(td)
    from lde.project import Project
    para = next(b for b in Project(root).blocks if b.kind == "para" and "@fig:afc" in b.src)

    # стартовый экран (без проекта)
    st = StudioServer(None, str(cfg))
    try:
        c = Chrome(st.url)
        c.wait("!document.querySelector('#start').hidden", 20, "стартовый экран")
        time.sleep(0.5)
        c.screenshot(str(out / "light-0-start.png"))
        c.close()
    finally:
        st.close()

    st = StudioServer(str(root), str(cfg))
    try:
        for theme in ("light", "dark"):
            c = Chrome(st.url, dark=(theme == "dark"))
            try:
                ready(c)
                c.eval("document.querySelector('#pages').scrollTop = 0")
                time.sleep(0.5)
                c.screenshot(str(out / f"{theme}-1-document.png"))
                # абзац со вставками: открыт из текста, место подсвечено в PDF
                c.eval(f"openElement({json.dumps(para.uid)}, true)")
                c.wait("document.querySelector('#panel .chips .chip')", 10, "редактор абзаца")
                time.sleep(1.0)
                c.screenshot(str(out / f"{theme}-2-paragraph.png"))
                # формула
                c.click('#tabs button[data-tab="objects"]')
                time.sleep(0.2)
                c.click('#side-list .item[data-uid="eq:fc"]')
                c.wait("document.querySelector('#panel math-field')", 10, "редактор формулы")
                time.sleep(1.0)
                c.screenshot(str(out / f"{theme}-3-formula.png"))
                if theme == "dark":
                    continue
                # расчёт
                c.click('#tabs button[data-tab="calcs"]')
                time.sleep(0.2)
                c.click("#side-list .item")
                c.wait("document.querySelector('#params-save')", 10, "карточка расчёта")
                time.sleep(0.5)
                c.screenshot(str(out / "light-4-calc.png"))
                # величина
                c.click('#tabs button[data-tab="quantities"]')
                time.sleep(0.2)
                c.click("#side-list .item")
                time.sleep(0.6)
                c.screenshot(str(out / "light-5-quantity.png"))
                # состояние документа и проверки
                c.click("#status-pill")
                time.sleep(1.0)
                c.screenshot(str(out / "light-6-status.png"))
                c.key("Escape")
                # настройки документа
                c.click("#settings-btn")
                time.sleep(0.8)
                c.screenshot(str(out / "light-7-settings.png"))
                c.key("Escape")
                # история и версии
                c.click("#history-btn")
                time.sleep(1.0)
                c.screenshot(str(out / "light-9-history.png"))
                c.key("Escape")
                # меню проекта
                c.click("#brand")
                c.wait("document.querySelector('#proj-menu')", 5, "меню проекта")
                time.sleep(0.3)
                c.screenshot(str(out / "light-1b-menu.png"))
                c.key("Escape")
                # подключение агента (MCP) — ключ замаскирован
                c.eval("agentDialog()")
                c.wait("document.querySelector('#mcp-start, #mcp-link')", 10, "окно подключения")
                if c.eval("!!document.querySelector('#mcp-start')"):
                    c.click("#mcp-start")
                    c.wait("document.querySelector('#mcp-link')", 10, "ссылка")
                time.sleep(0.5)
                mask_token(c)
                c.screenshot(str(out / "light-13-agent-mcp.png"))
                c.eval("[...document.querySelectorAll('#dialog button')].find(b => b.textContent === 'Выключить').click()")
            finally:
                if c.errors:
                    print("ошибки JS:", *c.errors[:5], sep="\n  ")
                c.close()
    finally:
        st.close()
print(f"скриншоты: {out}")
