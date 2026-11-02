"""MCP-сервер LDE (`lde mcp`): протокол, защита, инструменты, stdio.

    toolchain/venv/bin/python tests/server/test_mcp.py

Сервер — отдельным процессом, как у пользователя; настройки (~/.config/lde) — во временном каталоге.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import free_port  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


def post(url, body, headers=None, raw=False):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, body if raw else json.dumps(body).encode(), h), timeout=600)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


with tempfile.TemporaryDirectory(prefix="lde-mcp-") as td:
    td = Path(td)
    env = dict(os.environ, XDG_CONFIG_HOME=str(td / "cfg"), LDE_NO_RECENT="1")
    port = free_port()
    srv = subprocess.Popen([str(REPO / "bin" / "lde"), "mcp", "--port", str(port)], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        link = None
        t0 = time.time()
        while time.time() - t0 < 30 and not link:
            line = srv.stdout.readline()
            if "/mcp?token=" in line:
                link = line.strip()
        step("lde mcp печатает ссылку", bool(link), link)
        url, token = link.split("?token=")
        A = {"Authorization": "Bearer " + token}
        mcfg = td / "cfg" / "lde" / "mcp.json"
        step("ключ и порт сохранены (0600)", json.loads(mcfg.read_text())["port"] == port and oct(mcfg.stat().st_mode & 0o777) == "0o600")

        # --- защита
        c, _, _ = post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        step("без ключа — 401", c == 401, c)
        c, _, _ = post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Authorization": "Bearer x" + token})
        step("неверный ключ — 401", c == 401, c)
        c, _, _ = post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {**A, "Origin": "http://evil.example"})
        step("чужой Origin — 403 (защита от DNS-rebinding)", c == 403, c)
        import http.client
        hc = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        hc.putrequest("POST", "/mcp", skip_host=True)
        for k, v in {**A, "Host": f"evil.example:{port}", "Content-Type": "application/json", "Content-Length": "2"}.items():
            hc.putheader(k, v)
        hc.endheaders(b"{}")
        c = hc.getresponse().status
        step("чужой Host — 403", c == 403, c)
        c, _, b = post(url + "?token=" + token, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        step("ключ в ссылке (?token=) — принимается", c == 200 and json.loads(b)["result"] == {}, (c, b))

        # --- протокол
        c, h, b = post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2026-07-28", "clientInfo": {"name": "t", "version": "0"}, "capabilities": {}}}, A)
        r = json.loads(b)["result"]
        sess = h.get("Mcp-Session-Id")
        step("initialize: незнакомая версия → поддерживаемая, сеанс выдан", r["protocolVersion"] == "2025-06-18" and sess, r["protocolVersion"])
        step("instructions: что за среда и порядок работы", all(k in r["instructions"] for k in ("ГОСТ 7.32", "project_status", "lde_guide", "check")))
        step("instructions короче лимита клиентов (32 КБ)", len(r["instructions"].encode()) < 32768)
        A2 = {**A, "Mcp-Session-Id": sess}
        c, _, b = post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, A2)
        step("уведомление → 202 без тела", c == 202 and not b, c)
        c, _, b = post(url, {"jsonrpc": "2.0", "id": 5, "method": "server/discover"}, A2)
        step("неизвестный метод → -32601", json.loads(b)["error"]["code"] == -32601)
        c, _, b = post(url, b"{", A2, raw=True)
        step("битый JSON → -32700", c == 400 and json.loads(b)["error"]["code"] == -32700)
        c, _, b = post(url, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], A2)
        bt = json.loads(b)
        step("пакет запросов", isinstance(bt, list) and len(bt) == 2)
        tools = bt[1]["result"]["tools"]
        bad = [t["name"] for t in tools if t["inputSchema"].get("type") != "object"
               or not set(t["inputSchema"].get("required", [])) <= set(t["inputSchema"]["properties"])
               or any("_req" in v for v in t["inputSchema"]["properties"].values())]
        step(f"tools/list: {len(tools)} инструментов, схемы корректны", len(tools) >= 40 and not bad, bad)
        c, _, b = post(url, {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "lde://guide/format"}}, A2)
        step("ресурс lde://guide/format — формат исходников", "{=" in json.loads(b)["result"]["contents"][0]["text"])
        c, _, b = post(url, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "outline", "arguments": {}}}, A2)
        res = json.loads(b)["result"]
        step("без проекта — понятная ошибка инструмента (isError)", res["isError"] and "project_open" in res["content"][0]["text"])

        def call(name, **a):
            c, _, b = post(url, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": a}}, A2)
            res = json.loads(b)["result"]
            return res["isError"], res["content"]

        def text(name, **a):
            e, cont = call(name, **a)
            return e, "\n".join(x.get("text", "") for x in cont if x["type"] == "text")

        # --- работа
        proj = td / "proj"
        e, s = text("project_create", path=str(proj), title="Отчёт MCP")
        step("project_create: проект создан и открыт", not e and "Отчёт MCP" in s and (proj / "lde.toml").exists(), s[-300:])
        e, s = text("outline")
        uid = next(ln.split("[")[1].split("]")[0] for ln in s.splitlines() if "Частота среза зависит" in ln)
        e, s = text("insert_element", after_uid=uid, src="Частота 1592 Гц достигается при R = 1000 Ом.")
        step("insert_element: проверка места сразу показывает нарушения", "набрана текстом" in s and "{=afc.f_c}" in s, s)
        e, s = text("fix")
        e, s = text("check")
        step("fix → check: замечаний нет", "Замечаний нет" in s, s)
        e, s = text("build")
        step("build", not e and "PDF" in s, s[-200:])
        e, cont = call("render_page", uid="eq:k", width=500)
        img = [x for x in cont if x["type"] == "image"]
        step("render_page: PNG страницы с элементом", img and base64.b64decode(img[0]["data"])[:4] == b"\x89PNG", cont[0])
        e, s = text("history", n=5)
        au = subprocess.run(["git", "log", "-3", "--format=%an|%s"], cwd=proj, capture_output=True, text=True).stdout
        step("каждая правка — точка агента в истории (и команды lde — от его имени)",
             all(l.startswith("Агент t (MCP)|") for l in au.splitlines()[:2]) and "Агент t (MCP)" in s, au)
        e, s = text("undo")
        e, s2 = text("read", path="text/02-model.md")
        step("undo отменяет последнюю правку агента", "Отменено" in s and "{=afc.f_c} достигается" not in s2, s)
        e, s = text("write_file", path="out/x.pdf", content="x")
        step("служебное (out/) не пишется", e)
        e, s = text("read", path="../cfg/lde/mcp.json")
        step("чтение вне проекта запрещено", e and "вне проекта" in s)

        # --- агент без MCP-клиента
        c, _, b = post(url + "/tools/list?token=" + token, {"what": "values"})
        r = json.loads(b)
        step("POST /mcp/tools/<имя> — простой вызов", c == 200 and r["ok"] and "{=afc.f_c}" in r["text"], r)
        txt = urllib.request.urlopen(link, timeout=5).read().decode()
        step("GET по ссылке — полный адрес и описание", f"http://127.0.0.1:{port}/mcp" in txt and "POST" in txt)
    finally:
        srv.terminate()
        srv.wait(10)

    # --- stdio
    p = subprocess.Popen([str(REPO / "bin" / "lde"), "mcp", "--stdio", str(proj)], env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "s"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "project_status", "arguments": {}}}]
    out, err = p.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=120)
    lines = [json.loads(x) for x in out.splitlines() if x.strip()]
    step("stdio: ответы только на запросы, версия клиента сохранена", len(lines) == 2 and lines[0]["result"]["protocolVersion"] == "2025-03-26", out[:300])
    step("stdio: проект из аргумента", "Отчёт MCP" in lines[-1]["result"]["content"][0]["text"], err[-300:])

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
