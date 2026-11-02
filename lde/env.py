"""Пути к инструментам (тулчейн v0.1: toolchain/env.env) — без запуска bash."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_env() -> dict:
    """Настройки этой машины: toolchain/local.env (KEY=VALUE, в git не попадает)."""
    p = ROOT / "toolchain" / "local.env"
    out = {}
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = os.path.expanduser(os.path.expandvars(v.strip().strip("'\"")))
    except OSError:
        pass
    return out


def tools_dir() -> Path:
    """Каталог тулчейна (TinyTeX, загрузки, кэш): LDE_TOOLS_DIR из окружения или toolchain/local.env,
    иначе ~/.local/share/lde-tools."""
    v = os.environ.get("LDE_TOOLS_DIR") or local_env().get("LDE_TOOLS_DIR")
    return Path(v) if v else Path.home() / ".local/share/lde-tools"


def tex_bin() -> Path:
    tt = Path(os.environ.get("LDE_TINYTEX_DIR", tools_dir() / "TinyTeX"))
    return tt / "bin/x86_64-linux"


def tool(name: str) -> str:
    """Абсолютный путь к инструменту или имя (если не найден — пусть упадёт понятной ошибкой)."""
    p = tex_bin() / name
    if p.exists():
        return str(p)
    if name == "pandoc":
        p = Path(os.environ.get("LDE_PANDOC", Path.home() / ".local/bin/pandoc"))
        if p.exists():
            return str(p)
    found = shutil.which(name)
    return found or name


def venv_python() -> str:
    p = Path(os.environ.get("LDE_VENV", ROOT / "toolchain/venv")) / "bin/python"
    return str(p)


def tool_env() -> dict:
    """Окружение для дочерних процессов TeX/pandoc."""
    env = dict(os.environ)
    env["PATH"] = f"{tex_bin()}:{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
    return env


def cache_dir() -> Path:
    """Тяжёлый кэш (профиль Chrome Studio и т.п.) — в каталоге тулчейна."""
    d = Path(os.environ.get("LDE_CACHE_DIR", tools_dir() / "cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d
