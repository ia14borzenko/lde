"""Администрирование сервера LDE (внутри контейнера):

    lde-server invite [--admin] [--note "…"]   одноразовая ссылка-приглашение (7 дней)
    lde-server users                          пользователи
    lde-server disable <имя> | enable <имя>   запретить/разрешить вход
    lde-server delete-user <имя>              удалить пользователя, его ссылки и устройства
    lde-server backup <файл.tar.gz>           резервная копия данных (база + блобы)
    lde-server gc                             удалить блобы, на которые ничто не ссылается (старше часа)
    lde-server serve                          запустить сервер (по умолчанию в контейнере)
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

from app.db import DB, h

DATA = Path(os.environ.get("LDE_DATA", "/data"))
URL = os.environ.get("LDE_PUBLIC_URL", "").rstrip("/")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        from app.main import main as serve
        return serve(argv[1:])
    db = DB(DATA)
    if cmd == "invite":
        tok = secrets.token_urlsafe(32)
        note = argv[argv.index("--note") + 1] if "--note" in argv else ""
        db.x("INSERT INTO invites (token_hash, is_admin, created, expires, note) VALUES (?,?,?,?,?)",
             h(tok), 1 if "--admin" in argv else 0, time.time(), time.time() + 7 * 86400, note)
        print(f"{URL}/join#{tok}")
        print("Одноразовая ссылка, действует 7 дней. Пароль задаётся в браузере и на сервер не передаётся.")
        return 0
    if cmd == "users":
        for r in db.q("SELECT username, is_admin, disabled, created FROM users ORDER BY id"):
            print(f"{r['username']}\t{'admin' if r['is_admin'] else ''}\t{'отключён' if r['disabled'] else ''}\t"
                  f"{time.strftime('%d.%m.%Y', time.localtime(r['created']))}")
        return 0
    if cmd in ("disable", "enable"):
        n = db.x("UPDATE users SET disabled = ? WHERE username = ?", 1 if cmd == "disable" else 0, argv[1].lower())
        if cmd == "disable":
            db.x("UPDATE devices SET revoked = 1 WHERE user_id = (SELECT id FROM users WHERE username = ?)", argv[1].lower())
        print("готово" if n else "нет такого пользователя")
        return 0 if n else 1
    if cmd == "delete-user":
        u = db.one("SELECT id FROM users WHERE username = ?", argv[1].lower())
        if not u:
            print("нет такого пользователя")
            return 1
        with db.tx() as c:
            for t in ("share_sessions WHERE share_id IN (SELECT id FROM shares WHERE owner_id = ?)",
                      "shares WHERE owner_id = ?", "devices WHERE user_id = ?", "members WHERE user_id = ?"):
                c.execute("DELETE FROM " + t, (u["id"],))
            c.execute("UPDATE projects SET deleted = 1 WHERE owner_id = ?", (u["id"],))
            c.execute("DELETE FROM users WHERE id = ?", (u["id"],))
        print("удалён (блобы без ссылок остаются до очистки)")
        return 0
    if cmd == "gc":
        used = set()
        for sql in ("SELECT snapshot, copy, owner_doc FROM shares", "SELECT blob FROM packs"):
            for r in db.q(sql):
                used.update(x for x in r if x)
        n = 0
        for p in (DATA / "blobs").rglob("*"):
            if p.is_file() and p.name not in used and time.time() - p.stat().st_mtime > 3600:
                p.unlink()
                n += 1
        print(f"удалено блобов без ссылок: {n}")
        return 0
    if cmd == "backup":
        out = Path(argv[1])
        snap = DATA / "lde.sqlite.backup"
        src = sqlite3.connect(DATA / "lde.sqlite")
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        with tarfile.open(out, "w:gz") as tf:
            tf.add(snap, "lde.sqlite")
            tf.add(DATA / "secret", "secret")
            tf.add(DATA / "blobs", "blobs")
        snap.unlink()
        print(f"резервная копия: {out}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
