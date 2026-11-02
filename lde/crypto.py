"""Сквозное шифрование LDE (тот же формат — в браузере: lde-server/web/static/crypto.js).

Только стандартные алгоритмы: AES-256-GCM, X25519 + HKDF-SHA256, PBKDF2-SHA256.

Пакет LDE1:  b"LDE1" | flags(1: bit0 = gzip) | nonce(12) | AES-GCM(шифротекст+тег)
             AAD = первые 5 байт + назначение (например b"share-snapshot") — пакет одного
             назначения нельзя подсунуть вместо другого.
Ключ для получателя LDK1:  b"LDK1" | eph_pub(32) | nonce(12) | AES-GCM(ключ)
             ключ обёртки = HKDF(X25519(eph, получатель), salt = eph_pub + pub получателя, info = "lde-wrap")
Пароль:      master = PBKDF2-SHA256(пароль, kdf_salt, 600 000); вход = HKDF(master, "lde-auth"),
             шифрование = HKDF(master, "lde-enc"). Серверу уходит только ключ входа.
Закрытый ключ пользователя — PKCS#8 (X25519), хранится на сервере как LDE1(ключ шифрования, "user-priv").
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import os
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PBKDF2_ITER = 600_000


class CryptoError(Exception):
    pass


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64url_d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_key() -> bytes:
    return secrets.token_bytes(32)


def hkdf(secret: bytes, info: str, salt: bytes | None = None, n: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info.encode()).derive(secret)


def seal(key: bytes, data: bytes, purpose: str, compress: bool = True) -> bytes:
    flags = 1 if compress else 0
    body = gzip.compress(data, mtime=0) if compress else data
    head = b"LDE1" + bytes([flags])
    nonce = os.urandom(12)
    return head + nonce + AESGCM(key).encrypt(nonce, body, head + purpose.encode())


def open_(key: bytes, blob: bytes, purpose: str) -> bytes:
    if len(blob) < 5 + 12 + 16 or blob[:4] != b"LDE1":
        raise CryptoError("не пакет LDE1")
    head, nonce, ct = blob[:5], blob[5:17], blob[17:]
    try:
        body = AESGCM(key).decrypt(nonce, ct, head + purpose.encode())
    except Exception:
        raise CryptoError("не удалось расшифровать (неверный ключ или повреждённые данные)")
    return gzip.decompress(body) if head[4] & 1 else body


# ---------------------------------------------------------------- ключи ----
def gen_keypair() -> tuple[bytes, bytes]:
    """(закрытый PKCS#8 DER, открытый raw 32 байта)."""
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub


def _priv(pkcs8: bytes) -> X25519PrivateKey:
    return serialization.load_der_private_key(pkcs8, password=None)


def wrap_key(recipient_pub: bytes, key: bytes, purpose: str) -> bytes:
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub))
    k = hkdf(shared, "lde-wrap", salt=eph_pub + recipient_pub)
    nonce = os.urandom(12)
    return b"LDK1" + eph_pub + nonce + AESGCM(k).encrypt(nonce, key, ("wrap:" + purpose).encode())


def unwrap_key(my_priv_pkcs8: bytes, blob: bytes, purpose: str) -> bytes:
    if len(blob) < 4 + 32 + 12 + 16 or blob[:4] != b"LDK1":
        raise CryptoError("не обёртка ключа LDK1")
    me = _priv(my_priv_pkcs8)
    my_pub = me.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    eph_pub, nonce, ct = blob[4:36], blob[36:48], blob[48:]
    k = hkdf(me.exchange(X25519PublicKey.from_public_bytes(eph_pub)), "lde-wrap", salt=eph_pub + my_pub)
    try:
        return AESGCM(k).decrypt(nonce, ct, ("wrap:" + purpose).encode())
    except Exception:
        raise CryptoError("ключ зашифрован не для этого пользователя")


# --------------------------------------------------------------- пароль ----
def password_keys(password: str, kdf_salt_b64: str, iterations: int = PBKDF2_ITER) -> tuple[bytes, bytes]:
    """(ключ входа — серверу, ключ шифрования — только на устройстве)."""
    master = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b64d(kdf_salt_b64), iterations, 32)
    return hkdf(master, "lde-auth"), hkdf(master, "lde-enc")


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
