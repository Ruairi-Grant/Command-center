"""Regression coverage for the hierarchical organisation dashboard view."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
PY = sys.executable
PORT = 8898
BASE = "http://127.0.0.1:%d" % PORT


def main():
    root = Path(tempfile.mkdtemp(prefix="cc-org-ui-"))
    prod = root / "command-center-prod"
    env = dict(os.environ, CC_HOME=str(prod), CC_PORT=str(PORT))
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

        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as response:
            state = json.loads(response.read().decode())
        org = state["organisation"]
        assert isinstance(org, list)
        assert all("children" in node and "kind" in node for node in org)

        with urllib.request.urlopen(BASE + "/", timeout=10) as response:
            html = response.read().decode()
        assert 'id="organisation"' in html
        assert 'id="nt-parent"' in html
        assert 'data-org-add' not in html
        assert "section('No date'" not in html
        assert "'next'" not in html
        print("ALL ORGANISATION UI TESTS PASSED")
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
