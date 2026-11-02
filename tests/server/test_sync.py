"""Хранение проекта на сервере LDE: публикация, два «компьютера», синхронизация со слиянием по блокам,
конфликт и его решение, роли (читатель не может отправлять), сервер хранит только шифротекст.

    toolchain/venv/bin/python tests/server/test_sync.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import free_port  # noqa: E402
from lde import crypto  # noqa: E402

LDE = str(REPO / "bin" / "lde")
ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def register(url, invite, user, pw):
    """Регистрация из Python тем же форматом, что страница /join (проверка совместимости форматов)."""
    salt = crypto.b64e(os.urandom(16))
    auth, enc = crypto.password_keys(pw, salt)
    priv, pub = crypto.gen_keypair()
    body = json.dumps({"invite": invite, "username": user, "kdf_salt": salt, "auth": crypto.b64e(auth),
                       "pubkey": crypto.b64e(pub), "enc_priv": crypto.b64e(crypto.seal(enc, priv, "user-priv", compress=False))}).encode()
    req = urllib.request.Request(url + "/api/register", data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


with tempfile.TemporaryDirectory(prefix="lde-sync-") as td:
    td = Path(td)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    senv = dict(os.environ, LDE_DATA=str(td / "data"), LDE_PUBLIC_URL=url)
    srv = subprocess.Popen([sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=REPO / "lde-server", env=senv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=2)
            break
        except Exception:
            time.sleep(0.1)
    inv = lambda *a: subprocess.run([sys.executable, "-m", "app.cli", "invite", *a], cwd=REPO / "lde-server", env=senv,
                                    capture_output=True, text=True).stdout.split()[0].split("#")[1]
    register(url, inv("--admin"), "alice", "пароль-алисы-123")
    register(url, inv(), "bob", "пароль-боба-4567")

    def dev(name):
        d = td / ("cfg-" + name)
        d.mkdir(exist_ok=True)
        return d

    def lde(cfg, *a, pw=None, check=False):
        env = dict(os.environ, XDG_CONFIG_HOME=str(cfg), **({"LDE_PASSWORD": pw} if pw else {}))
        r = subprocess.run([LDE, *a], capture_output=True, text=True, env=env)
        if check and r.returncode:
            raise RuntimeError(f"lde {' '.join(a)}: {r.stdout}{r.stderr}")
        return r

    A, B, Bob = dev("a"), dev("b"), dev("bob")
    lde(A, "server", "login", url, "alice", pw="пароль-алисы-123", check=True)
    lde(B, "server", "login", url, "alice", pw="пароль-алисы-123", check=True)
    lde(Bob, "server", "login", url, "bob", pw="пароль-боба-4567", check=True)
    pa, pb = td / "pa", td / "pb"
    subprocess.run([LDE, "init", str(pa), "--no-build"], capture_output=True, check=True)
    lde(A, "version", "Черновик", "-C", str(pa), check=True)
    r = lde(A, "publish", "-C", str(pa))
    step("публикация проекта", r.returncode == 0 and "опубликован" in r.stdout, r.stdout + r.stderr)
    pid = subprocess.run(["git", "-C", str(pa), "config", "lde.remote.project"], capture_output=True, text=True).stdout.strip()
    lst = lde(B, "remote", "list").stdout
    step("второй компьютер видит проект (название расшифровано)", pid in lst and "RC-фильтра" in lst, lst)
    r = lde(B, "clone", pid, str(pb))
    step("клонирование: тот же текст и версии", r.returncode == 0 and
         (pa / "text" / "02-model.md").read_text() == (pb / "text" / "02-model.md").read_text() and
         "Черновик" in lde(B, "versions", "-C", str(pb)).stdout, r.stdout + r.stderr)

    # правки в разных абзацах одного файла на двух компьютерах
    fa, fb = pa / "text" / "02-model.md", pb / "text" / "02-model.md"
    fa.write_text(fa.read_text().replace("Модуль коэффициента передачи", "Модуль комплексного коэффициента передачи"))
    fb.write_text(fb.read_text().replace("Частота среза зависит", "Частота среза определяется"))
    r1 = lde(A, "sync", "-C", str(pa))
    r2 = lde(B, "sync", "-C", str(pb))
    r3 = lde(A, "sync", "-C", str(pa))
    step("разные абзацы: синхронизация без конфликтов", r2.returncode == 0 and "слито" in r2.stdout, r2.stdout + r2.stderr)
    step("обе копии одинаковы и содержат обе правки", fa.read_text() == fb.read_text() and "комплексного" in fa.read_text()
         and "определяется" in fa.read_text(), r3.stdout)

    # одна и та же фраза по-разному → конфликт → решение
    fa.write_text(fa.read_text().replace("Параметры расчёта приведены", "Параметры фильтра приведены"))
    fb.write_text(fb.read_text().replace("Параметры расчёта приведены", "Параметры модели приведены"))
    lde(A, "sync", "-C", str(pa))
    r = lde(B, "sync", "-C", str(pb))
    step("одно слово по-разному: конфликт обнаружен", r.returncode == 1 and "конфликт" in r.stdout and "<<<<<<<" in fb.read_text(), r.stdout)
    push_blocked = lde(B, "push", "-C", str(pb))
    lst = lde(B, "merge", "-C", str(pb)).stdout
    uid = next((w.split("=")[1] for w in lst.split() if w.startswith("uid=")), "")
    lde(B, "merge", "--resolve", uid, "theirs", "-C", str(pb), check=True)
    r = lde(B, "sync", "-C", str(pb))
    lde(A, "sync", "-C", str(pa))
    step("конфликт решён, копии сошлись", "<<<<<<<" not in fb.read_text() and fa.read_text() == fb.read_text()
         and "Параметры фильтра приведены" in fa.read_text(), r.stdout + r.stderr)

    # роли
    lde(A, "remote", "member", "bob", "reader", "-C", str(pa), check=True)
    pbob = td / "pbob"
    r = lde(Bob, "clone", pid, str(pbob))
    step("читатель получает проект", r.returncode == 0 and (pbob / "lde.toml").exists(), r.stdout + r.stderr)
    (pbob / "text" / "03-zakl.md").write_text("# Заключение {-}\n\nПравка читателя.\n")
    r = lde(Bob, "push", "-C", str(pbob))
    step("читатель не может отправлять", r.returncode != 0 and "прав" in r.stderr, r.stdout + r.stderr)

    # сервер хранит только шифротекст
    blobs = b"".join(p.read_bytes() for p in (td / "data" / "blobs").rglob("*") if p.is_file())
    db = (td / "data" / "lde.sqlite").read_bytes() + b"".join(p.read_bytes() for p in (td / "data").glob("lde.sqlite-*"))
    step("на сервере нет текста, названий и сообщений коммитов", all(w.encode() not in blobs + db for w in
         ("Модуль комплексного", "RC-фильтра", "Черновик", "Синхронизация")))
    views = json.load(urllib.request.urlopen(urllib.request.Request(
        url + f"/api/projects/{pid}/views", headers={"Authorization": "Bearer " + json.loads((A / "lde" / "servers.json").read_text())["servers"][url]["token"]})))
    step("витрины версий для панели загружены", isinstance(views, list))
    srv.terminate()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
