"""LDE как обычное приложение: одна команда, ярлык в меню, одно окно-экземпляр.

  lde                    (в терминале с экраном) = lde open
  lde open [каталог]     открыть Studio отдельным окном и вернуть терминал; если Studio уже запущена —
                         новое окно той же Studio (и открыть в ней проект, если указан)
  lde app install        команда `lde` в ~/.local/bin и ярлык «LDE Studio» в меню приложений
  lde app uninstall      убрать их
  lde app status         что установлено и запущено

Работающая Studio записывает себя в $XDG_RUNTIME_DIR/lde/studio.json (pid, порт) — по нему второй
запуск находит первую. Без проекта открывается последний (как в обычных приложениях).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "lde-studio"
WM_CLASS = "LDEStudio"

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect x="4" y="4" width="56" height="56" rx="13" fill="#3b6ea8"/>
<path d="M20 15h8v27h18v7H20z" fill="#fff"/>
<path d="M36 15h12v4H36zm0 8h12v4H36z" fill="#cfe0f4"/>
</svg>
"""


# ------------------------------------------------------------------ пути ----
def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local" / "share")


def bin_dir() -> Path:
    return Path(os.environ.get("LDE_BIN_DIR") or _home() / ".local" / "bin")


def runtime_file() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    d = Path(base) / "lde" if base else Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "lde"
    return d / "studio.json"


def log_file() -> Path:
    from lde.env import cache_dir
    return cache_dir() / "studio.log"


def desktop_file() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


# ------------------------------------------------------- работающая Studio ----
def register(port: int):
    p = runtime_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "since": time.time()}), encoding="utf-8")
    tmp.replace(p)


def unregister():
    p = runtime_file()
    try:
        if json.loads(p.read_text(encoding="utf-8")).get("pid") == os.getpid():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass


def running() -> str | None:
    """URL уже запущенной Studio этого пользователя или None."""
    try:
        d = json.loads(runtime_file().read_text(encoding="utf-8"))
        os.kill(int(d["pid"]), 0)
        url = f"http://127.0.0.1:{int(d['port'])}/"
        urllib.request.urlopen(url + "api/state", timeout=3).read()
        return url
    except Exception:  # noqa: BLE001
        return None


def default_project(arg: str | None) -> Path | None:
    """Каталог из аргумента, иначе проект в текущем каталоге, иначе последний открытый."""
    from lde.project import find_project
    if arg:
        return find_project(arg)
    root = find_project(None)
    if root:
        return root
    from lde.studio.server import recent_projects
    rec = recent_projects()
    return Path(rec[0]["path"]) if rec else None


# ------------------------------------------------------------------- окно ----
def browser() -> str | None:
    if os.environ.get("LDE_BROWSER"):
        return os.environ["LDE_BROWSER"]
    return next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)


def open_window(url: str) -> subprocess.Popen | None:
    """Окно приложения (Chrome --app со своим профилем); без Chrome — браузер по умолчанию."""
    from lde.env import cache_dir
    b = browser()
    if b:
        cmd = [b, f"--app={url}", f"--user-data-dir={cache_dir() / 'studio-chrome'}", "--no-first-run",
               "--no-default-browser-check", f"--class={WM_CLASS}", "--window-size=1500,950", "--disable-features=Translate"]
    elif shutil.which("xdg-open"):
        cmd = ["xdg-open", url]
    else:
        return None
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_windows(proc, studio, grace: float | None = None):
    """Studio живёт, пока открыто её окно. Если Chrome отдал окно уже работающему процессу (или окно —
    вкладка браузера), следим за подключениями страницы: нет ни одного `grace` секунд — выходим."""
    grace = grace if grace is not None else float(os.environ.get("LDE_APP_GRACE") or 20)
    t0 = time.time()
    if proc is not None:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return
        if time.time() - t0 > 10 and not os.environ.get("LDE_BROWSER"):
            return                                   # обычный случай: закрыто последнее окно
    idle = None
    try:
        while True:
            time.sleep(1)
            if studio.clients:
                idle = None
            elif idle is None:
                idle = time.time()
            elif time.time() - idle > grace:
                return
    except KeyboardInterrupt:
        return


# ---------------------------------------------------------------- команды ----
def cmd_open(args) -> int:
    from lde.cli import err, out
    root = default_project(getattr(args, "dir", None))
    if getattr(args, "dir", None) and not root:
        err(f"В {args.dir} нет проекта LDE (lde.toml) — Studio откроется со стартовым экраном.")
    url = running()
    if url:                                           # одна Studio на пользователя: новое окно той же
        if root:
            req = urllib.request.Request(url + "api/open-project", json.dumps({"path": str(root)}).encode(),
                                         {"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=60).read()
            except Exception as e:  # noqa: BLE001
                err(f"Не удалось открыть проект в запущенной Studio: {e}")
        open_window(url)
        out(f"LDE Studio уже запущена: {url} — открыто окно" + (f" ({root})" if root else ""))
        return 0
    lf = log_file()
    cmd = [str(ROOT / "bin" / "lde"), "studio"] + ([str(root)] if root else [])
    with open(lf, "ab") as log:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, LDE_APP="1"))
    t0 = time.time()
    while time.time() - t0 < 40:
        url = running()
        if url:
            out(f"LDE Studio: {url}" + (f" — {root}" if root else "") + f"  (журнал: {lf})")
            return 0
        time.sleep(0.2)
    err(f"Studio не запустилась за 40 с — см. {lf}")
    return 1


def desktop_entry() -> str:
    lde = ROOT / "bin" / "lde"
    return f"""[Desktop Entry]
Type=Application
Version=1.5
Name=LDE Studio
GenericName=Отчёты по ГОСТ 7.32
Comment=Отчёты, ВКР и пояснительные записки по ГОСТ 7.32: PDF и Word
Exec="{lde}" open %f
Icon={APP_ID}
Terminal=false
Categories=Office;WordProcessor;
Keywords=ГОСТ;отчёт;ВКР;НИР;LaTeX;PDF;
StartupNotify=true
StartupWMClass={WM_CLASS}
Actions=new-window;

[Desktop Action new-window]
Name=Новое окно
Exec="{lde}" open
"""


def install() -> list[str]:
    done = []
    b = bin_dir()
    b.mkdir(parents=True, exist_ok=True)
    link = b / "lde"
    target = ROOT / "bin" / "lde"
    if link.is_symlink() or not link.exists():
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        done.append(f"команда: {link} → {target}")
    elif link.resolve() != target.resolve():
        done.append(f"пропущено: {link} — чужой файл, не трогаю")
    ic = icon_file()
    ic.parent.mkdir(parents=True, exist_ok=True)
    ic.write_text(ICON_SVG, encoding="utf-8")
    df = desktop_file()
    df.parent.mkdir(parents=True, exist_ok=True)
    df.write_text(desktop_entry(), encoding="utf-8")
    df.chmod(0o755)
    done.append(f"ярлык: {df}")
    done.append(f"значок: {ic}")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(df.parent)], capture_output=True)
    if not any(Path(p) == b for p in os.environ.get("PATH", "").split(os.pathsep)):
        done.append(f"внимание: {b} нет в PATH — добавьте в ~/.profile: export PATH=\"{b}:$PATH\"")
    return done


def uninstall() -> list[str]:
    done = []
    link = bin_dir() / "lde"
    if link.is_symlink() and link.resolve() == (ROOT / "bin" / "lde").resolve():
        link.unlink()
        done.append(f"убрано: {link}")
    for f in (desktop_file(), icon_file()):
        if f.exists():
            f.unlink()
            done.append(f"убрано: {f}")
    return done or ["нечего убирать"]


def status() -> list[str]:
    link = bin_dir() / "lde"
    url = running()
    return [f"команда lde: {'есть' if link.is_symlink() and link.resolve() == (ROOT / 'bin' / 'lde').resolve() else 'нет'} ({link})",
            f"ярлык в меню: {'есть' if desktop_file().exists() else 'нет'} ({desktop_file()})",
            f"Studio: {'запущена — ' + url if url else 'не запущена'}"]


def cmd_app(args) -> int:
    from lde.cli import out
    act = (getattr(args, "action", None) or "status")
    fn = {"install": install, "uninstall": uninstall, "status": status}.get(act)
    if not fn:
        out("lde app install | uninstall | status")
        return 2
    for line in fn():
        out(line)
    if act == "install":
        out("Готово: LDE Studio — в меню приложений; в терминале — просто `lde`.")
    return 0


def bare_launch_ok() -> bool:
    """`lde` без аргументов открывает приложение только человеку: терминал и экран (агенту — справка)."""
    return (sys.stdin.isatty() and sys.stdout.isatty() and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            and not os.environ.get("LDE_AGENT"))
