"""Headless Chrome через DevTools Protocol: настоящие события мыши и клавиатуры.

Окна на рабочем столе не открываются (--headless=new), профиль — во временном каталоге.
JS (Runtime.evaluate) используется только чтобы *прочитать* состояние страницы и координаты
элементов для проверок; действия — только Input.dispatch* (как у человека).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import websocket


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Chrome:
    def __init__(self, url: str, width=1500, height=950, dark=False):
        exe = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
        self.tmp = tempfile.mkdtemp(prefix="lde-ui-chrome-")
        self.port = free_port()
        self.proc = subprocess.Popen(
            [exe, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.tmp}",
             f"--window-size={width},{height}", "--no-first-run", "--no-default-browser-check", "--disable-gpu",
             "--hide-scrollbars", "--force-device-scale-factor=1", "--lang=ru", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        target = None
        while time.time() - t0 < 20:
            try:
                lst = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))
                target = next((t for t in lst if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not target:
            raise RuntimeError("headless Chrome не запустился")
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
        self.id = 0
        self.errors: list[str] = []
        self.send("Page.enable")
        self.send("Runtime.enable")
        if dark:
            self.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
        self.send("Page.navigate", {"url": url})
        self.wait("document.readyState === 'complete'")

    def send(self, method, params=None):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description", "JS error"))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS: {r['exceptionDetails'].get('exception', {}).get('description', r['exceptionDetails'])}")
        return r.get("result", {}).get("value")

    def wait(self, expr: str, timeout=30.0, what=""):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                v = self.eval(f"!!({expr})")
                if v:
                    return v
            except RuntimeError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"не дождался: {what or expr}")

    def rect(self, sel: str):
        r = self.eval(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return null;"
                      f" e.scrollIntoView({{block:'center'}}); const r = e.getBoundingClientRect();"
                      f" return [r.left, r.top, r.width, r.height]; }})()")
        if not r:
            raise AssertionError(f"нет элемента {sel}")
        return r

    def click_xy(self, x, y, count=1):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": t, "x": x, "y": y}
            if t != "mouseMoved":
                p.update({"button": "left", "clickCount": count})
            self.send("Input.dispatchMouseEvent", p)

    def click(self, sel: str, dx=0.5, dy=0.5):
        x, y, w, h = self.rect(sel)
        self.click_xy(x + w * dx, y + h * dy)

    def type(self, text: str):
        self.send("Input.insertText", {"text": text})

    def keys(self, text: str):
        """Печать посимвольно, как с клавиатуры (keyDown с text + keyUp)."""
        for ch in text:
            self.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "unmodifiedText": ch, "key": ch})
            self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})

    def key(self, key: str, code: str | None = None, mods: int = 0, vk: int | None = None):
        p = {"key": key, "code": code or key, "modifiers": mods}
        if vk:
            p["windowsVirtualKeyCode"] = vk
        self.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **p})
        self.send("Input.dispatchKeyEvent", {"type": "keyUp", **p})

    def screenshot(self, path: str):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


CTRL = 2
SHIFT = 8
ALT = 1


class StudioServer:
    """lde studio --no-browser на свободном порту (окна не открывает, «открыть папку» не выполняет)."""

    def __init__(self, project: str | None, config_dir: str | None = None):
        import tempfile
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        self.port = free_port()
        # настройки интерфейса (~/.config/lde) — во временном каталоге: тесты не пишут в ~
        self._own_cfg = config_dir is None
        self.config_dir = config_dir or tempfile.mkdtemp(prefix="lde-ui-cfg-")
        env = dict(os.environ, LDE_NO_DESKTOP="1", LDE_NO_RECENT="1", XDG_CONFIG_HOME=self.config_dir)
        args = [str(repo / "bin" / "lde"), "studio", "--no-browser", "--port", str(self.port)]
        if project:
            args.insert(2, str(project))
        self.proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.url = f"http://127.0.0.1:{self.port}/"
        t0 = time.time()
        while time.time() - t0 < 30:
            try:
                urllib.request.urlopen(self.url + "api/state", timeout=5)
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("Studio не запустился")

    def api(self, path, body=None):
        req = urllib.request.Request(self.url + path.lstrip("/"), data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=120))

    def close(self):
        if self._own_cfg:
            import shutil
            shutil.rmtree(self.config_dir, ignore_errors=True)
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
