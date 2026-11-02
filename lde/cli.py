"""CLI `lde`: одна команда на действие; вывод — по-русски, коротко, с файлом и строкой."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lde import __version__
from lde.env import ROOT

HELP = """LDE — среда составления отчётов по ГОСТ 7.32-2017 (PDF + DOCX)

Проект
  init <каталог>              новый отчёт с примером (сразу собирается)
  import-docx <файл> <каталог> проект из существующего Word-отчёта
  open [<каталог>]            открыть LDE Studio окном (в терминале — просто `lde`); уже запущена — новое окно
  app install                 команда lde и ярлык «LDE Studio» в меню приложений (app uninstall | status)
  studio [<каталог>]          Studio в этом терминале (для отладки; --no-browser — без окна)
  mcp [<каталог>]             подключить агента по ссылке (MCP-сервер LDE; --stdio — для клиента)

Работа с текстом (для агента и опытного пользователя)
  new quantity|calc|source|figure|table|formula|section|abbr …   создать элемент
  new document <ключ> --title "…"  ещё один документ проекта (общие величины, расчёты, источники)
  list quantities|values|labels|sources|calcs|documents           что есть в проекте
  info <метка|расчёт>         откуда рисунок/число: скрипт, параметры, время, журнал

Проверка и сборка
  status                      что устарело и почему
  check [--json]              линтер: ошибки и замечания с местом
  update [<расчёт>…]          пересчитать устаревшее (в порядке зависимостей)
  build                       собрать PDF (out/<имя>.pdf)
  docx                        экспорт DOCX + проверка и ремонт
  submit                      всё сразу: пересчёт, PDF, DOCX, проверки → «можно сдавать?»

История
  save -m "сообщение"         точка сохранения (git-коммит)
  undo / redo                 отменить последнее действие (повторно — предыдущее) / повторить
  version "название" [-m …]    именованная версия (ставится сама при submit и при ссылке)
  versions · history          версии · все точки по сеансам
  diff <версия> [<версия>]    что изменилось (по элементам, пословно); без второй — до текущего
  restore <версия> [--only …] вернуть проект (или файлы) к версии — новой точкой, без потерь

Обращения агента (если LDE чего-то не умеет или пришлось делать рутину вручную)
  feedback check "что нужно"   есть ли это в LDE и какой командой
  feedback new --kind bug|missing|routine|docs|ux --title "…" [--asked --did --manual --want]
  feedback analyze            признаки ручной рутины в проекте · feedback list|show <id>

Служебное
  doctor [--fix]              проверить/доустановить инструменты
  skill-status                скилл агента lde установлен?
  version

Каталог проекта: аргумент --project/-C, переменная LDE_PROJECT или поиск вверх от
текущего каталога (по файлу lde.toml).
Документ проекта: -D/--doc <ключ> (по умолчанию основной — text/); список: lde list documents.
"""


def out(msg=""):
    print(msg, flush=True)


def err(msg):
    print(msg, file=sys.stderr, flush=True)


def _color(s, code):
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{s}\033[0m"
    return s


def load(args):
    from lde.project import Project, find_project
    root = find_project(getattr(args, "project", None))
    if not root:
        err("Не найден проект LDE (нет lde.toml). Укажите каталог: lde <команда> -C <каталог>, "
            "или создайте: lde init <каталог>")
        sys.exit(2)
    from lde.project import list_documents
    doc = getattr(args, "doc", None) or os.environ.get("LDE_DOC") or "main"
    keys = [d["key"] for d in list_documents(root)]
    if doc not in keys:
        err(f"В проекте нет документа «{doc}». Есть: {', '.join(keys)} (lde list documents)")
        sys.exit(2)
    return Project(root, doc)


# ------------------------------------------------------------------ init ----
def cmd_init(args):
    dst = Path(args.dir).expanduser().resolve()
    if dst.exists() and any(dst.iterdir()) and not args.force:
        err(f"Каталог {dst} не пуст. Выберите другой или добавьте --force.")
        return 2
    src = ROOT / "template" / "example"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    if args.title:
        p = dst / "lde.toml"
        t = p.read_text(encoding="utf-8")
        t = re.sub(r'^topic = ".*"$', f'topic = "{args.title}"', t, flags=re.M)
        p.write_text(t, encoding="utf-8")
    from lde import gitops
    gitops.ensure_repo(dst)
    out(f"Создан проект: {dst}")
    if not args.no_build:
        from lde.project import Project
        proj = Project(dst)
        _update(proj, [], quiet=False)
        proj.load()
        _build(proj)
    gitops.commit(dst, "Новый отчёт (lde init)")
    out(f"Дальше: lde studio {dst}")
    return 0


# ---------------------------------------------------------------- status ----
def cmd_status(args):
    from lde.check import stale_summary
    proj = load(args)
    st = proj.calc_statuses()
    nfig = sum(1 for b in proj.blocks if b.kind == "figure")
    ntab = sum(1 for b in proj.blocks if b.kind == "table")
    neq = sum(1 for b in proj.blocks if b.kind == "formula" and proj.numbers.get(id(b)))
    out(f"{proj.title}")
    out(f"  разделов: {len(proj.files)}, рисунков: {nfig}, таблиц: {ntab}, нумерованных формул: {neq}, "
        f"величин: {len(proj.quantities)}, источников: {len(proj.cite_order)}, расчётов: {len(proj.calcs)}")
    stale = stale_summary(proj, st)
    if args.json:
        print(json.dumps({"stale": stale, "calcs": {k: s.__dict__ for k, s in st.items()}}, ensure_ascii=False,
                         default=str, indent=1))
        return 1 if stale else 0
    if not stale and not any(s.failure for s in st.values()):
        out(_color("Всё актуально.", "32"))
    for s in stale:
        out(_color("Устарело: ", "33") + s["message"])
    for k, s in st.items():
        if s.failure:
            out(_color("Ошибка: ", "31") + f"расчёт «{proj.calcs[k].name}» упал: {s.failure.get('error')} "
                f"(журнал: {s.failure.get('log')})")
    if stale:
        out("Обновить: lde update")
    return 1 if stale else 0


# ----------------------------------------------------------------- check ----
def cmd_check(args):
    from lde.check import run_check
    proj = load(args)
    t0 = time.time()
    issues = run_check(proj)
    if args.json:
        print(json.dumps({"issues": [i.to_dict() for i in issues],
                          "errors": sum(i.severity == "error" for i in issues),
                          "warnings": sum(i.severity == "warning" for i in issues),
                          "seconds": round(time.time() - t0, 2)}, ensure_ascii=False, indent=1))
    else:
        order = {"error": 0, "warning": 1, "info": 2}
        shown = [i for i in issues if args.all or i.severity != "info"]
        for i in sorted(shown, key=lambda i: (order[i.severity], i.file, i.line)):
            line = i.human()
            out(_color(line, "31" if i.severity == "error" else "33" if i.severity == "warning" else "37"))
        ne = sum(i.severity == "error" for i in issues)
        nw = sum(i.severity == "warning" for i in issues)
        cand = [i for i in issues if i.rule == "symbol-undeclared"]
        if cand and not args.all:
            names = ", ".join(re.search(r"\$[^$]+\$", i.message).group(0) for i in cand[:6])
            out(f"К сведению: обозначения, набранные формулой несколько раз, — кандидаты в величины: {names}"
                + (" …" if len(cand) > 6 else "") + " (lde check --all)")
        if not ne and not nw:
            out(_color("Замечаний нет.", "32"))
        else:
            out(f"Итого: ошибок {ne}, замечаний {nw}.")
    return 1 if any(i.severity == "error" for i in issues) else 0


# ----------------------------------------------------------------- build ----
def _build(proj, quiet=False):
    from lde.build import build_pdf
    from lde.check import stale_summary
    st = proj.calc_statuses()
    res = build_pdf(proj, st)
    if not res["ok"]:
        err(_color("PDF не собран:", "31"))
        for e in res["errors"]:
            where = f"{e['file']}:{e['line']} " if e.get("file") else ""
            what = f"({e['element']}) " if e.get("element") else ""
            err(f"  {where}{what}{e['message']}")
        err("  подробности: out/build-error.log; прошлый PDF не тронут")
        return 1
    if not quiet:
        out(f"PDF: {res['pdf']} ({res['duration']} с)")
        for s in stale_summary(proj, st):
            out(_color("  внимание: ", "33") + s["message"] + " — в PDF помечено красным; lde update")
    return 0


def cmd_build(args):
    return _build(load(args))


# ---------------------------------------------------------------- update ----
def _update(proj, keys, quiet=False, force=False):
    from lde import calcs as cm
    st = proj.calc_statuses()
    todo = keys or [k for k, s in st.items() if s.state != "ok" or force]
    if not todo:
        if not quiet:
            out("Пересчитывать нечего — всё актуально.")
        return 0
    order = cm.topo_order(proj.calcs, list(proj.calcs))
    rc = 0
    for k in order:
        c = proj.calcs[k]
        s = cm.status(proj.root, c, proj.calcs)
        if k not in todo and s.state == "ok":
            continue
        if s.state == "ok" and not force and k not in keys:
            continue
        if not quiet:
            why = "; ".join(s.reasons) if s.reasons else "по запросу"
            out(f"Расчёт «{c.name}»: {why} … ", )
        r = cm.run_calc(proj.root, c, proj.calcs)
        if r["ok"]:
            if not quiet:
                out(_color(f"  готово за {r['duration']:.1f} с", "32"))
        else:
            rc = 1
            out(_color(f"  упал: {r['error']}", "31") + f" (журнал: {r['log']})")
            out("  прошлые результаты сохранены")
    return rc


def cmd_update(args):
    proj = load(args)
    for k in args.calcs:
        if k not in proj.calcs:
            err(f"Нет расчёта «{k}». Есть: {', '.join(proj.calcs) or 'никаких'}")
            return 2
    return _update(proj, args.calcs, force=args.force)


# ------------------------------------------------------------------ save ----
def cmd_save(args):
    from lde import gitops
    proj = load(args)
    h = gitops.commit(proj.root, args.message or "Точка сохранения")
    out(f"Сохранено: {h[:8]}" if h else "Изменений нет — сохранять нечего.")
    return 0


def cmd_undo(args):
    from lde import versions
    proj = load(args)
    try:
        r = versions.undo(proj.root)
    except RuntimeError as e:
        err(str(e))
        return 1
    out(f"Отменено: {r['subject']}  (повторить: lde redo)")
    return 0


def cmd_redo(args):
    from lde import versions
    proj = load(args)
    try:
        r = versions.redo(proj.root)
    except RuntimeError as e:
        err(str(e))
        return 1
    out(f"Повторено: {r['subject']}")
    return 0


def cmd_version(args):
    from lde import versions
    if not args.name:
        out(f"LDE {__version__} ({ROOT})")
        return 0
    proj = load(args)
    v = versions.create(proj.root, args.name, args.message or "")
    out(f"Версия «{v['name']}» ({v['tag']}) на точке {v['commit'][:8]}")
    return 0


def cmd_versions(args):
    from lde import versions
    proj = load(args)
    vs = versions.list_versions(proj.root)
    if not vs:
        out("Версий нет. Сохранить: lde version \"название\"")
    for v in vs:
        out(f"{time.strftime('%d.%m.%Y %H:%M', time.localtime(v['time']))}\t{v['name']}\t[{v['kind_ru']}]\t{v['commit'][:8]}"
            + (f"\t{v['note']}" if v["note"] else ""))
    return 0


def cmd_history(args):
    from lde import versions
    proj = load(args)
    for sn in versions.history(proj.root, args.n):
        a, b = (time.strftime("%d.%m %H:%M", time.localtime(x)) for x in (sn["start"], sn["end"]))
        out(f"== {a} — {b[-5:]} · {', '.join(sn['authors'])} · {len(sn['points'])}")
        for p in sn["points"]:
            tags = "".join(f"  [{v['name']}]" for v in p["versions"])
            out(f"  {p['hash'][:8]}  {time.strftime('%H:%M', time.localtime(p['time']))}  {p['subject']}{tags}")
    return 0


def cmd_diff(args):
    from lde import versions
    from lde.studio import model
    proj = load(args)
    try:
        base = versions.resolve(proj.root, args.base)
        target = versions.resolve(proj.root, args.target) if args.target else None
    except ValueError as e:
        err(str(e))
        return 2
    d = model.changes(proj.root, proj.doc_key, base, target)
    if not d["count"]:
        out("Различий нет.")
    for g in d["groups"]:
        out(f"# {g['where']}: {g['summary']}")
        for it in g["items"]:
            out(f"  [{ {'added': '+', 'removed': '−', 'changed': '~', 'moved': '↕'}[it['op']] }] {it['title']}  ({it['file']})")
            for op, t in it.get("words", []):
                if op != "=":
                    out(f"      {'+' if op == '+' else '−'} {t.strip()[:200]}")
    for o in d["other"]:
        out(f"# также: {o['what']}")
    return 0


def cmd_feedback(args):
    """Обращения агентов: check — есть ли это в LDE; new — написать; analyze — рутина в проекте; list/show/status."""
    from lde import feedback as fb
    from lde.project import find_project
    root = find_project(getattr(args, "project", None))
    act = args.action
    try:
        if act == "check":
            text = " ".join(args.rest)
            if not text:
                err('lde feedback check "что нужно сделать"')
                return 2
            r = fb.check(text)
            if r["found"]:
                out("Похоже, это уже есть в LDE:")
                for c in r["found"]:
                    out(f"  • {c['what']}\n      {c['how']}")
            if r["gaps"]:
                out("Известные пробелы LDE (можно сразу писать обращение kind=missing):")
                for g in r["gaps"]:
                    out(f"  • {g}")
            if not r["found"] and not r["gaps"]:
                out("Подходящего в LDE не найдено. Если сделали вручную — напишите обращение: lde feedback new --kind missing …")
            return 0
        if act == "new":
            body = Path(args.file).read_text(encoding="utf-8") if args.file else ""
            r = fb.new(args.kind or "", args.title or "", args.asked or "", args.did or "", args.manual or "",
                       args.want or "", body, args.severity or "medium", args.agent or "", root)
            out(("Дописано в существующее обращение (повтор " + str(r["repeat"]) + "): ") if r["repeat"] > 1 else "Обращение записано: ")
            out("  " + r["path"])
            if r["check"]["found"]:
                out("Сверка: возможно, подходящее уже есть — " + "; ".join(c["how"] for c in r["check"]["found"][:3]))
            return 0
        if act == "analyze":
            if not root:
                err("нужен проект LDE (-C <каталог>)")
                return 2
            rs = fb.analyze(root, float(args.hours or 6))
            if not rs:
                out("Признаков рутины не найдено.")
            for x in rs:
                out(f"• [{x['kind']}] {x['text']}\n    → {x['hint']}")
            if rs:
                out("Если это мешало работе — запишите обращение: lde feedback new --kind routine …")
            return 0
        if act == "list":
            rows = fb.list_reports(args.all, root)
            if not rows:
                out("Обращений нет." if args.all else "Открытых обращений нет (все: lde feedback list --all).")
            for r in rows:
                out(f"{r['id']}\t{r['kind_ru']}\t{r['severity']}\t×{r['repeats']}\t{r['status']}\t{r['title']}")
            return 0
        if act == "show":
            out(fb.show(args.rest[0], root))
            return 0
        if act == "status":
            fb.set_status(args.rest[0], args.rest[1], root)
            out("Статус изменён.")
            return 0
    except (ValueError, IndexError, FileNotFoundError) as e:
        err(str(e) or "не хватает аргументов")
        return 2
    err("lde feedback check \"…\" | new --kind bug|missing|routine|docs|ux --title \"…\" [--asked … --did … --manual … --want … "
        "--severity low|medium|high|blocker --file подробно.md] | analyze | list [--all] | show <id> | status <id> <статус>")
    return 2


def cmd_merge_driver(args):
    from lde.merge import driver
    return driver(args.base, args.ours, args.theirs, args.path or "")


def cmd_merge(args):
    from lde.merge import parse_conflict
    from lde.studio import model
    proj = load(args)
    cs = [b for b in proj.blocks if b.kind == "conflict"]
    if args.resolve:
        uid, how = args.resolve
        b = next((x for x in cs if x.uid == uid or f"{x.file}:{x.line}" == uid), None)
        if not b:
            err(f"нет конфликта «{uid}» (lde merge — список)")
            return 1
        c = parse_conflict(b.src)
        new = {"ours": c["ours"], "theirs": c["theirs"], "both": c["ours"] + "\n\n" + c["theirs"],
               "base": c["base"]}.get(how)
        if new is None:
            err("как решить: ours (моё) | theirs (чужое) | both (оба) | base (как было)")
            return 2
        model.save_block(proj.root, b.uid, b.src, new, message=f"Конфликт решён: {how} ({b.file}:{b.line})",
                         doc=proj.doc_key)
        out(f"Решено: {b.file}:{b.line} → {how}")
        return 0
    if not cs:
        out("Конфликтов нет.")
        return 0
    for b in cs:
        c = parse_conflict(b.src)
        out(f"{b.file}:{b.line}  uid={b.uid}  ({c.get('l_ours', 'моё')} / {c.get('l_theirs', 'чужое')})")
        for lab, key in (("моё", "ours"), ("было", "base"), ("чужое", "theirs")):
            out(f"    {lab}: {re.sub(r'\s+', ' ', c.get(key, ''))[:160]}")
    out("Решить: lde merge --resolve <uid> ours|theirs|both|base  (или своим текстом — правкой файла)")
    return 1


def cmd_restore(args):
    from lde import versions
    proj = load(args)
    try:
        r = versions.restore(proj.root, args.ref, args.only or None)
    except (ValueError, RuntimeError) as e:
        err(str(e))
        return 1
    out(f"Возвращено к «{r['name']}»" if r["changed"] else "Уже совпадает с этой версией")
    return 0


# ------------------------------------------------------------------ list ----
def cmd_list(args):
    from lde import calcs as cm
    from lde.fmt import format_value
    proj = load(args)
    what = args.what
    if what in ("quantities", "q", "величины"):
        for k, q in proj.quantities.items():
            out(f"{{{k}}}\t{q.meaning}{', ' + q.unit if q.unit else ''}")
    elif what in ("values", "v", "числа"):
        for ck, c in proj.calcs.items():
            if args.name and ck != args.name:
                continue
            out(f"# расчёт «{c.name}» ({ck})" + ("" if c.run_info() else " — ещё не запускался: lde update"))
            for path, v in cm.all_value_paths(c):
                meta = cm.lookup_value(c, path) or {}
                try:
                    shown = format_value(v, meta.get("unit", ""), meta.get("digits"))["text"]
                except Exception:
                    shown = str(v)
                t = f"  ({meta['title']})" if meta.get("title") else ""
                out(f"{{={ck}.{path}}}\t= {shown}{t}")
    elif what in ("labels", "refs", "метки"):
        for lab, t in proj.labels.items():
            num = f"({t.number})" if t.kind == "eq" else t.number
            title = re.sub(r"\s+", " ", t.title)[:70]
            out(f"@{lab}\t{num}\t{title}\t{t.block.file}:{t.block.line}")
    elif what in ("sources", "источники"):
        from lde.bib import format_entry
        for k, e in proj.bib.items():
            n = proj.cite_num.get(k)
            out(f"[@{k}]\t{'[' + str(n) + ']' if n else '(не цитируется)'}\t{format_entry(e)[:100]}")
    elif what in ("documents", "docs", "документы"):
        for d in proj.documents():
            mark = "*" if d["key"] == proj.doc_key else " "
            where = "text/" if d["key"] == "main" else f"documents/{d['key']}/"
            out(f"{mark} {d['key']}\t{d['title']}\t{where}\tout/{d['name']}.pdf")
    elif what in ("calcs", "расчёты"):
        st = proj.calc_statuses()
        for k, c in proj.calcs.items():
            s = st[k]
            state = {"ok": "актуален", "stale": "устарел", "never": "не запускался", "error": "ошибка"}[s.state]
            out(f"{k}\t{c.name}\t{state}\tпараметры: " + ", ".join(f"{p}={v}" for p, v in c.params.items()))
    else:
        err("Что показать: quantities | values [расчёт] | labels | sources | calcs | documents")
        return 2
    return 0


# ------------------------------------------------------------------ info ----
def cmd_info(args):
    proj = load(args)
    what = args.what
    from lde import calcs as cm
    calc_key = None
    if what in proj.calcs:
        calc_key = what
    elif what.lstrip("@") in proj.labels:
        t = proj.labels[what.lstrip("@")]
        b = t.block
        out(f"{proj.title_of(b)} — {b.file}:{b.line}")
        deps = proj.block_calcs(b)
        if b.kind == "figure":
            out(f"  файл: {b.path or ', '.join(sf[1] for sf in b.subfigs)}")
        if not deps:
            out("  не зависит от расчётов")
            return 0
        calc_key = sorted(deps)[0]
    elif what.startswith("{=") or "." in what:
        path = what.strip("{}=")
        calc_key = path.split(".", 1)[0]
        v = proj.value(path)
        out(f"Число {{={path}}}: {v['value'] if v else 'нет'}")
    if not calc_key or calc_key not in proj.calcs:
        err(f"Не знаю «{what}». Примеры: lde info fig:afc, lde info afc, lde info afc.f_c")
        return 2
    c = proj.calcs[calc_key]
    s = cm.status(proj.root, c, proj.calcs)
    info = c.run_info()
    out(f"Расчёт «{c.name}» (calc/{c.key}/{c.script})")
    out(f"  состояние: {dict(ok='актуален', stale='устарел', never='не запускался', error='ошибка')[s.state]}"
        + (f" — {'; '.join(s.reasons)}" if s.reasons else ""))
    out("  параметры: " + ", ".join(f"{k}={v}" for k, v in c.params.items()))
    if c.inputs:
        out("  входы: " + ", ".join(c.inputs))
    if info:
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(info["time"]))
        out(f"  последний удачный запуск: {when}, {info.get('duration')} с; выходы: {', '.join(info.get('outputs', []))}")
        out(f"  журнал: calc/{c.key}/out/.log.txt")
    if s.failure:
        out(f"  последняя попытка упала: {s.failure.get('error')} (журнал: {s.failure.get('log')})")
    return 0


# ------------------------------------------------------------------- new ----
def _append(path: Path, text: str):
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if not old or old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
    path.write_text(old + sep + text.rstrip("\n") + "\n", encoding="utf-8")


def cmd_new(args):
    proj = load(args)
    kind = args.kind
    root = proj.root
    a = args.rest
    opts = _kv(a)
    name = opts.pop("_", [None])[0] if opts.get("_") else None
    if kind in ("quantity", "величина"):
        if not name:
            err('lde new quantity <ключ> --meaning "смысл" --unit "ед." [--latex "…"]  (ключ как пишется: R_ц, f_0, φ_1)')
            return 2
        if name in proj.quantities:
            err(f"Величина {{{name}}} уже есть (quantities.toml:{proj.quantities[name].line})")
            return 1
        key = f'["{name}"]' if not re.fullmatch(r"[A-Za-z0-9_\-]+", name) else f"[{name}]"
        lines = [key, f'meaning = "{opts.get("meaning", [""])[0]}"', f'unit = "{opts.get("unit", [""])[0]}"']
        if opts.get("latex"):
            lines.append(f"latex = '{opts['latex'][0]}'")
        if opts.get("value"):
            lines.append(f'value = "{opts["value"][0]}"')
        _append(root / "quantities.toml", "\n".join(lines))
        from lde.quantities import latex_from_key
        out(f"Добавлена величина {{{name}}} (набор: {opts.get('latex', [latex_from_key(name)])[0]}). В тексте: {{{name}}}")
        return 0
    if kind in ("abbr", "сокращение"):
        if not name or not opts.get("meaning"):
            err('lde new abbr РСА --meaning "радиолокатор с синтезированной апертурой"')
            return 2
        _append(root / "abbreviations.toml", f'"{name}" = "{opts["meaning"][0]}"')
        out(f"Добавлено сокращение {name}")
        return 0
    if kind in ("calc", "расчёт"):
        if not name or not re.fullmatch(r"[a-z0-9_\-]+", name):
            err('lde new calc <ключ латиницей> --title "Название"   (ключ: a-z, 0-9, _)')
            return 2
        d = root / "calc" / name
        if d.exists():
            err(f"Расчёт {name} уже есть: {d}")
            return 1
        d.mkdir(parents=True)
        title = opts.get("title", [name])[0]
        (d / "calc.toml").write_text(f'''title = "{title}"
script = "script.py"
timeout = 600
inputs = []                   # файлы проекта, от которых зависит расчёт: ["data/raw.csv"]
outputs = ["fig.png", "values.json"]

[params]                      # параметры: правятся здесь или в Studio
# PRF = {{ value = 1800.0, unit = "Гц", title = "Частота повторения импульсов" }}
x = {{ value = 1.0, unit = "", title = "Пример параметра" }}

[values]                      # единицы/точность/подписи чисел из JSON-выходов
# alpha = {{ unit = "", digits = 3, title = "Коэффициент" }}
''', encoding="utf-8")
        (d / "script.py").write_text('''"""Расчёт «%s». Параметры — lde_calc.params(), выходы — в lde_calc.out(…)."""
import lde_calc

p = lde_calc.params()

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(7, 4))
ax.plot([0, 1], [0, p["x"]], color="black")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(lde_calc.out("fig.png"), dpi=200)

lde_calc.save_values(result=p["x"] * 2)
''' % title, encoding="utf-8")
        out(f"Создан расчёт calc/{name}/ (calc.toml + script.py). Запуск: lde update {name}")
        out(f"Рисунок в тексте: ![Подпись](calc/{name}/out/fig.png){{#fig:{name}}}   Число: {{={name}.result}}")
        return 0
    if kind in ("document", "документ"):
        from lde.cli_more import new_document
        r = new_document(root, name or "", opts.get("title", [""])[0])
        if not r.get("ok"):
            err(r["error"])
            return 1
        out(f"Создан документ «{r['key']}»: {r['dir']}/ (document.toml + 01-текст.md). "
            f"Величины, расчёты, источники, сокращения — общие с остальными документами проекта.")
        out(f"Работа с ним: lde -D {r['key']} check | build | docx | submit;  в Studio — меню проекта.")
        return 0
    if kind in ("source", "источник"):
        if not name:
            err('lde new source <ключ> --author "Фамилия, И. О. and …" --title "…" --year 2020 '
                '[--publisher … --address … --type book|article|online --file путь.pdf]')
            return 2
        if name in proj.bib:
            err(f"Источник {name} уже есть (refs.bib:{proj.bib[name].line})")
            return 1
        typ = opts.pop("type", ["book"])[0]
        fpath = opts.pop("file", [None])[0]
        fields = {k: v[0] for k, v in opts.items()}
        if fpath:
            src = Path(fpath).expanduser()
            if not src.exists():
                err(f"Нет файла {src}")
                return 1
            dd = root / "sources" / name
            dd.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dd / src.name)
            fields["file"] = f"sources/{name}/{src.name}"
        body = ",\n".join(f"  {k:<9} = {{{v}}}" for k, v in fields.items())
        _append(root / "refs.bib", f"@{typ}{{{name},\n{body},\n}}")
        out(f"Добавлен источник {name}. Цитата в тексте: [@{name}] или [@{name}, с. 45]")
        return 0
    snippets = {
        "figure": "![Подпись рисунка без точки](calc/<расчёт>/out/<файл>.png){#fig:<имя> width=0.8}",
        "table": ": Заголовок таблицы {#tab:<имя>}\n| Параметр | Обозначение | Значение |\n|---|---|---|\n| Скорость | {V} | {=<расчёт>.V} |",
        "formula": "$$ D(f_t) = \\frac{1}{\\sqrt{1 - (c f_t / 2 V f_0)^2}} $$ {#eq:<имя>}\n\nгде {c} – скорость света, м/с;",
        "section": "# Название раздела {#sec:<имя>}\n\n## Подраздел {#sec:<имя2>}\n\nТекст абзаца.",
    }
    if kind in snippets:
        out(snippets[kind])
        return 0
    err("lde new quantity|abbr|calc|source|document|figure|table|formula|section …")
    return 2


def _kv(items: list[str]) -> dict:
    d: dict = {"_": []}
    i = 0
    while i < len(items):
        x = items[i]
        if x.startswith("--"):
            k = x[2:]
            v = items[i + 1] if i + 1 < len(items) else ""
            d.setdefault(k, []).append(v)
            i += 2
        else:
            d["_"].append(x)
            i += 1
    return d


# --------------------------------------------------------------- service ----
def cmd_doctor(args):
    cmd = ["bash", str(ROOT / "toolchain" / "doctor.sh")] + (["--fix"] if args.fix else [])
    return subprocess.call(cmd)


def cmd_skill_status(args):
    return subprocess.call(["bash", str(ROOT / "toolchain" / "install-skill.sh"), "--check"])




def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        from lde import app
        if app.bare_launch_ok():          # человек в терминале с экраном: `lde` — как обычное приложение
            import argparse as _a
            return app.cmd_open(_a.Namespace(dir=None))
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(HELP)
        return 0
    ap = argparse.ArgumentParser(prog="lde", add_help=True)
    ap.add_argument("-C", "--project", help="каталог проекта")
    ap.add_argument("-D", "--doc", help="документ проекта (ключ; по умолчанию основной)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("init"); s.add_argument("dir"); s.add_argument("--title"); s.add_argument("--force", action="store_true"); s.add_argument("--no-build", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("check"); s.add_argument("--json", action="store_true"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("build"); s.set_defaults(fn=cmd_build)
    s = sub.add_parser("update"); s.add_argument("calcs", nargs="*"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("save"); s.add_argument("-m", "--message", default=""); s.set_defaults(fn=cmd_save)
    s = sub.add_parser("undo"); s.set_defaults(fn=cmd_undo)
    s = sub.add_parser("redo"); s.set_defaults(fn=cmd_redo)
    s = sub.add_parser("version"); s.add_argument("name", nargs="?"); s.add_argument("-m", "--message"); s.set_defaults(fn=cmd_version)
    s = sub.add_parser("versions"); s.set_defaults(fn=cmd_versions)
    s = sub.add_parser("history"); s.add_argument("-n", type=int, default=100); s.set_defaults(fn=cmd_history)
    s = sub.add_parser("diff"); s.add_argument("base"); s.add_argument("target", nargs="?"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("feedback"); s.add_argument("action", nargs="?", default="list"); s.add_argument("rest", nargs="*")
    for o in ("kind", "title", "asked", "did", "manual", "want", "severity", "agent", "file", "hours"):
        s.add_argument("--" + o)
    s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_feedback)
    s = sub.add_parser("merge"); s.add_argument("--resolve", nargs=2, metavar=("UID", "КАК")); s.set_defaults(fn=cmd_merge)
    s = sub.add_parser("merge-driver"); s.add_argument("base"); s.add_argument("ours"); s.add_argument("theirs"); s.add_argument("path", nargs="?"); s.set_defaults(fn=cmd_merge_driver)
    s = sub.add_parser("restore"); s.add_argument("ref"); s.add_argument("--only", nargs="*"); s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("list"); s.add_argument("what"); s.add_argument("name", nargs="?"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("info"); s.add_argument("what"); s.set_defaults(fn=cmd_info)
    s = sub.add_parser("new"); s.add_argument("kind"); s.add_argument("rest", nargs=argparse.REMAINDER); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("doctor"); s.add_argument("--fix", action="store_true"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("skill-status"); s.set_defaults(fn=cmd_skill_status)
    _register_more(sub)

    # -C можно писать после команды
    for flag in ("-C", "--project", "-D", "--doc"):
        if flag in argv[1:]:
            i = argv.index(flag, 1)
            argv = [flag, argv[i + 1]] + argv[:i] + argv[i + 2:]
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        print(HELP)
        return 2
    t0 = time.time()
    rc = 1
    try:
        rc = args.fn(args) or 0
        return rc
    except KeyboardInterrupt:
        rc = 130
        return 130
    finally:
        # журнал команд проекта — по нему lde feedback analyze находит ручную рутину агентов
        if args.cmd not in ("init", "merge-driver", "studio", "version", "doctor", "skill-status", "import-docx", "clone", "mcp", "open", "app"):
            try:
                from lde.feedback import log_command
                from lde.project import find_project
                root = find_project(getattr(args, "project", None))
                if root:
                    log_command(root, argv, rc, time.time() - t0)
            except Exception:  # noqa: BLE001 — журнал не должен мешать команде
                pass


def _register_more(sub):
    """Команды из других модулей (импорт, DOCX, Studio) — подключаются, если модуль есть."""
    try:
        from lde import cli_more
        cli_more.register(sub)
    except ImportError as e:
        if "cli_more" not in str(e):
            raise
