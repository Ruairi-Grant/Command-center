"""Real-server tests for editable organisation records."""
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
PORT = 8899
BASE = "http://127.0.0.1:%d" % PORT


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main():
    root = Path(tempfile.mkdtemp(prefix="cc-org-edit-"))
    env = dict(os.environ, CC_HOME=str(root / "command-center-prod"), CC_PORT=str(PORT))
    srv = subprocess.Popen([PY, str(LIB / "cc_dashboard.py")], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(BASE + "/", timeout=2):
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise AssertionError("dashboard did not start")

        st, body = request("/api/organisation/create", {"title": "UAA", "kind": "area"})
        assert st == 201 and body["record"]["kind"] == "area"
        area_id = body["record"]["id"]
        st, body = request("/api/organisation/create", {
            "title": "Data policy", "kind": "project", "parent_id": area_id,
            "next_action": "Review stored data", "due": "2026-12-31"
        })
        assert st == 201 and body["record"]["parent_id"] == area_id
        assert body["record"]["due"] == "2026-12-31"
        child_id = body["record"]["id"]
        st, body = request("/api/organisation/create", {
            "title": "Review stored data", "kind": "task", "parent_id": child_id
        })
        assert st == 201
        task_id = body["record"]["id"]
        st, body = request("/api/organisation/update", {
            "id": task_id, "title": "Data policy review", "status": "waiting",
            "waiting_on": "Policy decision"
        })
        assert st == 200
        assert body["record"]["title"] == "Data policy review"
        assert body["record"]["waiting_on"] == "Policy decision"
        st, body = request("/api/organisation/create", {
            "title": "orphan", "kind": "task", "parent_id": "20260101-aaaaaa"
        })
        assert st == 400 and "parent" in body["error"]
        print("ALL ORGANISATION EDIT TESTS PASSED")
        return 0
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
