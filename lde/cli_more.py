"""Команды импорта, экспорта и Studio (подключаются к CLI из lde/cli.py)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from lde.cli import _build, _update, err, out


def cmd_import_docx(args):
    from lde import gitops
    from lde.importers.docx_import import import_docx
    from lde.project import Project
    src = Path(args.file).expanduser().resolve()
    dst = Path(args.dir).expanduser().resolve()
    if not src.exists():
        err(f"Нет файла {src}")
        return 2
    if dst.exists() and any(dst.iterdir()) and not args.force:
        err(f"Каталог {dst} не пуст. Выберите другой или добавьте --force.")
        return 2
    t0 = time.time()
    rep = import_docx(src, dst, profile=args.profile)
    gitops.ensure_repo(dst)
    out(f"Импортировано за {time.time() - t0:.1f} с: {dst}")
    for k, v in rep.counts.items():
        out(f"  {k}: {v}")
    out(f"  ссылки → @метки: {len(rep.refs)}; приблизительно: {len(rep.approx)}; не перенесено: {len(rep.lost)}")
    out(f"  подробно: {dst / 'import' / 'report.md'}")
    if not args.no_build:
        proj = Project(dst)
        _build(proj)
    gitops.commit(dst, f"Импорт из {src.name}")
    out(f"Дальше: lde check -C {dst}   или   lde studio {dst}")
    return 0


def cmd_import_rde(args):
    from lde import gitops
    from lde.importers.rde_import import import_rde
    from lde.project import Project
    src = Path(args.rde_project).expanduser().resolve()
    dst = Path(args.dir).expanduser().resolve()
    if dst.exists() and any(dst.iterdir()) and not args.force:
        err(f"Каталог {dst} не пуст. Выберите другой или добавьте --force.")
        return 2
    t0 = time.time()
    keys = dict(x.split("=", 1) for x in (args.calc_key or []))
    rep = import_rde(src, dst, original=args.original, run_calcs=not args.no_calc, calc_keys=keys)
    gitops.ensure_repo(dst)
    out(f"Миграция RDE за {time.time() - t0:.1f} с: {dst}")
    for k, v in rep.counts.items():
        out(f"  {k}: {v}")
    out(f"  ссылки → @метки: {len(rep.refs)}; приблизительно: {len(rep.approx)}; не перенесено: {len(rep.lost)}")
    out(f"  подробно: {dst / 'import' / 'report.md'}")
    proj = Project(dst)
    if not args.no_build:
        _build(proj)
    gitops.commit(dst, f"Миграция из RDE ({src.name})")
    return 0


def cmd_fix(args):
    from lde.cli import load
    from lde.fixer import fix_all
    proj = load(args)
    rules = args.rules or None
    res = fix_all(proj.root, rules, dry=args.dry_run, doc=proj.doc_key)
    if not res:
        out("Исправлять нечего.")
        return 0
    for i in res:
        fx = i.fix
        out(f"{i.file}:{i.line}: «{fx['old']}» → «{fx['new']}»")
    out(("Будет исправлено: " if args.dry_run else "Исправлено: ") + str(len(res)))
    if not args.dry_run:
        from lde import gitops
        gitops.commit(proj.root, f"lde fix: исправлено {len(res)} мест по замечаниям линтера")
        out("Точка сохранения создана; отменить: lde undo")
    return 0


def cmd_mcp(args):
    """lde mcp [папка] [--port N] [--stdio] [--new-key] — MCP-сервер LDE: агент подключается по ссылке."""
    from lde import mcp_server as ms
    from lde.project import find_project
    root = find_project(args.dir) if args.dir else find_project(getattr(args, "project", None))
    if args.dir and not root:
        err(f"В {args.dir} нет проекта LDE (lde.toml): агент сможет открыть или создать проект сам.")
    if args.stdio:
        ms.serve_stdio(str(root) if root else None, getattr(args, "doc", None))
        return 0
    if args.new_key:
        ms.rotate_token()
        out("Новый ключ создан: старые подключения агентов перестанут работать.")
    port = args.port or ms.saved_port()
    try:
        srv, app, token = ms.serve(str(root) if root else None, port, getattr(args, "doc", None))
    except OSError as e:
        err(f"Порт {port} занят ({e.strerror}). Другой порт: lde mcp --port N")
        return 1
    if args.port:
        ms.save_port(port)
    sn = ms.client_snippets(port, token)
    out(f"LDE MCP: {sn['url']}" + (f" — проект {root}" if root else " — проект не открыт (агент откроет или создаст сам)"))
    out("")
    out("Ссылка для агента (ключ в ней — не публикуйте):")
    out(f"  {sn['link']}")
    out("")
    out("Claude Code:  " + sn["claude"])
    out("DSH (файл для dsh --patch):")
    for line in sn["dsh"].splitlines():
        out("  " + line)
    out("")
    out("Остановить: Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_studio(args):
    import threading
    from lde import app
    from lde.project import find_project
    from lde.studio.server import serve
    root = find_project(args.dir) if args.dir else find_project(None)
    if args.dir and not root:
        err(f"В {args.dir} нет проекта LDE (lde.toml). Studio откроется со стартовым экраном.")
    if not args.no_browser and not args.port and app.running():
        return app.cmd_open(args)             # одна Studio на пользователя — новое окно той же
    httpd, st = serve(root, args.port)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    out(f"LDE Studio: {url}" + (f" — {root}" if root else ""))
    if args.no_browser:
        out("Окно не открываю (--no-browser). Остановить: Ctrl+C")
        try:
            th.join()
        except KeyboardInterrupt:
            pass
        return 0
    app.register(port)
    try:
        proc = app.open_window(url)
        if proc is None:
            out(f"Нет ни Chrome, ни xdg-open — откройте в браузере: {url}")
        app.wait_windows(proc, st)
    finally:
        app.unregister()
        st.stop = True
        httpd.shutdown()
    return 0


def cmd_docx(args):
    from lde.cli import load
    from lde.docx_export import export_docx
    proj = load(args)
    r = export_docx(proj, log=out, qa=not args.no_qa)
    if not r.get("ok"):
        err("DOCX не собран: " + r.get("error", ""))
        return 1
    out(r["summary"] + f" ({r['seconds']} с)")
    q = r.get("qa") or {}
    for i in q.get("issues", [])[:20]:
        out(f"  {'ошибка' if i['severity'] == 'error' else 'замечание'} ({i['source']}): {i['message']}")
    return 1 if q.get("errors") else 0


def cmd_submit(args):
    from lde.cli import _color, load
    from lde.submit import submit
    proj = load(args)
    r = submit(proj.root, log=out, doc=proj.doc_key)
    if r["ok"]:
        out(_color("Можно сдавать.", "32"))
        out(f"  PDF:  {r['pdf']}")
        out(f"  DOCX: {r['docx']} (текст совпадает с PDF на {r['docx_coverage']})")
    else:
        out(_color("Пока нельзя сдавать — вот что мешает:", "31"))
        for b in r["blockers"]:
            out(f"  • {b}")
    if r["notes"]:
        out(f"Замечаний, не мешающих сдаче: {len(r['notes'])} (out/submit.md)")
    return 0 if r["ok"] else 1


def cmd_review(args):
    from lde import gitops, review
    from lde.cli import load
    proj = load(args)
    a = args.args
    if a and a[0] == "import":
        if len(a) < 2:
            err("lde review import <файл.docx с примечаниями>")
            return 2
        items = review.import_review(proj.root, Path(a[1]).expanduser(), doc=proj.doc_key)
        gitops.commit(proj.root, f"Примечания рецензента из {Path(a[1]).name}", paths=["review"])
        out(f"Примечаний: {len(items)} (новых и прежних). Список: lde review")
        return 0
    if a and a[0] == "done":
        review.set_done(proj.root, int(a[1]))
        gitops.commit(proj.root, f"Примечание {a[1]} выполнено", paths=["review"])
        out(f"Отмечено выполненным: {a[1]}")
        return 0
    items = review.load(proj.root)
    if not items:
        out("Примечаний нет. Импорт: lde review import <файл.docx>")
        return 0
    for i in items:
        mark = "✓" if i["done"] else "•"
        where = f"{i['file']}:{i['line']} ({i['title']})" if i.get("file") else "место не найдено"
        quote = f" к «{i['anchor'][:60]}»" if i.get("anchor") else ""
        out(f"{mark} {i['n']}. {where}{quote}: {i['text']} — {i['author']}")
    return 0


def register(sub):
    import argparse as _ap
    s = sub.add_parser("server")
    s.add_argument("rest", nargs=_ap.REMAINDER)
    s.set_defaults(fn=cmd_server)
    s = sub.add_parser("share")
    s.add_argument("rest", nargs=_ap.REMAINDER)
    s.set_defaults(fn=cmd_share)
    for name in ("publish", "push", "pull", "sync", "clone", "remote"):
        s = sub.add_parser(name)
        s.add_argument("rest", nargs=_ap.REMAINDER)
        s.set_defaults(fn=cmd_remote, cmd=name)
    s = sub.add_parser("review")
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("submit")
    s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("docx")
    s.add_argument("--no-qa", action="store_true")
    s.set_defaults(fn=cmd_docx)
    s = sub.add_parser("studio")
    s.add_argument("dir", nargs="?")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_studio)
    s = sub.add_parser("open")
    s.add_argument("dir", nargs="?")
    s.set_defaults(fn=lambda a: __import__("lde.app", fromlist=["x"]).cmd_open(a))
    s = sub.add_parser("app")
    s.add_argument("action", nargs="?", default="status")
    s.set_defaults(fn=lambda a: __import__("lde.app", fromlist=["x"]).cmd_app(a))
    s = sub.add_parser("mcp")
    s.add_argument("dir", nargs="?")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--stdio", action="store_true")
    s.add_argument("--new-key", action="store_true")
    s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("fix")
    s.add_argument("rules", nargs="*", help="правила (qty-as-text, qty-as-math, number-typed, ref-as-text)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_fix)
    s = sub.add_parser("import-docx")
    s.add_argument("file")
    s.add_argument("dir")
    s.add_argument("--profile")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-build", action="store_true")
    s.set_defaults(fn=cmd_import_docx)
    s = sub.add_parser("import-rde")
    s.add_argument("rde_project")
    s.add_argument("dir")
    s.add_argument("--original", help="исходный DOCX, из которого делался проект RDE (для восстановления нумерации)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-build", action="store_true")
    s.add_argument("--no-calc", action="store_true")
    s.add_argument("--calc-key", action="append", help="ключ расчёта для артефакта RDE: <id>=<ключ>")
    s.set_defaults(fn=cmd_import_rde)


def new_document(root: Path, key: str, title: str = "") -> dict:
    """Ещё один документ проекта: documents/<ключ>/document.toml + первый файл текста.
    Общая база (величины, расчёты, источники, сокращения, рисунки) — та же, что у основного."""
    import re as _re
    from lde import gitops
    from lde.project import list_documents
    key = (key or "").strip()
    if not key and title:
        key = _translit(title)
    if not _re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,40}", key or "") or key == "main":
        return {"ok": False, "error": "ключ документа — латиницей: a-z, 0-9, _ и - (например, pz, tz, prez)"}
    if key in [d["key"] for d in list_documents(root)]:
        return {"ok": False, "error": f"документ «{key}» уже есть"}
    d = root / "documents" / key
    d.mkdir(parents=True, exist_ok=True)
    t = (title or key).replace('"', "'")
    (d / "document.toml").write_text(f"""# Документ проекта. Не указанное здесь берётся из lde.toml проекта (оформление, титул).
[document]
name = "{key}"             # out/{key}.pdf, out/{key}.docx

[title]
topic = "{t}"
""", encoding="utf-8")
    (d / "01-текст.md").write_text(f"# {t} {{-}}\n\nТекст документа. Величины, расчёты и источники — общие для всего проекта.\n",
                                    encoding="utf-8")
    gitops.ensure_repo(root)
    gitops.commit(root, f"Новый документ «{t}» ({key})", paths=[f"documents/{key}"])
    return {"ok": True, "key": key, "dir": f"documents/{key}"}


def _translit(s: str) -> str:
    tab = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                   ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                    "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    import re as _re
    out = "".join(tab.get(c, c) for c in s.lower())
    return _re.sub(r"[^a-z0-9]+", "-", out).strip("-")[:30]


# ------------------------------------------------------- сервер и ссылки ----
def cmd_server(args):
    import getpass
    import os as _os
    from lde import server_client as sc
    a = args.rest
    sub = a[0] if a else "status"
    try:
        if sub == "login":
            if len(a) < 3:
                err("lde server login <адрес сервера> <имя>   (пароль спросит; LDE_PASSWORD — для скриптов)")
                return 2
            pw = _os.environ.get("LDE_PASSWORD") or getpass.getpass("Пароль: ")
            r = sc.login(a[1], a[2], pw)
            out(f"Вход выполнен: {r['username']} на {r['url']}" + (" (администратор)" if r["is_admin"] else ""))
            return 0
        if sub == "logout":
            sc.logout(a[1] if len(a) > 1 else None)
            out("Ключ устройства отозван и удалён.")
            return 0
        if sub == "invite":
            srv = sc.Server()
            d = srv.json("POST", "/api/invites", {"admin": "--admin" in a, "note": " ".join(x for x in a[1:] if x != "--admin")})
            out(d["link"] or f"{srv.url}/join#{d['invite']}")
            return 0
        st = sc.status()
        if not st["connected"]:
            out("Сервер LDE не подключён. Подключить: lde server login <адрес> <имя>")
            return 1
        out(f"{st['url']} — {st['username']}" + (" (администратор)" if st.get("is_admin") else "")
            + ("" if st.get("ok") else f" — ошибка: {st.get('error')}"))
        return 0 if st.get("ok") else 1
    except sc.ServerError as e:
        err(str(e))
        return 1


def cmd_share(args):
    from lde import share
    from lde.cli import load
    from lde.server_client import ServerError
    proj = load(args)
    a = args.rest
    sub = a[0] if a else "list"
    opts = {}
    pos = []
    i = 1
    while i < len(a):
        if a[i].startswith("--"):
            if i + 1 < len(a) and not a[i + 1].startswith("--") and a[i] not in ("--comment-only", "--no-pdf", "--all", "--done", "--rejected"):
                opts[a[i][2:]] = a[i + 1]
                i += 2
                continue
            opts[a[i][2:]] = True
        else:
            pos.append(a[i])
        i += 1
    try:
        if sub == "new":
            r = share.create(proj.root, proj.doc_key, opts.get("to", ""), "comment" if opts.get("comment-only") else "edit",
                             float(opts.get("days", 30)), opts.get("pin", ""), not opts.get("no-pdf"))
            out(f"Ссылка для «{r['label']}» ({'только замечания' if r['perms'] == 'comment' else 'правка и замечания'}):")
            out(r["link"])
            out(f"Версия зафиксирована: «{r['version_name']}». Передайте ссылку целиком (часть после # — ключ).")
            return 0
        if sub == "list":
            rows = share.list_shares(proj.root, doc=proj.doc_key)
            if not rows:
                out("Ссылок нет. Создать: lde share new --to \"Иванов И. И.\"")
            for r in rows:
                state = "отозвана" if r["revoked"] else "истекла" if r["expires"] and r["expires"] < time.time() else "действует"
                copy = f"правки сохранены {time.strftime('%d.%m %H:%M', time.localtime(r['copy_updated']))}" if r["copy_ver"] else "правок нет"
                out(f"{r['id']}\t{r['label']}\t{state}\tоткрывали: {r['opens']}\t{copy}")
            return 0
        if sub == "link":
            r = next((x for x in share.list_shares(proj.root) if x["id"] == pos[0]), None)
            out(r["link"] if r else "нет такой ссылки")
            return 0 if r else 1
        if sub == "pull":
            rv = share.pull(proj.root, pos[0])
            out(f"Копия «{rv['name']}» ({rv['version_name']}): правок {len(rv['items'])}, замечаний {len(rv['comments'])}"
                + (f"; служебная ветка {rv['branch']}" if rv["branch"] else ""))
            st = {"clean": "применится как есть", "same": "уже так", "merge": "сольётся с вашими правками",
                  "conflict": "КОНФЛИКТ с вашими правками", "deleted": "фрагмента у вас больше нет"}
            for it in rv["items"]:
                dec = {"accepted": " [принято]", "rejected": " [отклонено]", "partial": " [принято с правкой]"}.get(it["decision"] or "", "")
                out(f"  {it['block']}\t{it['where']}\t{st[it['status']]}{dec}")
                for op, t in it["words"]:
                    if op != "=":
                        out(f"      {'+' if op == '+' else '−'} {t.strip()[:160]}")
            for c in rv["comments"]:
                out(f"  [{c['id']}] {c.get('where') or ''}: «{c.get('quote') or ''}» — {c['text']}" + (f" [{c['status']}]" if c.get("status") else ""))
            for k in rv.get("rejected_unsafe", []):
                out(f"  {k}: правка отклонена автоматически — меняла вид блока (небезопасный ввод)")
            out(f"Принять: lde share accept {pos[0]} <блок>|--all · отклонить: lde share reject {pos[0]} <блок>")
            return 0
        if sub in ("accept", "reject"):
            sid = pos[0]
            blocks = [it["block"] for it in share.pull(proj.root, sid)["items"]
                      if not it["decision"] and (sub == "reject" or it["status"] != "conflict")] if opts.get("all") else pos[1:]
            for b in blocks:
                r = share.decide(proj.root, sid, b, sub)
                if r.get("conflict"):
                    out(f"{b}: конфликт — решите в Studio или: lde share own {sid} {b} \"свой текст\"")
                else:
                    out(f"{b}: {'принято' if sub == 'accept' else 'отклонено'}")
            return 0
        if sub == "own":
            share.decide(proj.root, pos[0], pos[1], "own", pos[2])
            out("Записан ваш вариант.")
            return 0
        if sub == "reply":
            status = "done" if opts.get("done") else "rejected" if opts.get("rejected") else None
            share.reply(proj.root, pos[0], pos[1], " ".join(pos[2:]), status)
            out("Ответ отправлен — рецензент увидит его по своей ссылке.")
            return 0
        if sub in ("revoke", "restore"):
            share.revoke(pos[0], sub == "revoke")
            out("Ссылка отозвана." if sub == "revoke" else "Ссылка снова действует.")
            return 0
    except (ServerError, ValueError) as e:
        err(str(e))
        return 1
    err("lde share new [--to «кому»] [--comment-only] [--days N] [--pin 1234] [--no-pdf] | list | link <id> | "
        "pull <id> | accept <id> <блок>|--all | reject <id> <блок> | own <id> <блок> \"текст\" | reply <id> <замечание> \"текст\" [--done|--rejected] | revoke <id>")
    return 2


def cmd_remote(args):
    """lde publish | push | pull | sync | clone <id> <папка> | remote [list|info|member <имя> <роль>]"""
    from lde import sync
    from lde.server_client import ServerError
    a = getattr(args, "rest", []) or []
    cmd = args.cmd
    try:
        if cmd == "clone":
            if len(a) < 2:
                err("lde clone <id проекта> <папка>   (id — lde remote list)")
                return 2
            r = sync.clone(a[0], Path(a[1]))
            out(f"Проект получен в {r['dir']}" + (f" (пакетов: {r['packs']})" if r.get("packs") else ""))
            return 0
        if cmd == "remote" and (not a or a[0] == "list"):
            for p in sync.list_projects():
                out(f"{p['id']}\t{p['title']}\t{p['role']}\tвладелец: {p['owner']}\t{time.strftime('%d.%m.%Y %H:%M', time.localtime(p['updated']))}")
            return 0
        from lde.cli import load
        proj = load(args)
        if cmd == "publish":
            r = sync.publish(proj.root, a[0] if a else None)
            out(f"Проект опубликован на сервере (id {r['id']}), история отправлена зашифрованной.")
            return 0
        if cmd == "push":
            r = sync.push(proj.root)
            out("Отправлено." if r["pushed"] else r.get("note", "Нечего отправлять."))
            return 0
        if cmd == "pull":
            r = sync.pull(proj.root)
            out(_pull_msg(r))
            return 1 if r.get("conflicts") else 0
        if cmd == "sync":
            r = sync.sync(proj.root)
            out(_pull_msg(r["pull"]))
            out("Отправлено." if r["push"].get("pushed") else r["push"].get("note", "Отправлять нечего."))
            return 1 if r["pull"].get("conflicts") else 0
        if cmd == "remote" and a[0] == "info":
            i = sync.remote_info(proj.root)
            out(f"{i['url']} · проект {i['project']}" if i else "Проект не опубликован (lde publish).")
            return 0
        if cmd == "remote" and a[0] == "member":
            sync.add_member(proj.root, a[1], a[2] if len(a) > 2 else "reader")
            out(f"Доступ выдан: {a[1]} ({a[2] if len(a) > 2 else 'reader'})")
            return 0
    except ServerError as e:
        err(str(e))
        return 1
    err("lde publish | push | pull | sync | clone <id> <папка> | remote list|info|member <имя> owner|editor|reviewer|reader")
    return 2


def _pull_msg(r: dict) -> str:
    if r.get("conflicts"):
        return (f"Получено и слито; конфликтов в тексте: {r['conflicts']} — решите в Studio (красные пометки) "
                f"или lde merge.")
    if r.get("kept_ours"):
        return f"Получено и слито; в файлах {', '.join(r['kept_ours'])} оставлен ваш вариант (серверный — в истории)."
    if r.get("merged"):
        return "Получено: " + ("ваша копия догнала сервер." if r.get("fast_forward") else "слито с вашими правками.")
    return r.get("note") or "Новых изменений нет."
