"""Studio → ⚙ «Настройки документа» → «Переносы слов в тексте»: снять — lde.toml и PDF без переносов (Р37).

    toolchain/venv/bin/python tests/ui/test_settings_hyphenation.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import Chrome, StudioServer  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def hyph_lines(pdf: Path) -> int:
    import pymupdf
    n = 0
    for pg in pymupdf.open(str(pdf)):
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                if re.search(r"[а-яА-Яa-zA-Z]-$", "".join(s["text"] for s in ln["spans"]).rstrip()):
                    n += 1
    return n


with tempfile.TemporaryDirectory(prefix="lde-ui-hyph-") as td:
    td = Path(td)
    root = td / "proj"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    # длинный абзац с длинными словами — чтобы переносы точно были
    f = root / "text" / "01-vvedenie.md"
    f.write_text(f.read_text(encoding="utf-8") + "\n" + " ".join(
        ["Высокочастотные электромагнитные колебания характеризуются многочисленными взаимосвязанными "
         "параметрами, определяющими энергетическую эффективность радиотехнических преобразователей."] * 6) + "\n",
        encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "длинный абзац"], capture_output=True)
    st = StudioServer(str(root), str(td / "cfg"))
    c = None
    try:
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 180, "сборка")
        pdf = next((root / "out").glob("*.pdf"))
        before = hyph_lines(pdf)
        step("по умолчанию переносы есть", before > 0, before)
        c.click("#settings-btn")
        c.wait("[...document.querySelectorAll('#dialog label.check')].some(l => l.textContent.startsWith('Переносы слов'))", 10, "окно настроек")
        checked = c.eval("[...document.querySelectorAll('#dialog label.check')].find(l => l.textContent.startsWith('Переносы слов')).querySelector('input').checked")
        step("галочка «Переносы слов» стоит по умолчанию", checked is True, checked)
        c.eval("[...document.querySelectorAll('#dialog label.check')].find(l => l.textContent.startsWith('Переносы слов')).querySelector('input').click()")
        c.eval("[...document.querySelectorAll('#dialog button')].find(b => b.textContent === 'Сохранить').click()")
        c.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Настройки сохранены'))", 10, "сохранено")
        toml = (root / "lde.toml").read_text(encoding="utf-8")
        step("lde.toml: hyphenation = false", re.search(r"^hyphenation = false$", toml, re.M), toml)
        mt = pdf.stat().st_mtime
        c.wait(f"document.querySelector('#build-state').innerText.includes('обновлён')", 180, "пересборка")
        import time
        t0 = time.time()
        while pdf.stat().st_mtime == mt and time.time() - t0 < 120:
            time.sleep(0.5)
        after = hyph_lines(pdf)
        step(f"PDF пересобран без переносов ({before} → {after})", pdf.stat().st_mtime != mt and after == 0, after)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        if c:
            c.close()
        st.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
