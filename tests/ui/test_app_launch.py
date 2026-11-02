"""LDE как приложение: `lde app install`, `lde open` (фоном, один экземпляр), выход после закрытия окон.

    toolchain/venv/bin/python tests/ui/test_app_launch.py

Окна не открываются: «браузер» — заглушка (LDE_BROWSER), которая держит соединение страницы, как окно.
Домашний каталог, ~/.local, настройки и каталог выполнения — временные.
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
LDE = str(REPO / "bin" / "lde")
ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


STUB = r'''#!/usr/bin/env python3
import os, sys, urllib.request
log = os.environ["STUB_LOG"]
url = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--app="))
with open(log, "a") as f:
    f.write(f"{os.getpid()} {' '.join(sys.argv[1:])}\n")
r = urllib.request.urlopen(url + "api/events", timeout=None)      # «окно» открыто, пока висит соединение
while r.read(64):
    pass
'''

with tempfile.TemporaryDirectory(prefix="lde-app-") as td:
    td = Path(td)
    home = td / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    stub = td / "browser-stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = td / "browser.log"
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / ".local" / "share"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_RUNTIME_DIR=str(td / "run"), LDE_CACHE_DIR=str(td / "cache"), LDE_BROWSER=str(stub), STUB_LOG=str(log),
               LDE_NO_DESKTOP="1", LDE_APP_GRACE="2", PATH=f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    (td / "run").mkdir(mode=0o700)
    run = lambda *a, **k: subprocess.run([LDE, *a], env=env, capture_output=True, text=True, timeout=k.get("timeout", 120), cwd=td)

    # --- установка
    r = run("app", "install")
    link = home / ".local" / "bin" / "lde"
    df = home / ".local" / "share" / "applications" / "lde-studio.desktop"
    step("app install: команда lde в ~/.local/bin", link.is_symlink() and link.resolve() == Path(LDE).resolve(), r.stdout + r.stderr)
    step("app install: ярлык и значок", df.exists() and (home / ".local/share/icons/hicolor/scalable/apps/lde-studio.svg").exists())
    v = subprocess.run(["desktop-file-validate", str(df)], capture_output=True, text=True) if Path("/usr/bin/desktop-file-validate").exists() else None
    step("ярлык проходит desktop-file-validate", v is None or (v.returncode == 0 and not v.stdout.strip()), v and v.stdout)
    step("ярлык: запуск `lde open`, окно группируется (StartupWMClass)", f'Exec="{REPO}/bin/lde" open %f' in df.read_text() and "StartupWMClass=LDEStudio" in df.read_text())
    r = subprocess.run(["lde", "app", "status"], env=env, capture_output=True, text=True, cwd=td)
    step("`lde` находится в PATH", r.returncode == 0 and "команда lde: есть" in r.stdout, r.stdout + r.stderr)

    p1, p2 = td / "p1", td / "p2"
    run("init", str(p1), "--title", "Первый", "--no-build")
    run("init", str(p2), "--title", "Второй", "--no-build")
    try:
        # --- первый запуск: фоном, терминал возвращается
        t0 = time.time()
        r = run("open", str(p1))
        dt = time.time() - t0
        rt = json.loads((td / "run" / "lde" / "studio.json").read_text())
        url = f"http://127.0.0.1:{rt['port']}/"
        step(f"lde open: Studio запущена фоном, терминал свободен через {dt:.1f} с", r.returncode == 0 and dt < 40 and url in r.stdout, r.stdout + r.stderr)
        st = json.loads(urllib.request.urlopen(url + "api/state", timeout=10).read())
        step("открыт указанный проект", st["project"] and st["project"]["root"] == str(p1), st.get("project"))
        time.sleep(1)
        step("окно приложения (--app, свой класс окна)", log.exists() and f"--app={url}" in log.read_text() and "--class=LDEStudio" in log.read_text())

        # --- второй запуск: та же Studio, новое окно, другой проект
        r = run("open", str(p2))
        rt2 = json.loads((td / "run" / "lde" / "studio.json").read_text())
        time.sleep(1)
        st = json.loads(urllib.request.urlopen(url + "api/state", timeout=10).read())
        step("повторный запуск — та же Studio (один экземпляр) и новое окно",
             rt2["pid"] == rt["pid"] and "уже запущена" in r.stdout and len(log.read_text().splitlines()) == 2, (r.stdout, log.read_text()))
        step("в ней открыт проект из второго запуска", st["project"]["root"] == str(p2))

        # --- без аргументов и без терминала (агент) — справка, не окно
        r = run()
        step("`lde` без терминала (агент) — справка, окно не открывается", "LDE —" in r.stdout and len(log.read_text().splitlines()) == 2)

        # --- без аргумента — последний проект
        r = run("app", "status")
        step("app status: Studio запущена", "запущена — " + url in r.stdout, r.stdout)
    finally:
        # закрыть «окна»
        for line in (log.read_text().splitlines() if log.exists() else []):
            try:
                os.kill(int(line.split()[0]), 15)
            except ProcessLookupError:
                pass
    t0 = time.time()
    while time.time() - t0 < 40 and (td / "run" / "lde" / "studio.json").exists():
        time.sleep(0.5)
    step(f"окна закрыты → Studio завершилась сама ({time.time() - t0:.0f} с)", not (td / "run" / "lde" / "studio.json").exists())
    try:
        urllib.request.urlopen(url + "api/state", timeout=2)
        step("порт закрыт", False)
    except Exception:
        step("порт закрыт", True)

    # --- без каталога — последний открытый проект
    try:
        r = subprocess.run([LDE, "open"], env=env, capture_output=True, text=True, timeout=60, cwd=home)
        rt = json.loads((td / "run" / "lde" / "studio.json").read_text())
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{rt['port']}/api/state", timeout=10).read())
        step("`lde open` без каталога — последний проект", st["project"] and st["project"]["root"] == str(p2), st.get("project"))
    finally:
        for line in log.read_text().splitlines()[2:]:
            try:
                os.kill(int(line.split()[0]), 15)
            except ProcessLookupError:
                pass
        t0 = time.time()
        while time.time() - t0 < 40 and (td / "run" / "lde" / "studio.json").exists():
            time.sleep(0.5)

    r = run("app", "uninstall")
    step("app uninstall убирает команду и ярлык", not link.exists() and not link.is_symlink() and not df.exists(), r.stdout)

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
