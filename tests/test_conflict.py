"""Одновременная работа: агент правит файлы, пока открыт Studio. Ничего не теряется молча.

    toolchain/venv/bin/python tests/test_conflict.py
"""
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ui"))
from cdp import StudioServer  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ok = True


def step(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    ok = ok and cond


with tempfile.TemporaryDirectory(prefix="lde-conflict-") as td:
    root = Path(td) / "r"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root), "--no-build"], capture_output=True, check=True)
    srv = StudioServer(str(root))
    try:
        q = urllib.request.quote
        el = srv.api("api/element?uid=" + q("text/01-vvedenie.md#1"))
        f = root / "text" / "01-vvedenie.md"
        f.write_text(f.read_text(encoding="utf-8").replace("широко применяются", "повсеместно применяются"), encoding="utf-8")
        try:
            srv.api("api/element/save", {"uid": el["uid"], "base": el["src"], "src": el["src"] + " Правка автора."})
            step("правка устаревшей версии отклонена (конфликт)", False)
        except urllib.error.HTTPError as e:
            d = json.load(e)
            step("правка устаревшей версии отклонена (конфликт 409)", e.code == 409 and d.get("conflict"))
            step("текущая версия возвращена автору для сравнения", "повсеместно" in (d.get("current") or ""))
        txt = f.read_text(encoding="utf-8")
        step("правка агента в файле сохранена", "повсеместно" in txt and "Правка автора" not in txt)
        el2 = srv.api("api/element?uid=" + q("text/03-zakl.md#1"))
        srv.api("api/element/save", {"uid": el2["uid"], "base": el2["src"], "src": el2["src"] + " Итог."})
        log = subprocess.run(["git", "log", "--format=%s", "-2"], cwd=root, capture_output=True, text=True).stdout.split("\n")
        step("чужие изменения — отдельной точкой, затем правка Studio",
             log[0].startswith("Studio: изменён") and log[1].startswith("Изменения вне Studio"))
        el3 = srv.api("api/element?uid=" + q("text/01-vvedenie.md#1"))
        r = srv.api("api/element/save", {"uid": el3["uid"], "base": el3["src"], "src": el3["src"] + " Правка автора."})
        step("после обновления элемента правка автора применяется", r.get("changed") and "повсеместно" in f.read_text(encoding="utf-8"))
    finally:
        srv.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
