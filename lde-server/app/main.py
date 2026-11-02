"""Сервер LDE: аккаунты по приглашению, ссылки на документ для рецензента, хранение проектов.

Стандартная библиотека Python, без сторонних зависимостей. Всё содержимое (снимки документов,
правки рецензентов, пакеты git проектов, названия) шифруется на устройствах пользователей; сервер
хранит байты, проверяет права и не может прочитать текст.

    python -m app.main --data /data --port 8790
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import traceback
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.db import DB, h

WEB = Path(__file__).resolve().parent.parent / "web"
# MathLive и прочие сторонние файлы панели: в контейнере — /app/vendor, при разработке — из LDE
VENDOR = Path(os.environ.get("LDE_VENDOR") or (
    Path(__file__).resolve().parents[2] / "lde" / "studio" / "static" / "vendor"))
# Интерфейс LDE Studio — рецензент работает в той же Studio (роль «рецензент», данные — в браузере)
STUDIO = Path(os.environ.get("LDE_STUDIO") or (Path(__file__).resolve().parents[2] / "lde" / "studio" / "static"))
VERSION = "0.2.0"
LIMITS = {"json": 8 << 20, "snapshot": 64 << 20, "copy": 8 << 20, "owner": 8 << 20, "pack": 256 << 20}
ROLES = ("owner", "editor", "reviewer", "reader")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
       "font-src 'self' data:; connect-src 'self'; frame-src blob:; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")


def b64e(b: bytes | None) -> str | None:
    return base64.b64encode(b).decode() if b is not None else None


def b64d(s: str | None, limit: int = 1 << 20) -> bytes | None:
    if s is None:
        return None
    if len(s) > limit * 4 // 3 + 8:
        raise ApiError(413, "слишком большое значение")
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        raise ApiError(400, "неверная кодировка base64")


def scrypt(secret: bytes, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(secret, salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 << 20, dklen=32)


class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class RateLimit:
    """Скользящее окно по ключу (IP + действие)."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, n: int, per: float):
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - per:
                q.popleft()
            if len(q) >= n:
                raise ApiError(429, "слишком много попыток — подождите")
            q.append(now)


class App:
    def __init__(self, data: Path, public_url: str = "", trust_proxy: bool = False):
        self.db = DB(data)
        self.public_url = public_url.rstrip("/")
        self.trust_proxy = trust_proxy
        self.rl = RateLimit()
        sp = Path(data) / "secret"
        if not sp.exists():
            sp.write_bytes(secrets.token_bytes(32))
            os.chmod(sp, 0o600)
        self.secret = sp.read_bytes()

    def fake_salt(self, username: str) -> str:
        """Соль для несуществующего имени — постоянная, чтобы по ответу нельзя было узнать, есть ли пользователь."""
        return b64e(hmac.new(self.secret, b"salt:" + username.lower().encode(), "sha256").digest()[:16])


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_id() -> str:
    return secrets.token_urlsafe(9)


# =================================================================== HTTP ====
class Handler(BaseHTTPRequestHandler):
    server_version = "lde-server"
    sys_version = ""
    app: App = None

    # ------------------------------------------------------------ helpers ----
    def log_message(self, fmt, *args):     # без путей (в них бывают токены ссылок)
        pass

    @property
    def ip(self) -> str:
        # за обратным прокси (порт сервера доступен только ему): клиент — последний адрес в X-Forwarded-For
        if self.app.trust_proxy:
            xf = self.headers.get("X-Forwarded-For", "")
            if xf:
                return xf.split(",")[-1].strip()
        return self.client_address[0]

    def _common_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, cache: str = "no-store"):
        self.send_response(code)
        self._common_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            if isinstance(v, list):
                for x in v:
                    self.send_header(k, x)
            else:
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200, extra=None):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra)

    def redirect(self, loc: str, cookies: list[str] | None = None):
        self.send_response(302)
        self._common_headers()
        self.send_header("Location", loc)
        self.send_header("Cache-Control", "no-store")
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def body(self, limit: int) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, f"слишком большой запрос (предел {limit >> 20} МБ)")
        return self.rfile.read(n) if n else b""

    def jbody(self) -> dict:
        raw = self.body(LIMITS["json"])
        try:
            d = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "неверный JSON")
        if not isinstance(d, dict):
            raise ApiError(400, "ожидается объект JSON")
        return d

    def cookies(self) -> dict:
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def check_csrf(self):
        """Запросы с cookie-сессией меняют данные только со своего сайта (заголовок + Origin)."""
        if self.headers.get("X-LDE-Request") != "1":
            raise ApiError(403, "запрос отклонён (CSRF)")
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
            raise ApiError(403, "запрос отклонён (чужой сайт)")

    def user(self, required=True):
        """Пользователь по ключу устройства (Authorization: Bearer) или cookie веб-сессии."""
        auth = self.headers.get("Authorization", "")
        tok, via_cookie = None, False
        if auth.startswith("Bearer "):
            tok = auth[7:].strip()
        elif self.cookies().get("lde_u"):
            tok, via_cookie = self.cookies()["lde_u"], True
        if not tok:
            if required:
                raise ApiError(401, "нужен вход")
            return None
        row = self.app.db.one("SELECT d.user_id, d.revoked, u.username, u.is_admin, u.disabled FROM devices d "
                              "JOIN users u ON u.id = d.user_id WHERE d.token_hash = ?", h(tok))
        if not row or row["revoked"] or row["disabled"]:
            if required:
                raise ApiError(401, "сеанс недействителен — войдите снова")
            return None
        if via_cookie and self.command not in ("GET", "HEAD"):
            self.check_csrf()
        self.app.db.x("UPDATE devices SET last_used = ? WHERE token_hash = ?", time.time(), h(tok))
        return {"id": row["user_id"], "username": row["username"], "is_admin": bool(row["is_admin"])}

    def static(self, rel: str):
        root = WEB
        if rel.startswith("static/vendor/"):
            root, rel = VENDOR, rel[len("static/vendor/"):]
        p = (root / rel).resolve()
        if root.resolve() not in p.parents or not p.is_file():
            raise ApiError(404, "нет такой страницы")
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send(200, p.read_bytes(), ctype, cache="public, max-age=86400" if root is VENDOR else "no-cache")

    # ------------------------------------------------------------ routing ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str):
        u = urlparse(self.path)
        path = u.path
        self.q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            for m, rx, fn in ROUTES:
                if m != method and not (m == "GET" and method == "HEAD"):
                    continue
                mt = re.fullmatch(rx, path)
                if mt:
                    return fn(self, *mt.groups())
            raise ApiError(404, "не найдено")
        except ApiError as e:
            if path.startswith(("/api/", "/r/")) and "/api/" in path:
                self.json({"error": str(e)}, e.code)
            else:
                self.send(e.code, page_error(str(e)).encode(), "text/html; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.json({"error": f"внутренняя ошибка: {type(e).__name__}"}, 500)


def page_error(msg: str) -> str:
    import html
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>LDE</title><link rel=stylesheet href='/static/app.css'><main class=card><h1>LDE</h1>"
            f"<p>{html.escape(msg)}</p></main>")


# ================================================================ handlers ====
def r_health(hd: Handler):
    hd.json({"ok": True, "version": VERSION})


def r_index(hd: Handler):
    hd.static("index.html")


def r_static(hd: Handler, rel: str):
    hd.static("static/" + rel)


def r_info(hd: Handler):
    hd.json({"name": "LDE server", "version": VERSION, "public_url": hd.app.public_url})


# --------------------------------------------------------------- аккаунты ----
def r_prelogin(hd: Handler):
    """Соль PBKDF2 для входа (на устройстве из пароля выводятся ключ входа и ключ шифрования)."""
    name = (hd.q.get("username") or "").strip().lower()
    hd.app.rl.check(f"pre:{hd.ip}", 60, 60)
    row = hd.app.db.one("SELECT kdf_salt FROM users WHERE username = ?", name)
    hd.json({"kdf_salt": row["kdf_salt"] if row else hd.app.fake_salt(name), "iterations": 600000})


def r_invite_info(hd: Handler):
    d = hd.app.db.one("SELECT is_admin, expires, used_by FROM invites WHERE token_hash = ?", h(hd.q.get("invite", "")))
    hd.app.rl.check(f"inv:{hd.ip}", 30, 60)
    if not d or d["used_by"] or (d["expires"] and d["expires"] < time.time()):
        raise ApiError(404, "приглашение недействительно или уже использовано")
    hd.json({"ok": True, "is_admin": bool(d["is_admin"])})


def r_register(hd: Handler):
    d = hd.jbody()
    hd.app.rl.check(f"reg:{hd.ip}", 10, 600)
    name = (d.get("username") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.\-]{1,31}", name):
        raise ApiError(400, "имя: 2–32 знака, латиница, цифры, _ . -")
    auth = b64d(d.get("auth"), 64)
    pub, priv = b64d(d.get("pubkey"), 64), b64d(d.get("enc_priv"), 256)
    kdf_salt = d.get("kdf_salt") or ""
    if not (auth and len(auth) == 32 and pub and len(pub) == 32 and priv and kdf_salt):
        raise ApiError(400, "неполные данные регистрации")
    salt = secrets.token_bytes(16)
    with hd.app.db.tx() as c:
        inv = c.execute("SELECT * FROM invites WHERE token_hash = ?", (h(d.get("invite", "")),)).fetchone()
        if not inv or inv["used_by"] or (inv["expires"] and inv["expires"] < time.time()):
            raise ApiError(403, "приглашение недействительно или уже использовано")
        if c.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
            raise ApiError(409, "такое имя уже занято")
        cur = c.execute("INSERT INTO users (username, auth_hash, auth_salt, kdf_salt, pubkey, enc_priv, is_admin, created) "
                        "VALUES (?,?,?,?,?,?,?,?)", (name, scrypt(auth, salt), salt, kdf_salt, pub, priv,
                                                     inv["is_admin"], time.time()))
        c.execute("UPDATE invites SET used_by = ? WHERE token_hash = ?", (cur.lastrowid, inv["token_hash"]))
    hd.app.db.audit(cur.lastrowid, "register", name, hd.ip)
    hd.json({"ok": True, "username": name})


def r_login(hd: Handler):
    d = hd.jbody()
    name = (d.get("username") or "").strip().lower()
    hd.app.rl.check(f"login:{hd.ip}", 10, 300)
    hd.app.rl.check(f"login-u:{name}", 20, 3600)
    row = hd.app.db.one("SELECT * FROM users WHERE username = ?", name)
    auth = b64d(d.get("auth"), 64) or b""
    good = row is not None and not row["disabled"] and hmac.compare_digest(scrypt(auth, row["auth_salt"]), row["auth_hash"])
    if not good:
        hd.app.db.audit(row["id"] if row else None, "login-failed", name, hd.ip)
        raise ApiError(401, "неверное имя или пароль")
    tok = new_token()
    kind = "web" if d.get("web") else "device"
    hd.app.db.x("INSERT INTO devices (token_hash, user_id, name, kind, created, last_used) VALUES (?,?,?,?,?,?)",
                h(tok), row["id"], (d.get("device") or kind)[:80], kind, time.time(), time.time())
    hd.app.db.audit(row["id"], "login", kind, hd.ip)
    res = {"ok": True, "user": {"username": row["username"], "is_admin": bool(row["is_admin"])},
           "pubkey": b64e(row["pubkey"]), "enc_priv": b64e(row["enc_priv"])}
    if kind == "web":
        hd.json(res, extra={"Set-Cookie": f"lde_u={tok}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=604800"})
    else:
        res["token"] = tok
        hd.json(res)


def r_logout(hd: Handler):
    tok = (hd.headers.get("Authorization", "")[7:].strip() or hd.cookies().get("lde_u", ""))
    u = hd.user()
    hd.app.db.x("UPDATE devices SET revoked = 1 WHERE token_hash = ?", h(tok))
    hd.app.db.audit(u["id"], "logout", "", hd.ip)
    hd.json({"ok": True}, extra={"Set-Cookie": "lde_u=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0"})


def r_me(hd: Handler):
    u = hd.user()
    row = hd.app.db.one("SELECT pubkey, enc_priv FROM users WHERE id = ?", u["id"])
    devs = [dict(name=r["name"], kind=r["kind"], created=r["created"], last_used=r["last_used"])
            for r in hd.app.db.q("SELECT * FROM devices WHERE user_id = ? AND revoked = 0 ORDER BY last_used DESC", u["id"])]
    hd.json({"user": u, "pubkey": b64e(row["pubkey"]), "enc_priv": b64e(row["enc_priv"]), "devices": devs})


def r_pubkey(hd: Handler, name: str):
    hd.user()
    row = hd.app.db.one("SELECT pubkey FROM users WHERE username = ? AND disabled = 0", name.lower())
    if not row:
        raise ApiError(404, "нет такого пользователя")
    hd.json({"username": name.lower(), "pubkey": b64e(row["pubkey"])})


def r_invite_create(hd: Handler):
    u = hd.user()
    if not u["is_admin"]:
        raise ApiError(403, "приглашать может администратор")
    d = hd.jbody()
    tok = new_token()
    hd.app.db.x("INSERT INTO invites (token_hash, is_admin, created, expires, created_by, note) VALUES (?,?,?,?,?,?)",
                h(tok), 1 if d.get("admin") else 0, time.time(), time.time() + 7 * 86400, u["id"], (d.get("note") or "")[:100])
    hd.app.db.audit(u["id"], "invite", d.get("note", ""), hd.ip)
    hd.json({"invite": tok, "link": f"{hd.app.public_url}/join#{tok}"})


# ------------------------------------------------------- ссылки: автор ----
def _share_of_owner(hd: Handler, sid: str):
    u = hd.user()
    s = hd.app.db.one("SELECT * FROM shares WHERE id = ?", sid)
    if not s or s["owner_id"] != u["id"]:
        raise ApiError(404, "нет такой ссылки")
    return u, s


def _share_row(s) -> dict:
    return {"id": s["id"], "perms": s["perms"], "created": s["created"], "expires": s["expires"],
            "revoked": bool(s["revoked"]), "pin": s["pin_hash"] is not None, "opened": s["opened"], "opens": s["opens"],
            "copy_ver": s["copy_ver"], "copy_updated": s["copy_updated"], "owner_ver": s["owner_ver"],
            "meta_enc": b64e(s["meta_enc"]), "key_wrap": b64e(s["key_wrap"]), "has_snapshot": bool(s["snapshot"])}


def r_share_create(hd: Handler):
    u = hd.user()
    d = hd.jbody()
    th = b64d(d.get("token_hash"), 64)
    if not th or len(th) != 32:
        raise ApiError(400, "нужен хеш токена ссылки")
    perms = d.get("perms", "edit")
    if perms not in ("edit", "comment"):
        raise ApiError(400, "права: edit | comment")
    days = float(d.get("days") or 30)
    pin = (d.get("pin") or "").strip()
    salt = secrets.token_bytes(16) if pin else None
    sid = new_id()
    hd.app.db.x("INSERT INTO shares (id, owner_id, token_hash, perms, created, expires, pin_hash, pin_salt, meta_enc, key_wrap) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", sid, u["id"], th, perms, time.time(),
                time.time() + days * 86400 if days > 0 else None, scrypt(pin.encode(), salt) if pin else None, salt,
                b64d(d.get("meta_enc"), 4 << 20), b64d(d.get("key_wrap"), 1024))
    hd.app.db.audit(u["id"], "share-create", sid, hd.ip)
    hd.json({"id": sid, "base": hd.app.public_url})


def r_share_snapshot_put(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    data = hd.body(LIMITS["snapshot"])
    if not data.startswith(b"LDE1"):
        raise ApiError(400, "ожидается зашифрованный пакет LDE1")
    hd.app.db.x("UPDATE shares SET snapshot = ? WHERE id = ?", hd.app.db.put_blob(data), sid)
    hd.json({"ok": True, "size": len(data)})


def r_share_list(hd: Handler):
    u = hd.user()
    hd.json([_share_row(s) for s in hd.app.db.q("SELECT * FROM shares WHERE owner_id = ? ORDER BY created DESC", u["id"])])


def r_share_get(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    hd.json(_share_row(s))


def r_share_copy_owner(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    data = hd.app.db.get_blob(s["copy"])
    if data is None:
        return hd.json({"error": "рецензент ещё ничего не сохранил"}, 404)
    hd.send(200, data, "application/octet-stream", {"X-LDE-Version": str(s["copy_ver"])})


def r_share_owner_get(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    data = hd.app.db.get_blob(s["owner_doc"])
    if data is None:
        return hd.json({"empty": True}, 404)
    hd.send(200, data, "application/octet-stream")


def r_share_snapshot_owner(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    hd.send(200, hd.app.db.get_blob(s["snapshot"]) or b"", "application/octet-stream")


def r_share_owner_put(hd: Handler, sid: str):
    """Ответы автора и решения по правкам (зашифрованы) — видны рецензенту."""
    u, s = _share_of_owner(hd, sid)
    data = hd.body(LIMITS["owner"])
    if not data.startswith(b"LDE1"):
        raise ApiError(400, "ожидается зашифрованный пакет LDE1")
    hd.app.db.x("UPDATE shares SET owner_doc = ?, owner_ver = owner_ver + 1, owner_updated = ? WHERE id = ?",
                hd.app.db.put_blob(data), time.time(), sid)
    hd.json({"ok": True})


def r_share_update(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    d = hd.jbody()
    if "revoked" in d:
        hd.app.db.x("UPDATE shares SET revoked = ? WHERE id = ?", 1 if d["revoked"] else 0, sid)
    if "days" in d:
        days = float(d["days"] or 0)
        hd.app.db.x("UPDATE shares SET expires = ? WHERE id = ?", time.time() + days * 86400 if days > 0 else None, sid)
    if "perms" in d and d["perms"] in ("edit", "comment"):
        hd.app.db.x("UPDATE shares SET perms = ? WHERE id = ?", d["perms"], sid)
    hd.app.db.audit(u["id"], "share-update", sid, hd.ip)
    hd.json({"ok": True})


def r_share_delete(hd: Handler, sid: str):
    u, s = _share_of_owner(hd, sid)
    hd.app.db.x("DELETE FROM share_sessions WHERE share_id = ?", sid)
    hd.app.db.x("DELETE FROM shares WHERE id = ?", sid)
    hd.app.db.audit(u["id"], "share-delete", sid, hd.ip)
    hd.json({"ok": True})


# ---------------------------------------------------- ссылки: рецензент ----
def _share_active(s) -> str | None:
    if not s:
        return "ссылка недействительна"
    if s["revoked"]:
        return "автор отозвал эту ссылку"
    if s["expires"] and s["expires"] < time.time():
        return "срок действия ссылки истёк"
    if not s["snapshot"]:
        return "документ ещё не загружен — попробуйте позже"
    return None


def r_open_link(hd: Handler, token: str):
    """/s/<токен>: проверить, при необходимости спросить PIN, обменять на cookie и уйти на адрес без токена."""
    hd.app.rl.check(f"open:{hd.ip}", 30, 60)
    s = hd.app.db.one("SELECT * FROM shares WHERE token_hash = ?", h(token))
    why = _share_active(s)
    if why:
        raise ApiError(404, why)
    if s["pin_hash"] is not None:
        pin = ""
        if hd.command == "POST":
            raw = hd.body(4096).decode("utf-8", "replace")
            pin = (parse_qs(raw).get("pin") or [""])[0]
            hd.app.rl.check(f"pin:{s['id']}", 10, 3600)
        if not pin or not hmac.compare_digest(scrypt(pin.encode(), s["pin_salt"]), s["pin_hash"]):
            return hd.send(200 if not pin else 403, page_pin(bool(pin)).encode(), "text/html; charset=utf-8")
    sid = new_token()
    hd.app.db.x("INSERT INTO share_sessions VALUES (?,?,?,?)", h(sid), s["id"], time.time(), time.time())
    hd.app.db.x("UPDATE shares SET opened = ?, opens = opens + 1 WHERE id = ?", time.time(), s["id"])
    path = f"/r/{s['id']}/"
    hd.redirect(path, [f"lde_r={sid}; Path={path}; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000"])


def page_pin(wrong: bool) -> str:
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>LDE — PIN</title><link rel=stylesheet href='/static/app.css'><main class=card><h1>Документ защищён PIN-кодом</h1>"
            + ("<p class=err>Неверный PIN.</p>" if wrong else "<p>Введите PIN, который сообщил автор.</p>")
            + "<form method=post><input name=pin inputmode=numeric autocomplete=off autofocus class=inp>"
            "<button class='btn primary'>Открыть</button></form><p class=muted>Ссылку целиком (с частью после #) "
            "сохраните: без неё документ не расшифровать.</p><script src='/static/keep-hash.js'></script></main>")


def _reviewer(hd: Handler, sid: str):
    s = hd.app.db.one("SELECT * FROM shares WHERE id = ?", sid)
    why = _share_active(s)
    if why:
        raise ApiError(403, why)
    ck = hd.cookies().get("lde_r")
    ok = ck and hd.app.db.one("SELECT 1 FROM share_sessions WHERE sid_hash = ? AND share_id = ?", h(ck), sid)
    if not ok:
        raise ApiError(401, "откройте документ по ссылке от автора")
    hd.app.db.x("UPDATE share_sessions SET last_used = ? WHERE sid_hash = ?", time.time(), h(ck))
    if hd.command not in ("GET", "HEAD"):
        hd.check_csrf()
    return s


def r_review_page(hd: Handler, sid: str):
    """Studio в роли рецензента: тот же index.html, «сервер» — в браузере (static/rbackend.js)."""
    _reviewer(hd, sid)
    html = (STUDIO / "index.html").read_text(encoding="utf-8")
    html = html.replace('<html lang="ru">', '<html lang="ru" data-lde-role="reviewer" data-theme="light">', 1)
    html = html.replace('href="/static/app.css"', 'href="/studio/app.css"')
    html = html.replace('<script src="/static/app.js"></script>',
                        '<script src="/static/crypto.js"></script>\n<script src="/static/rbackend.js"></script>\n'
                        '<script src="/studio/app.js"></script>')
    html = html.replace("<title>LDE Studio</title>", "<title>Документ на проверку — LDE</title>")
    hd.send(200, html.encode("utf-8"), "text/html; charset=utf-8")


def r_studio_file(hd: Handler, name: str):
    p = STUDIO / name
    if not p.is_file():
        raise ApiError(404, "нет такого файла")
    ctype = {"js": "application/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}[name.rsplit(".", 1)[1]]
    hd.send(200, p.read_bytes(), ctype, cache="no-cache")


def r_review_meta(hd: Handler, sid: str):
    s = _reviewer(hd, sid)
    hd.json({"id": sid, "perms": s["perms"], "expires": s["expires"], "copy_ver": s["copy_ver"],
             "owner_ver": s["owner_ver"]})


def r_review_snapshot(hd: Handler, sid: str):
    s = _reviewer(hd, sid)
    hd.send(200, hd.app.db.get_blob(s["snapshot"]) or b"", "application/octet-stream")


def r_review_copy_get(hd: Handler, sid: str):
    s = _reviewer(hd, sid)
    data = hd.app.db.get_blob(s["copy"])
    if data is None:
        return hd.json({"empty": True}, 404)
    hd.send(200, data, "application/octet-stream", {"X-LDE-Version": str(s["copy_ver"])})


def r_review_copy_put(hd: Handler, sid: str):
    """Сохранить копию рецензента (зашифрована). If-Match: версия — защита от затирания из второй вкладки."""
    s = _reviewer(hd, sid)
    data = hd.body(LIMITS["copy"])
    if not data.startswith(b"LDE1"):
        raise ApiError(400, "ожидается зашифрованный пакет LDE1")
    want = hd.headers.get("If-Match")
    name = hd.app.db.put_blob(data)
    with hd.app.db.tx() as c:
        cur = c.execute("SELECT copy_ver FROM shares WHERE id = ?", (sid,)).fetchone()["copy_ver"]
        if want is not None and want.strip('"') != str(cur):
            raise ApiError(409, "копия изменилась в другой вкладке — обновите страницу")
        c.execute("UPDATE shares SET copy = ?, copy_ver = copy_ver + 1, copy_updated = ? WHERE id = ?", (name, time.time(), sid))
    hd.json({"ok": True, "version": cur + 1})


def r_review_owner_get(hd: Handler, sid: str):
    s = _reviewer(hd, sid)
    data = hd.app.db.get_blob(s["owner_doc"])
    if data is None:
        return hd.json({"empty": True}, 404)
    hd.send(200, data, "application/octet-stream")


# --------------------------------------------------------------- проекты ----
def _member(hd: Handler, pid: str, roles=ROLES):
    u = hd.user()
    m = hd.app.db.one("SELECT m.role, m.key_wrap, m.key_gen FROM members m JOIN projects p ON p.id = m.project_id "
                      "WHERE m.project_id = ? AND m.user_id = ? AND p.deleted = 0", pid, u["id"])
    if not m:
        raise ApiError(404, "нет такого проекта или нет доступа")
    if m["role"] not in roles:
        raise ApiError(403, "недостаточно прав для этого действия")
    return u, m


def r_projects(hd: Handler):
    u = hd.user()
    rows = hd.app.db.q("SELECT p.id, p.meta_enc, p.updated, p.created, m.role, m.key_wrap, m.key_gen, "
                       "(SELECT username FROM users WHERE id = p.owner_id) owner FROM projects p "
                       "JOIN members m ON m.project_id = p.id WHERE m.user_id = ? AND p.deleted = 0 ORDER BY p.updated DESC", u["id"])
    hd.json([{"id": r["id"], "meta_enc": b64e(r["meta_enc"]), "updated": r["updated"], "created": r["created"],
              "role": r["role"], "key_wrap": b64e(r["key_wrap"]), "key_gen": r["key_gen"], "owner": r["owner"]} for r in rows])


def r_project_create(hd: Handler):
    u = hd.user()
    d = hd.jbody()
    pid = new_id()
    now = time.time()
    with hd.app.db.tx() as c:
        c.execute("INSERT INTO projects (id, owner_id, meta_enc, created, updated) VALUES (?,?,?,?,?)",
                  (pid, u["id"], b64d(d.get("meta_enc"), 64 << 10), now, now))
        c.execute("INSERT INTO members VALUES (?,?,?,?,?,?)", (pid, u["id"], "owner", b64d(d.get("key_wrap"), 1024), 1, now))
    hd.app.db.audit(u["id"], "project-create", pid, hd.ip)
    hd.json({"id": pid})


def r_project_get(hd: Handler, pid: str):
    u, m = _member(hd, pid)
    p = hd.app.db.one("SELECT * FROM projects WHERE id = ?", pid)
    members = [{"username": r["username"], "role": r["role"], "added": r["added"]} for r in hd.app.db.q(
        "SELECT u.username, m.role, m.added FROM members m JOIN users u ON u.id = m.user_id WHERE m.project_id = ?", pid)]
    refs = {r["name"]: {"value": b64e(r["value"]), "version": r["version"], "updated": r["updated"]}
            for r in hd.app.db.q("SELECT * FROM refs WHERE project_id = ?", pid)}
    hd.json({"id": pid, "meta_enc": b64e(p["meta_enc"]), "role": m["role"], "key_wrap": b64e(m["key_wrap"]),
             "members": members, "refs": refs, "updated": p["updated"]})


def r_project_meta(hd: Handler, pid: str):
    u, m = _member(hd, pid, ("owner",))
    d = hd.jbody()
    hd.app.db.x("UPDATE projects SET meta_enc = ?, updated = ? WHERE id = ?", b64d(d.get("meta_enc"), 64 << 10), time.time(), pid)
    hd.json({"ok": True})


def r_project_member(hd: Handler, pid: str):
    u, m = _member(hd, pid, ("owner",))
    d = hd.jbody()
    target = hd.app.db.one("SELECT id FROM users WHERE username = ? AND disabled = 0", (d.get("username") or "").lower())
    if not target:
        raise ApiError(404, "нет такого пользователя")
    role = d.get("role", "reader")
    if role == "remove":
        if target["id"] == u["id"]:
            raise ApiError(400, "владелец не может удалить себя")
        hd.app.db.x("DELETE FROM members WHERE project_id = ? AND user_id = ?", pid, target["id"])
    else:
        if role not in ROLES:
            raise ApiError(400, "роль: owner | editor | reviewer | reader")
        hd.app.db.x("INSERT INTO members VALUES (?,?,?,?,?,?) ON CONFLICT(project_id, user_id) DO UPDATE SET "
                    "role = excluded.role, key_wrap = COALESCE(excluded.key_wrap, members.key_wrap)",
                    pid, target["id"], role, b64d(d.get("key_wrap"), 1024), 1, time.time())
    hd.app.db.audit(u["id"], f"member-{role}", f"{pid}:{d.get('username')}", hd.ip)
    hd.json({"ok": True})


def r_project_pack_put(hd: Handler, pid: str, kind: str):
    """Зашифрованный пакет: kind=pack (git bundle) или view (витрина версии для панели)."""
    u, m = _member(hd, pid, ("owner", "editor"))
    data = hd.body(LIMITS["pack"])
    if not data.startswith(b"LDE1"):
        raise ApiError(400, "ожидается зашифрованный пакет LDE1")
    name = hd.app.db.put_blob(data)
    with hd.app.db.tx() as c:
        seq = (c.execute("SELECT COALESCE(MAX(seq), 0) FROM packs WHERE project_id = ? AND kind = ?", (pid, kind)).fetchone()[0]) + 1
        c.execute("INSERT INTO packs VALUES (?,?,?,?,?,?,?)", (pid, seq, name, len(data), time.time(), u["id"], kind))
        c.execute("UPDATE projects SET updated = ? WHERE id = ?", (time.time(), pid))
    hd.json({"seq": seq, "blob": name})


def r_project_packs(hd: Handler, pid: str, kind: str):
    _member(hd, pid)
    after = int(hd.q.get("after") or 0)
    hd.json([{"seq": r["seq"], "blob": r["blob"], "size": r["size"], "created": r["created"]} for r in hd.app.db.q(
        "SELECT * FROM packs WHERE project_id = ? AND kind = ? AND seq > ? ORDER BY seq", pid, kind, after)])


def r_project_blob(hd: Handler, pid: str, name: str):
    _member(hd, pid)
    if not hd.app.db.one("SELECT 1 FROM packs WHERE project_id = ? AND blob = ?", pid, name):
        raise ApiError(404, "нет такого пакета")
    hd.send(200, hd.app.db.get_blob(name) or b"", "application/octet-stream", cache="private, max-age=86400")


def r_project_refs(hd: Handler, pid: str):
    """Атомарное обновление веток: «сравни и замени» по номеру версии (как git push без --force)."""
    u, m = _member(hd, pid, ("owner", "editor"))
    d = hd.jbody()
    ups = d.get("updates") or []
    with hd.app.db.tx() as c:
        for up in ups:
            cur = c.execute("SELECT version FROM refs WHERE project_id = ? AND name = ?", (pid, up["name"])).fetchone()
            have = cur["version"] if cur else 0
            if have != int(up.get("old_version", 0)):
                raise ApiError(409, f"ветка «{up['name']}» уже изменилась на сервере — сначала синхронизируйте (pull)")
        for up in ups:
            val = b64d(up.get("value"), 64 << 10)
            c.execute("INSERT INTO refs VALUES (?,?,?,?,?,?) ON CONFLICT(project_id, name) DO UPDATE SET "
                      "value = excluded.value, version = excluded.version, updated = excluded.updated, "
                      "updated_by = excluded.updated_by", (pid, up["name"], val, int(up.get("old_version", 0)) + 1,
                                                           time.time(), u["id"]))
        c.execute("UPDATE projects SET updated = ? WHERE id = ?", (time.time(), pid))
    hd.app.db.audit(u["id"], "push", pid, hd.ip)
    hd.json({"ok": True})


def r_project_delete(hd: Handler, pid: str):
    u, m = _member(hd, pid, ("owner",))
    hd.app.db.x("UPDATE projects SET deleted = 1 WHERE id = ?", pid)
    hd.app.db.audit(u["id"], "project-delete", pid, hd.ip)
    hd.json({"ok": True})


# ---------------------------------------------------------------- панель ----
def r_panel(hd: Handler):
    hd.static("panel.html")


def r_join(hd: Handler):
    hd.static("join.html")


ROUTES = [
    ("GET", r"/healthz", r_health),
    ("GET", r"/", r_index),
    ("GET", r"/panel/?", r_panel),
    ("GET", r"/join/?", r_join),
    ("GET", r"/static/([\w./\-]+)", r_static),
    ("GET", r"/studio/(app\.js|app\.css)", r_studio_file),
    ("GET", r"/api/info", r_info),
    ("GET", r"/api/prelogin", r_prelogin),
    ("GET", r"/api/invite", r_invite_info),
    ("POST", r"/api/register", r_register),
    ("POST", r"/api/login", r_login),
    ("POST", r"/api/logout", r_logout),
    ("GET", r"/api/me", r_me),
    ("GET", r"/api/users/([\w.\-]+)/pubkey", r_pubkey),
    ("POST", r"/api/invites", r_invite_create),
    ("GET", r"/api/shares", r_share_list),
    ("POST", r"/api/shares", r_share_create),
    ("GET", r"/api/shares/([\w\-]+)", r_share_get),
    ("PUT", r"/api/shares/([\w\-]+)/snapshot", r_share_snapshot_put),
    ("GET", r"/api/shares/([\w\-]+)/copy", r_share_copy_owner),
    ("PUT", r"/api/shares/([\w\-]+)/owner", r_share_owner_put),
    ("GET", r"/api/shares/([\w\-]+)/owner", r_share_owner_get),
    ("GET", r"/api/shares/([\w\-]+)/snapshot", r_share_snapshot_owner),
    ("POST", r"/api/shares/([\w\-]+)", r_share_update),
    ("DELETE", r"/api/shares/([\w\-]+)", r_share_delete),
    ("GET", r"/s/([\w\-]{20,})", r_open_link),
    ("POST", r"/s/([\w\-]{20,})", r_open_link),
    ("GET", r"/r/([\w\-]+)/", r_review_page),
    ("GET", r"/r/([\w\-]+)/api/meta", r_review_meta),
    ("GET", r"/r/([\w\-]+)/api/snapshot", r_review_snapshot),
    ("GET", r"/r/([\w\-]+)/api/copy", r_review_copy_get),
    ("PUT", r"/r/([\w\-]+)/api/copy", r_review_copy_put),
    ("GET", r"/r/([\w\-]+)/api/owner", r_review_owner_get),
    ("GET", r"/api/projects", r_projects),
    ("POST", r"/api/projects", r_project_create),
    ("GET", r"/api/projects/([\w\-]+)", r_project_get),
    ("POST", r"/api/projects/([\w\-]+)/meta", r_project_meta),
    ("POST", r"/api/projects/([\w\-]+)/members", r_project_member),
    ("PUT", r"/api/projects/([\w\-]+)/(pack|view)s", r_project_pack_put),
    ("GET", r"/api/projects/([\w\-]+)/(pack|view)s", r_project_packs),
    ("GET", r"/api/projects/([\w\-]+)/blobs/([0-9a-f]{64})", r_project_blob),
    ("POST", r"/api/projects/([\w\-]+)/refs", r_project_refs),
    ("DELETE", r"/api/projects/([\w\-]+)", r_project_delete),
]


def make_server(data: Path, host: str, port: int, public_url: str = "", trust_proxy: bool = False) -> ThreadingHTTPServer:
    Handler.app = App(data, public_url, trust_proxy)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lde-server")
    ap.add_argument("--data", default=os.environ.get("LDE_DATA", "/data"))
    ap.add_argument("--host", default=os.environ.get("LDE_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LDE_PORT", "8790")))
    ap.add_argument("--public-url", default=os.environ.get("LDE_PUBLIC_URL", ""))
    ap.add_argument("--trust-proxy", action="store_true", default=os.environ.get("LDE_TRUST_PROXY") == "1")
    a = ap.parse_args(argv)
    srv = make_server(Path(a.data), a.host, a.port, a.public_url, a.trust_proxy)
    print(f"lde-server {VERSION}: {a.host}:{srv.server_address[1]} data={a.data} url={a.public_url}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
