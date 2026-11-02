"""Клиент сервера LDE: вход, ключ устройства, вызовы API.

Настройки — ~/.config/lde/servers.json (права 0600): адрес, имя, ключ устройства, ключи пользователя.
Пароль не хранится и на сервер не уходит: из него выводятся ключ входа и ключ шифрования (lde/crypto.py).
"""
from __future__ import annotations

import json
import os
import platform
import urllib.error
import urllib.request
from pathlib import Path

from lde import crypto


class ServerError(RuntimeError):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def _cfg_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lde" / "servers.json"


def load_cfg() -> dict:
    try:
        return json.loads(_cfg_path().read_text(encoding="utf-8"))
    except Exception:
        return {"default": None, "servers": {}}


def save_cfg(cfg: dict):
    p = _cfg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def norm_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


class Server:
    def __init__(self, url: str | None = None):
        cfg = load_cfg()
        url = norm_url(url) if url else cfg.get("default")
        if not url:
            raise ServerError("сервер LDE не подключён: lde server login <адрес> <имя>")
        self.url = url
        self.acc = cfg.get("servers", {}).get(url) or {}

    @property
    def logged_in(self) -> bool:
        return bool(self.acc.get("token"))

    @property
    def priv(self) -> bytes:
        return crypto.b64d(self.acc["priv"])

    @property
    def pub(self) -> bytes:
        return crypto.b64d(self.acc["pub"])

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, headers=None, auth=True,
             timeout=120) -> tuple[int, bytes, dict]:
        hd = {"User-Agent": "lde-client"}
        if auth:
            if not self.logged_in:
                raise ServerError("нет входа на сервер: lde server login")
            hd["Authorization"] = "Bearer " + self.acc["token"]
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hd["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
            hd["Content-Type"] = "application/octet-stream"
        hd.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=hd)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                msg = json.loads(payload).get("error") or str(e)
            except Exception:
                msg = str(e)
            raise ServerError(msg, e.code)
        except urllib.error.URLError as e:
            raise ServerError(f"сервер {self.url} недоступен: {e.reason}")

    def json(self, method: str, path: str, body=None, **kw):
        code, data, _ = self.call(method, path, body, **kw)
        return json.loads(data or b"{}")


def login(url: str, username: str, password: str) -> dict:
    url = norm_url(url)
    s = Server.__new__(Server)
    s.url, s.acc = url, {}
    pre = s.json("GET", f"/api/prelogin?username={urllib.request.quote(username.lower())}", auth=False)
    auth_key, enc_key = crypto.password_keys(password, pre["kdf_salt"], pre.get("iterations", crypto.PBKDF2_ITER))
    d = s.json("POST", "/api/login", {"username": username.lower(), "auth": crypto.b64e(auth_key),
                                      "device": f"LDE на {platform.node() or 'компьютере'}"}, auth=False)
    priv = crypto.open_(enc_key, crypto.b64d(d["enc_priv"]), "user-priv")
    cfg = load_cfg()
    cfg.setdefault("servers", {})[url] = {"username": d["user"]["username"], "token": d["token"],
                                          "priv": crypto.b64e(priv), "pub": d["pubkey"],
                                          "is_admin": d["user"]["is_admin"]}
    cfg["default"] = url
    save_cfg(cfg)
    return {"url": url, "username": d["user"]["username"], "is_admin": d["user"]["is_admin"]}


def logout(url: str | None = None):
    s = Server(url)
    try:
        s.json("POST", "/api/logout", {})
    except ServerError:
        pass
    cfg = load_cfg()
    cfg.get("servers", {}).pop(s.url, None)
    if cfg.get("default") == s.url:
        cfg["default"] = next(iter(cfg.get("servers", {})), None)
    save_cfg(cfg)


def status() -> dict:
    cfg = load_cfg()
    url = cfg.get("default")
    if not url:
        return {"connected": False}
    acc = cfg["servers"].get(url, {})
    out = {"connected": True, "url": url, "username": acc.get("username"), "is_admin": acc.get("is_admin", False)}
    try:
        Server(url).json("GET", "/api/me")
        out["ok"] = True
    except ServerError as e:
        out["ok"] = False
        out["error"] = str(e)
    return out
