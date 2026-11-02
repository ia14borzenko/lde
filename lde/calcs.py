"""Расчёты: объявление (calc/<ключ>/calc.toml), актуальность по хешам, запуск, провенанс.

calc.toml:

    title = "Численный пример ЧМ-масштабирования"
    script = "script.py"
    timeout = 600                    # секунд
    inputs = ["data/raw.csv"]        # файлы проекта (и выходы других расчётов calc/<k>/out/…)
    outputs = ["fig14.png", "metrics.json"]   # относительно out/; пусто — всё, что появилось

    [params]
    PRF = { value = 1800.0, unit = "Гц", title = "Частота повторения импульсов" }
    N = 5632

    [values]                         # подписи/единицы/точность чисел из JSON-выходов
    "migration.diff_edge_m" = { unit = "м", digits = 2, title = "Дифференциальная миграция" }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from lde.env import ROOT

RUNTIME_DIR = ROOT / "lde" / "calc_runtime"
RUNTIME_VERSION = "1"
_UNSHARE_OK: bool | None = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Calc:
    key: str
    dir: Path
    title: str = ""
    script: str = "script.py"
    python: str = "python3"
    timeout: int = 600
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    params: dict = field(default_factory=dict)       # имя → значение
    params_meta: dict = field(default_factory=dict)  # имя → {unit,title}
    values_meta: dict = field(default_factory=dict)  # путь → {unit,digits,title}
    network: bool = False
    errors: list = field(default_factory=list)

    @property
    def out_dir(self) -> Path:
        return self.dir / "out"

    @property
    def name(self) -> str:
        return self.title or self.key

    def run_info(self) -> dict | None:
        p = self.out_dir / ".run.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def failure(self, root: Path) -> dict | None:
        p = root / ".lde" / "calc" / self.key / "failed.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def script_files(self) -> list[Path]:
        files = []
        for p in sorted(self.dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.dir)
            if rel.parts[0] in ("out", "__pycache__") or p.name == "calc.toml" or p.suffix in (".pyc",):
                continue
            files.append(p)
        return files

    def upstream(self) -> list[str]:
        ups = []
        for inp in self.inputs:
            m = re.match(r"^calc/([^/]+)/", inp)
            if m and m.group(1) != self.key:
                ups.append(m.group(1))
        return ups


def load_calc(root: Path, d: Path) -> Calc:
    c = Calc(key=d.name, dir=d)
    p = d / "calc.toml"
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        c.errors.append(f"calc/{d.name}/calc.toml: ошибка TOML: {e}")
        return c
    c.title = str(data.get("title", ""))
    c.script = str(data.get("script", "script.py"))
    c.python = str(data.get("python", "python3"))
    c.timeout = int(data.get("timeout", 600))
    c.inputs = list(data.get("inputs", []))
    c.outputs = list(data.get("outputs", []))
    c.network = bool(data.get("network", False))
    for k, v in (data.get("params") or {}).items():
        if isinstance(v, dict):
            c.params[k] = v.get("value")
            c.params_meta[k] = {kk: vv for kk, vv in v.items() if kk != "value"}
        else:
            c.params[k] = v
            c.params_meta[k] = {}
    for k, v in (data.get("values") or {}).items():
        c.values_meta[k] = v if isinstance(v, dict) else {"unit": str(v)}
    if not (d / c.script).exists():
        c.errors.append(f"calc/{d.name}: нет скрипта {c.script}")
    return c


def load_calcs(root: Path) -> dict[str, Calc]:
    out = {}
    cd = root / "calc"
    if cd.is_dir():
        for d in sorted(cd.iterdir()):
            if d.is_dir() and (d / "calc.toml").exists():
                out[d.name] = load_calc(root, d)
    return out


# ---------------------------------------------------------------- hashing ----

def input_fingerprint(root: Path, c: Calc, calcs: dict[str, Calc]) -> dict:
    """Всё, от чего зависит результат: скрипты, параметры, входы, выходы предшественников."""
    scripts = {str(p.relative_to(c.dir)): file_hash(p) for p in c.script_files()}
    inputs = {}
    for inp in c.inputs:
        p = root / inp
        if p.is_file():
            inputs[inp] = file_hash(p)
        elif p.is_dir():
            inputs[inp] = _sha("".join(file_hash(q) for q in sorted(p.rglob("*")) if q.is_file()).encode())
        else:
            inputs[inp] = "missing"
    ups = {}
    for u in c.upstream():
        uc = calcs.get(u)
        info = uc.run_info() if uc else None
        ups[u] = (info or {}).get("outputs_hash", "none")
    fp = {
        "scripts": scripts,
        "params": c.params,
        "inputs": inputs,
        "upstream": ups,
        "python": c.python,
        "runtime": RUNTIME_VERSION,
    }
    fp["hash"] = _sha(json.dumps(fp, sort_keys=True, ensure_ascii=False, default=str).encode())
    return fp


def _fmt_param(v) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    return str(v)


@dataclass
class CalcStatus:
    key: str
    state: str                 # ok | stale | never | error
    reasons: list = field(default_factory=list)   # человеческие причины
    changed_params: list = field(default_factory=list)
    failure: dict | None = None
    fingerprint: dict | None = None


def status(root: Path, c: Calc, calcs: dict[str, Calc], _cache: dict | None = None) -> CalcStatus:
    if c.errors:
        return CalcStatus(c.key, "error", list(c.errors))
    fp = input_fingerprint(root, c, calcs)
    info = c.run_info()
    fail = c.failure(root)
    if fail and fail.get("input_hash") != fp["hash"]:
        fail = None   # упал на другой версии входов — уже неактуально
    if not info:
        st = CalcStatus(c.key, "never", ["расчёт ещё не запускался"], failure=fail, fingerprint=fp)
        return st
    reasons, changed = [], []
    if info.get("input_hash") == fp["hash"]:
        missing = [o for o in c.outputs if not (c.out_dir / o).exists()]
        if missing:
            return CalcStatus(c.key, "stale", [f"нет выхода {', '.join(missing)}"], failure=fail, fingerprint=fp)
        return CalcStatus(c.key, "ok", failure=None, fingerprint=fp)
    old = info.get("fingerprint", {})
    op = old.get("params", {})
    for k in sorted(set(op) | set(c.params)):
        if op.get(k) != c.params.get(k):
            changed.append(k)
            if k not in c.params:
                reasons.append(f"удалён параметр {k}")
            elif k not in op:
                reasons.append(f"добавлен параметр {k}")
            else:
                reasons.append(f"изменён параметр {k}: {_fmt_param(op.get(k))} → {_fmt_param(c.params.get(k))}")
    if old.get("scripts") != fp["scripts"]:
        names = sorted(set(old.get("scripts", {})) ^ set(fp["scripts"]) |
                       {k for k in fp["scripts"] if old.get("scripts", {}).get(k) != fp["scripts"][k]})
        reasons.append("изменён скрипт " + ", ".join(names) if names else "изменён скрипт")
    for k, v in fp["inputs"].items():
        if old.get("inputs", {}).get(k) != v:
            reasons.append(f"изменён файл {k}" if v != "missing" else f"нет входного файла {k}")
    for k, v in fp["upstream"].items():
        if old.get("upstream", {}).get(k) != v:
            uc = calcs.get(k)
            reasons.append(f"обновлён расчёт «{uc.name if uc else k}»")
    if old.get("python") != fp["python"] or old.get("runtime") != fp["runtime"]:
        reasons.append("изменилась среда выполнения")
    if not reasons:
        reasons.append("изменились входы")
    return CalcStatus(c.key, "stale", reasons, changed, failure=fail, fingerprint=fp)


def topo_order(calcs: dict[str, Calc], keys: list[str]) -> list[str]:
    seen, order = set(), []

    def visit(k, stack=()):
        if k in seen or k not in calcs:
            return
        if k in stack:
            return
        for u in calcs[k].upstream():
            visit(u, stack + (k,))
        seen.add(k)
        order.append(k)

    for k in keys:
        visit(k)
    want = set(keys)
    # добавить зависимые: если пересчитали предшественника, потомок тоже станет устаревшим —
    # статус проверяется заново перед каждым запуском
    return [k for k in order if k in want]


# ------------------------------------------------------------------- run ----

def _unshare_ok() -> bool:
    global _UNSHARE_OK
    if _UNSHARE_OK is None:
        try:
            r = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10)
            _UNSHARE_OK = r.returncode == 0
        except Exception:
            _UNSHARE_OK = False
    return _UNSHARE_OK


def humanize_error(log: str, code: int | None, timed_out: bool, timeout: int) -> str:
    if timed_out:
        return f"не уложился в {timeout} с и был остановлен"
    lines = [l for l in log.strip().splitlines() if l.strip()]
    for l in reversed(lines):
        m = re.match(r"(?:\w+\.)*ModuleNotFoundError: No module named '([^']+)'", l)
        if m:
            return f"нет модуля {m.group(1)}"
        m = re.match(r"(?:\w+\.)*FileNotFoundError: .*?'([^']+)'", l)
        if m:
            return f"не найден файл {m.group(1)}"
        m = re.match(r"^(\w+(?:Error|Exception)): (.*)$", l)
        if m:
            return f"{m.group(1)}: {m.group(2)[:200]}"
    if lines:
        return lines[-1][:200]
    return f"завершился с кодом {code}"


def run_calc(root: Path, c: Calc, calcs: dict[str, Calc], log=print) -> dict:
    """Выполнить расчёт. Успех — выходы атомарно заменяют calc/<k>/out. Неудача — out не трогается."""
    fp = input_fingerprint(root, c, calcs)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = root / ".lde" / "runs" / f"{c.key}-{stamp}-{os.getpid()}"
    work = run_dir / "work"
    work.mkdir(parents=True)
    for p in c.script_files():
        dst = work / p.relative_to(c.dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    (work / "out").mkdir(exist_ok=True)
    params_file = run_dir / "params.json"
    params_file.write_text(json.dumps(c.params, ensure_ascii=False, indent=1), encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "LDE_PARAMS": str(params_file),
        "LDE_OUT": str(work / "out"),
        "LDE_PROJECT": str(root),
        "LDE_CALC": c.key,
        "MPLCONFIGDIR": str(run_dir / "mpl"),
        "MPLBACKEND": "Agg",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(RUNTIME_DIR) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        "XDG_CACHE_HOME": str(run_dir / "cache"),   # кэши библиотек — не в домашний каталог
    })
    py = c.python
    if py in ("python3", "system"):
        py = shutil.which("python3") or "/usr/bin/python3"
    cmd = [py, c.script]
    isolated = False
    if not c.network and _unshare_ok():
        cmd = ["unshare", "-rn"] + cmd
        isolated = True
    t0 = time.time()
    log_path = run_dir / "log.txt"
    timed_out = False
    with open(log_path, "wb") as lf:
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            code = proc.wait(timeout=c.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
    dur = time.time() - t0
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    produced = sorted(str(p.relative_to(work / "out")) for p in (work / "out").rglob("*") if p.is_file())
    missing = [o for o in c.outputs if not (work / "out" / o).exists()]
    ok = (code == 0 and not timed_out and not missing)
    fail_file = root / ".lde" / "calc" / c.key / "failed.json"
    if not ok:
        msg = humanize_error(log_text, code, timed_out, c.timeout)
        if code == 0 and missing:
            msg = "скрипт не создал " + ", ".join(missing)
        fail_file.parent.mkdir(parents=True, exist_ok=True)
        info = {"time": time.time(), "error": msg, "log": str(log_path.relative_to(root)),
                "input_hash": fp["hash"], "code": code}
        fail_file.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": False, "key": c.key, "error": msg, "log": str(log_path), "duration": dur}
    outputs = c.outputs or produced
    oh = _sha("".join(f"{o}:{file_hash(work / 'out' / o)}" for o in sorted(outputs)).encode())
    info = {
        "calc": c.key, "title": c.title, "time": time.time(), "duration": round(dur, 2),
        "input_hash": fp["hash"], "fingerprint": fp, "params": c.params,
        "outputs": outputs, "outputs_hash": oh, "isolated_network": isolated,
        "command": " ".join(cmd[-2:]), "log": ".log.txt",
    }
    (work / "out" / ".run.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str),
                                            encoding="utf-8")
    shutil.copy2(log_path, work / "out" / ".log.txt")
    # атомарная замена out/
    old = c.dir / f".out.old-{os.getpid()}"
    if c.out_dir.exists():
        os.rename(c.out_dir, old)
    shutil.move(str(work / "out"), str(c.out_dir))
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if fail_file.exists():
        fail_file.unlink()
    shutil.rmtree(run_dir, ignore_errors=True)
    return {"ok": True, "key": c.key, "duration": dur, "outputs": outputs}


# ---------------------------------------------------------------- values ----

def _json_outputs(c: Calc) -> dict:
    merged: dict = {}
    if not c.out_dir.is_dir():
        return merged
    for p in sorted(c.out_dir.glob("*.json")):
        if p.name.startswith("."):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            merged.setdefault(p.stem, data)
            for k, v in data.items():
                merged.setdefault(k, v)
    return merged


def _dig(d, path: list[str]):
    cur = d
    for part in path:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None, False
    return cur, True


def lookup_value(c: Calc, path: str) -> dict | None:
    """Значение по пути «a.b.c» из параметров последнего удачного запуска и JSON-выходов."""
    info = c.run_info()
    parts = path.split(".")
    meta = dict(c.values_meta.get(path, {}))
    if info and len(parts) == 1 and parts[0] in (info.get("params") or {}):
        v = info["params"][parts[0]]
        pm = c.params_meta.get(parts[0], {})
        meta = {**pm, **meta}
        return {"value": v, "unit": meta.get("unit", ""), "digits": meta.get("digits"),
                "title": meta.get("title", ""), "source": "param"}
    data = _json_outputs(c)
    v, ok = _dig(data, parts)
    if not ok:
        return None
    if isinstance(v, dict) and "value" in v:
        meta = {**{k: vv for k, vv in v.items() if k != "value"}, **meta}
        v = v["value"]
    if isinstance(v, (dict, list)):
        return None
    return {"value": v, "unit": meta.get("unit", ""), "digits": meta.get("digits"),
            "title": meta.get("title", ""), "source": "output"}


def all_value_paths(c: Calc) -> list[tuple[str, object]]:
    """Все доступные числа расчёта (для поиска «вставить число»)."""
    out = []
    info = c.run_info() or {}
    for k, v in (info.get("params") or {}).items():
        if isinstance(v, (int, float)):
            out.append((k, v))

    def rec(prefix, d):
        if isinstance(d, dict):
            if "value" in d and isinstance(d["value"], (int, float)):
                out.append((prefix, d["value"]))
                return
            for k, v in d.items():
                rec(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(d, (int, float)) and not isinstance(d, bool):
            out.append((prefix, d))

    if c.out_dir.is_dir():
        for p in sorted(c.out_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                rec("", json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    seen = set()
    res = []
    for k, v in out:
        if k not in seen:
            seen.add(k)
            res.append((k, v))
    return res
