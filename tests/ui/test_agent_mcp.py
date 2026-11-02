"""Studio → «Подключить агента (MCP)…»: ссылка, агент правит проект, открытый в окне, Studio это показывает.

    toolchain/venv/bin/python tests/ui/test_agent_mcp.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "ui"))
from cdp import Chrome, StudioServer, free_port  # noqa: E402

ok = True


def step(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    ok = ok and bool(cond)


class Agent:
    """Минимальный MCP-клиент (Streamable HTTP, JSON-ответы)."""

    def __init__(self, link):
        u = urlparse(link)
        self.url = f"{u.scheme}://{u.netloc}{u.path}"
        self.token = parse_qs(u.query)["token"][0]
        self.sess, self.n = None, 0

    def rpc(self, method, params=None):
        self.n += 1
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "Authorization": "Bearer " + self.token}
        if self.sess:
            h["Mcp-Session-Id"] = self.sess
        r = urllib.request.urlopen(urllib.request.Request(self.url, json.dumps(
            {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}).encode(), h), timeout=300)
        self.sess = r.headers.get("Mcp-Session-Id") or self.sess
        return json.loads(r.read())

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["isError"], "\n".join(c.get("text", "") for c in res["content"] if c["type"] == "text")


with tempfile.TemporaryDirectory(prefix="lde-ui-mcp-") as td:
    td = Path(td)
    cfg = td / "cfg"
    (cfg / "lde").mkdir(parents=True)
    mport = free_port()
    (cfg / "lde" / "mcp.json").write_text(json.dumps({"port": mport}), encoding="utf-8")
    root = td / "proj"
    subprocess.run([str(REPO / "bin" / "lde"), "init", str(root)], capture_output=True, check=True)
    st = StudioServer(str(root), str(cfg))
    c = None
    try:
        c = Chrome(st.url)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "сборка")
        c.click("#brand")
        c.wait("document.querySelector('#proj-menu')", 5)
        c.eval("[...document.querySelectorAll('#proj-menu .mi')].find(e => e.textContent.startsWith('Подключить агента')).click()")
        c.wait("document.querySelector('#mcp-start')", 10, "окно подключения")
        c.click("#mcp-start")
        c.wait("document.querySelector('#mcp-link')", 10, "ссылка")
        link = c.eval("document.querySelector('#mcp-link').value")
        step("ссылка постоянная, на 127.0.0.1 и с ключом", link.startswith(f"http://127.0.0.1:{mport}/mcp?token="), link)
        snip = c.eval("document.querySelector('.mcp-dialog').textContent")
        step("готовые фрагменты для DSH и Claude Code", "dsh-mcp-client" in snip and "claude mcp add" in snip)
        mcfg = json.loads((cfg / "lde" / "mcp.json").read_text(encoding="utf-8"))
        step("ключ хранится в ~/.config/lde/mcp.json (0600)",
             mcfg.get("token") and oct((cfg / "lde" / "mcp.json").stat().st_mode & 0o777) == "0o600", mcfg)
        if os.environ.get("LDE_SCREENS"):
            c.screenshot(str(Path(os.environ["LDE_SCREENS"]) / "light-13-agent-mcp.png"))

        # --- агент по ссылке
        try:
            urllib.request.urlopen(link.split("?")[0], timeout=5)
            step("без ключа — 401", False)
        except urllib.error.HTTPError as e:
            step("без ключа — 401", e.code == 401, e.code)
        txt = urllib.request.urlopen(link, timeout=5).read().decode()
        step("GET по ссылке — описание среды для агента без MCP-клиента", "LDE" in txt and "/tools/" in txt, txt[:200])
        ag = Agent(link)
        init = ag.rpc("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test-agent", "version": "1"}, "capabilities": {}})
        ins = init["result"]["instructions"]
        step("initialize: среда описана в instructions", "ГОСТ 7.32" in ins and "lde_guide" in ins, ins[:200])
        e, s = ag.call("project_status")
        step("агент видит проект, открытый в Studio", not e and str(root) in s, s[:300])
        e, s = ag.call("outline", section="1.1")
        uid = next(ln.split("[")[1].split("]")[0] for ln in s.splitlines() if "Частота среза зависит" in ln)
        e, s = ag.call("element", uid=uid)
        base = s.split("---- исходник ----\n", 1)[1]
        e, s = ag.call("edit_element", uid=uid, base_src=base, new_src=base.replace("зависит от", "определяется значениями"))
        step("правка агента записана и проверена", not e and "замечаний нет" in s, s)
        c.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Агент:'))", 10, "уведомление в Studio")
        step("Studio показала «Агент: …»", True)
        c.wait("document.querySelector('#build-state').innerText.includes('обновлён')", 120, "пересборка")
        t = (root / "text" / "02-model.md").read_text(encoding="utf-8")
        step("файл изменён", "определяется значениями" in t)
        hist = subprocess.run([str(REPO / "bin" / "lde"), "history", "-n", "3", "-C", str(root)], capture_output=True, text=True).stdout
        step("в истории — точка агента с его именем", "Агент test-agent (MCP)" in hist and "Агент: изменён элемент" in hist, hist)
        # Studio открыла другой проект → агент работает в нём
        root2 = td / "proj2"
        subprocess.run([str(REPO / "bin" / "lde"), "init", str(root2), "--title", "Второй отчёт", "--no-build"], capture_output=True, check=True)
        urllib.request.urlopen(urllib.request.Request(st.url + "api/open-project", json.dumps({"path": str(root2)}).encode(),
                                                      {"Content-Type": "application/json"}), timeout=30)
        e, s = ag.call("project_status")
        step("агент следует за проектом, открытым в Studio", "Второй отчёт" in s, s[:200])
        # окно показывает активность
        c.eval("agentDialog()")
        c.wait("document.querySelector('#mcp-activity')", 10)
        act = c.eval("document.querySelector('.mcp-dialog').textContent")
        step("окно: кто подключался и вызовы", "test-agent" in act and "edit_element" in act, act[-300:])
        # выключить → ссылка перестаёт работать
        c.eval("[...document.querySelectorAll('#dialog button')].find(b => b.textContent === 'Выключить').click()")
        time.sleep(0.8)
        try:
            urllib.request.urlopen(link, timeout=3)
            step("«Выключить» — порт закрыт", False)
        except Exception:
            step("«Выключить» — порт закрыт", True)
        step("ошибок JS нет", not c.errors, str(c.errors))
    finally:
        if c:
            c.close()
        st.close()

print("ИТОГ:", "все проверки пройдены" if ok else "есть провалы")
sys.exit(0 if ok else 1)
