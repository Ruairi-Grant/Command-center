"""Real-server regression coverage for fixed per-process storage selection."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

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
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        return exc.code, json.loads(body) if body else None


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
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def start(home):
    env = dict(os.environ, CC_ENV="test", CC_DATA_DIR=str(home), CC_PORT=str(PORT))
    return subprocess.Popen(
        [PY, str(LIB / "cc_dashboard.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def main():
    root = Path(tempfile.mkdtemp(prefix="cc-fixed-store-"))
    configured = root / "configured-store"
    unexpected = root / "unexpected-store"
    srv = None
    try:
        srv = start(configured)
        assert wait_up(), "dashboard did not start"

        status, body = request("/api/store/switch")
        assert status == 404 and body == {"error": "not found"}
        status, body = request("/api/store/switch", "POST", {"store": "dev"})
        assert status == 404 and body == {"error": "not found"}
        status, body = request("/api/store/switch", "POST", {"store": str(unexpected)})
        assert status == 404 and body == {"error": "not found"}

        assert request("/health/live") == (200, {"status": "ok"})
        assert request("/health/ready") == (200, {"status": "ready"})

        with urllib.request.urlopen(BASE + "/", timeout=10) as page_response:
            page = page_response.read().decode()
        assert 'id="store"' not in page
        assert "/api/store/switch" not in page

        status, body = request("/api/tasks/create", "POST", {"title": "fixed store"})
        assert status == 201 and body["record"]["title"] == "fixed store"
        assert [r["title"] for r in records(configured)] == ["fixed store"]
        assert records(unexpected) == []

        print("[PASS] fixed configured store rejects switch and arbitrary-path requests")
        print("ALL FIXED STORE TESTS PASSED")
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
