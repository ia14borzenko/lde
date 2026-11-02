"""Хранилище сервера LDE: SQLite (метаданные, права) + каталог блобов (зашифрованное содержимое).

Сервер не видит текстов: всё содержимое приходит зашифрованным на устройствах пользователей
(сквозное шифрование), здесь — только байты, размеры, время и права доступа.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, auth_hash BLOB NOT NULL, auth_salt BLOB NOT NULL,
  kdf_salt TEXT NOT NULL, pubkey BLOB NOT NULL, enc_priv BLOB NOT NULL, is_admin INTEGER DEFAULT 0,
  created REAL NOT NULL, disabled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS invites (
  token_hash BLOB PRIMARY KEY, is_admin INTEGER DEFAULT 0, created REAL, expires REAL, created_by INTEGER,
  used_by INTEGER, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS devices (
  token_hash BLOB PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT, kind TEXT DEFAULT 'device',
  created REAL, last_used REAL, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS shares (
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, token_hash BLOB UNIQUE NOT NULL, perms TEXT NOT NULL,
  created REAL NOT NULL, expires REAL, revoked INTEGER DEFAULT 0, pin_hash BLOB, pin_salt BLOB,
  meta_enc BLOB, key_wrap BLOB, snapshot TEXT, opened REAL, opens INTEGER DEFAULT 0,
  copy TEXT, copy_ver INTEGER DEFAULT 0, copy_updated REAL,
  owner_doc TEXT, owner_ver INTEGER DEFAULT 0, owner_updated REAL);
CREATE TABLE IF NOT EXISTS share_sessions (
  sid_hash BLOB PRIMARY KEY, share_id TEXT NOT NULL, created REAL, last_used REAL);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, meta_enc BLOB, created REAL, updated REAL, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members (
  project_id TEXT NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, key_wrap BLOB, key_gen INTEGER DEFAULT 1,
  added REAL, PRIMARY KEY (project_id, user_id));
CREATE TABLE IF NOT EXISTS refs (
  project_id TEXT NOT NULL, name TEXT NOT NULL, value BLOB NOT NULL, version INTEGER NOT NULL,
  updated REAL, updated_by INTEGER, PRIMARY KEY (project_id, name));
CREATE TABLE IF NOT EXISTS packs (
  project_id TEXT NOT NULL, seq INTEGER NOT NULL, blob TEXT NOT NULL, size INTEGER, created REAL, created_by INTEGER,
  kind TEXT DEFAULT 'pack', PRIMARY KEY (project_id, seq, kind));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, time REAL, user_id INTEGER, action TEXT, target TEXT, ip TEXT);
"""


def h(token: str | bytes) -> bytes:
    """Хеш токена для хранения (сами токены сервер не хранит)."""
    if isinstance(token, str):
        token = token.encode()
    return hashlib.sha256(token).digest()


class DB:
    def __init__(self, data: Path):
        self.data = Path(data)
        (self.data / "blobs").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.data / "lde.sqlite", check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args):
        r = self.q(sql, *args)
        return r[0] if r else None

    def x(self, sql: str, *args) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.rowcount

    def tx(self):
        return _Tx(self)

    # ---------------------------------------------------------- блобы ----
    def put_blob(self, data: bytes) -> str:
        name = hashlib.sha256(data).hexdigest()
        p = self.data / "blobs" / name[:2] / name
        if not p.exists():
            p.parent.mkdir(exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return name

    def get_blob(self, name: str | None) -> bytes | None:
        if not name or not all(c in "0123456789abcdef" for c in name):
            return None
        p = self.data / "blobs" / name[:2] / name
        return p.read_bytes() if p.exists() else None

    def audit(self, user_id, action, target="", ip=""):
        self.x("INSERT INTO audit (time, user_id, action, target, ip) VALUES (?,?,?,?,?)",
               time.time(), user_id, action, target, ip)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, et, ev, tb):
        try:
            self.db.conn.execute("COMMIT" if et is None else "ROLLBACK")
        finally:
            self.db.lock.release()
        return False
