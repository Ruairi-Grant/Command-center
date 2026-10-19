"""Real-server regression coverage for the runtime PROD/DEV store switch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
PY = sys.executable
PORT = 8897
BASE = "http://127.0.0.1:%d" % PORT


def request(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def wait_up():
    deadline = time.time() + 15
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2):
                return True
        except Exception as exc:
            last = exc
            time.sleep(0.2)
    raise AssertionError("dashboard did not start: %r" % (last,))


def records(home):
    path = Path(home) / "data" / "tasks.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def start(prod):
    env = dict(os.environ, CC_HOME=str(prod), CC_PORT=str(PORT))
    return subprocess.Popen([PY, str(LIB / "cc_dashboard.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    root = Path(tempfile.mkdtemp(prefix="cc-switch-"))
    prod = root / "command-center-prod"
    dev = root / "command-center-dev"
    srv = None
    try:
        srv = start(prod)
        assert wait_up(), "dashboard did not start"
        assert request("/api/store/switch") == (200, {"store": "prod"})
        with urllib.request.urlopen(BASE + "/", timeout=10) as page_response:
            page = page_response.read().decode()
        assert 'id="store"' in page
        assert "store.addEventListener('click'" in page
        assert "JSON.stringify({store: next})" in page

        st, body = request("/api/tasks/create", "POST", {"title": "PROD marker"})
        assert st == 201 and body["record"]["title"] == "PROD marker"
        assert [r["title"] for r in records(prod)] == ["PROD marker"]
        assert records(dev) == []

        st, body = request("/api/store/switch", "POST", {"store": "dev"})
        assert st == 200 and body == {"ok": True, "store": "dev"}
        assert request("/api/store/switch") == (200, {"store": "dev"})
        st, body = request("/api/tasks/create", "POST", {"title": "DEV marker"})
        assert st == 201 and body["record"]["title"] == "DEV marker"
        assert [r["title"] for r in records(dev)] == ["DEV marker"]
        assert [r["title"] for r in records(prod)] == ["PROD marker"]

        st, body = request("/api/store/switch", "POST", {"store": "C:/outside"})
        assert st == 400 and "store" in body["error"]
        assert request("/api/store/switch") == (200, {"store": "dev"})

        srv.terminate()
        srv.wait(timeout=5)
        srv = start(prod)
        assert wait_up(), "dashboard did not restart"
        assert request("/api/store/switch") == (200, {"store": "prod"})
        print("[PASS] default PROD, runtime DEV switch, selected writes, invalid names, and restart reset")
        print("ALL STORE SWITCH TESTS PASSED")
        return 0
    finally:
        if srv is not None:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
