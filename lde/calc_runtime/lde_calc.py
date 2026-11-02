"""lde_calc — модуль для скриптов расчётов LDE.

    import lde_calc
    p = lde_calc.params()             # параметры из calc.toml: {"PRF": 1800.0, ...}
    fig.savefig(lde_calc.out("fig1.png"))
    lde_calc.save_values(R0=1150.0, alpha_max=0.0427)   # → out/values.json
    data = lde_calc.path("data/raw.csv")                # путь к файлу проекта

Скрипт запускается LDE во временной копии каталога расчёта; текущий каталог — эта копия,
выходы пишутся в out/. Сеть по умолчанию недоступна.
"""
import json
import os


def params() -> dict:
    p = os.environ.get("LDE_PARAMS")
    if not p:
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def out(name: str = "") -> str:
    d = os.environ.get("LDE_OUT") or os.path.join(os.getcwd(), "out")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name) if name else d


def path(rel: str) -> str:
    root = os.environ.get("LDE_PROJECT", os.getcwd())
    return os.path.join(root, rel)


def save_values(_file: str = "values.json", **values) -> str:
    """Дописать числа в JSON-выход (по умолчанию out/values.json)."""
    fn = out(_file)
    data = {}
    if os.path.exists(fn):
        with open(fn, encoding="utf-8") as f:
            data = json.load(f)
    data.update(values)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return fn
