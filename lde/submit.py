"""lde submit — всё для сдачи: пересчёт устаревшего, проверки, PDF, DOCX, QA → «можно сдавать?».

Итог — out/submit.md (человеку) и out/submit.json. Ничего в тексте не меняет: если мешают
ошибки линтера, они перечисляются с местом и подсказкой.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from lde import calcs as calcmod
from lde.build import build_pdf
from lde.check import run_check, stale_summary
from lde.project import Project


def submit(root: Path, log=print, progress=lambda step, total, text: None, doc=None) -> dict:
    t0 = time.time()
    steps = 5
    blockers, notes = [], []
    proj = Project(root, doc)
    # 1. пересчёт устаревшего
    progress(1, steps, "пересчёт устаревших расчётов")
    st = proj.calc_statuses()
    for k in calcmod.topo_order(proj.calcs, list(proj.calcs)):
        s = calcmod.status(proj.root, proj.calcs[k], proj.calcs)
        if s.state != "ok":
            log(f"[1/5] пересчёт «{proj.calcs[k].name}»: {'; '.join(s.reasons)}")
            r = calcmod.run_calc(proj.root, proj.calcs[k], proj.calcs)
            if not r["ok"]:
                blockers.append(f"расчёт «{proj.calcs[k].name}» упал: {r['error']} (журнал: {r['log']})")
    proj = Project(root, doc)
    st = proj.calc_statuses()
    for s in stale_summary(proj, st):
        blockers.append(s["message"])
    # 2. линтер
    progress(2, steps, "проверка текста")
    log("[2/5] проверка текста (lde check)")
    issues = run_check(proj, st)
    errs = [i for i in issues if i.severity == "error" and i.rule != "stale"]
    warns = [i for i in issues if i.severity == "warning"]
    for i in errs:
        blockers.append(i.human())
    for i in warns[:30]:
        notes.append(i.human())
    # 3. PDF
    progress(3, steps, "сборка PDF")
    log("[3/5] сборка PDF")
    res = build_pdf(proj, st, force_passes=2)
    if not res["ok"]:
        for e in res["errors"]:
            blockers.append(f"PDF не собран: {e.get('file', '')}:{e.get('line', '')} {e['message']}")
    for w in res.get("warnings", []):
        notes.append(f"PDF: {w}")
    # 4–5. DOCX и его проверка
    docx = {}
    if res["ok"]:
        progress(4, steps, "DOCX для Word и его проверка")
        log("[4/5] DOCX для Word + проверка и ремонт")
        from lde.docx_export import export_docx
        docx = export_docx(proj, log=lambda *a: None)
        if not docx.get("ok"):
            blockers.append("DOCX не собран: " + docx.get("error", ""))
        else:
            for i in (docx.get("qa") or {}).get("issues", []):
                (blockers if i["severity"] == "error" else notes).append(f"DOCX ({i['source']}): {i['message']}")
    progress(5, steps, "итог")
    ok = not blockers
    name = proj.name
    out = root / "out"
    summary = {
        "ok": ok, "seconds": round(time.time() - t0, 1),
        "pdf": str(out / f"{name}.pdf") if res.get("ok") else None,
        "docx": docx.get("path"),
        "blockers": blockers, "notes": notes,
        "docx_coverage": (docx.get("qa") or {}).get("coverage"),
    }
    md = [f"# Сдача: {proj.title}", "", f"**{'Можно сдавать.' if ok else 'Пока нельзя сдавать — вот что мешает:'}**", ""]
    md += [f"- {b}" for b in blockers]
    if ok:
        md += [f"- PDF: `{summary['pdf']}`", f"- DOCX: `{summary['docx']}` (текст совпадает с PDF на {summary['docx_coverage']})"]
    if notes:
        md += ["", "Замечания (сдаче не мешают):", ""] + [f"- {n}" for n in notes]
    (out / "submit.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "submit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if ok:
        from lde import versions
        try:
            v = versions.create(root, time.strftime("Сдача %d.%m.%Y %H:%M"), kind="submit")
            summary["version"] = v["name"]
        except Exception as e:  # noqa: BLE001 — версия не должна мешать сдаче
            notes.append(f"версию «Сдача» поставить не удалось: {e}")
    log(f"[5/5] итог за {summary['seconds']} с")
    return summary
