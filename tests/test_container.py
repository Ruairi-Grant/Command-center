"""Black-box integration tests for the production container contract.

These tests are opt-in because ordinary Python development does not require a
Docker daemon. Run them with CC_RUN_CONTAINER_TESTS=1 and either provide
CC_CONTAINER_IMAGE or allow the test to build command-centre:integration.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

if os.environ.get("CC_RUN_CONTAINER_TESTS") != "1":
    pytest.skip("container integration tests are opt-in", allow_module_level=True)

DOCKER = shutil.which("docker")
if not DOCKER:
    pytest.skip("docker CLI is not available", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("CC_CONTAINER_IMAGE", "command-centre:integration")
EXPECTED_ARCH = os.environ.get("CC_EXPECTED_ARCH")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([DOCKER, *args], check=check, text=True, capture_output=True)


def wait_for(url: str, *, status: int = 200, timeout: float = 30) -> bytes:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read()
                if response.status == status:
                    return body
                last_error = RuntimeError(f"unexpected status {response.status}")
        except Exception as exc:  # server is expected to be unavailable during boot
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"{url} did not become ready: {last_error}")


def request(base: str, path: str, *, method: str = "GET", payload=None, origin=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Host", base.removeprefix("http://"))
    if origin is not None:
        req.add_header("Origin", origin)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_container_contract():
    tag = IMAGE
    if "CC_CONTAINER_IMAGE" not in os.environ:
        run(
            "build",
            "--tag",
            tag,
            "--build-arg",
            "VCS_REF=local-test",
            "--build-arg",
            "APP_VERSION=integration",
            str(ROOT),
        )

    if EXPECTED_ARCH:
        arch = run("image", "inspect", "--format", "{{.Architecture}}", tag).stdout.strip()
        assert arch == EXPECTED_ARCH, (arch, EXPECTED_ARCH)

    image_files = run(
        "run",
        "--rm",
        "--entrypoint",
        "python",
        tag,
        "-c",
        "import os; print('\\n'.join(os.path.join(root, name) for root, _, files in os.walk('/app') for name in files if name.endswith(('.jsonl', '.db', '.sqlite', '.sqlite3'))))",
    ).stdout.strip()
    assert image_files == ""

    with tempfile.TemporaryDirectory(prefix="cc-container-") as temp:
        root = Path(temp)
        data = root / "data"
        backups = root / "backups"
        data.mkdir(mode=0o777)
        backups.mkdir(mode=0o777)
        name = f"cc-integration-{os.getpid()}"
        run(
            "run",
            "-d",
            "--name",
            name,
            "--user",
            "10001:10001",
            "-e",
            "CC_ENV=development",
            "-e",
            "CC_CONTAINER=true",
            "-e",
            "CC_DATA_DIR=/var/lib/command-centre/data",
            "-e",
            "CC_BACKUP_DIR=/var/lib/command-centre/backups",
            "-e",
            "CC_BIND_HOST=0.0.0.0",
            "-e",
            "CC_PORT=8787",
            "-e",
            "CC_ALLOWED_HOSTS=127.0.0.1,localhost",
            "-p",
            "127.0.0.1::8787",
            "-v",
            f"{data}:/var/lib/command-centre/data",
            "-v",
            f"{backups}:/var/lib/command-centre/backups",
            tag,
        )
        try:
            port = run("port", name, "8787/tcp").stdout.strip().rsplit(":", 1)[1]
            base = f"http://127.0.0.1:{port}"
            try:
                wait_for(base + "/health/live")
            except AssertionError as exc:
                logs = run("logs", name, check=False)
                raise AssertionError(f"{exc}; container logs: {logs.stdout}{logs.stderr}") from exc
            wait_for(base + "/health/ready")

            status, body = request(
                base,
                "/api/tasks/create",
                method="POST",
                payload={"title": "container integration task"},
                origin=base,
            )
            assert status == 201, body
            task_id = body["record"]["id"]

            status, body = request(base, "/api/state", origin=base)
            assert status == 200
            assert any(item["id"] == task_id for item in body["tasks"])

            status, _ = request(
                base,
                "/api/tasks/create",
                method="POST",
                payload={"title": "should be rejected"},
                origin="http://evil.invalid",
            )
            assert status == 403

            status, _ = request(
                base,
                "/api/store/switch",
                method="POST",
                payload={"store": "dev"},
                origin=base,
            )
            assert status == 404

            uid = run("exec", name, "id", "-u").stdout.strip()
            assert uid == "10001"
            assert list(data.glob("*.jsonl")), "container did not write synthetic data"

            run("restart", name)
            wait_for(base + "/health/ready")
            status, body = request(base, "/api/state", origin=base)
            assert status == 200
            assert any(item["id"] == task_id for item in body["tasks"])
        finally:
            run("rm", "--force", name, check=False)

        replacement = f"{name}-replacement"
        run(
            "run",
            "-d",
            "--name",
            replacement,
            "--user",
            "10001:10001",
            "-e",
            "CC_ENV=development",
            "-e",
            "CC_CONTAINER=true",
            "-e",
            "CC_DATA_DIR=/var/lib/command-centre/data",
            "-e",
            "CC_BACKUP_DIR=/var/lib/command-centre/backups",
            "-e",
            "CC_BIND_HOST=0.0.0.0",
            "-e",
            "CC_PORT=8787",
            "-e",
            "CC_ALLOWED_HOSTS=127.0.0.1,localhost",
            "-p",
            "127.0.0.1::8787",
            "-v",
            f"{data}:/var/lib/command-centre/data",
            "-v",
            f"{backups}:/var/lib/command-centre/backups",
            tag,
        )
        try:
            replacement_port = run("port", replacement, "8787/tcp").stdout.strip().rsplit(":", 1)[1]
            replacement_base = f"http://127.0.0.1:{replacement_port}"
            wait_for(replacement_base + "/health/ready")
            status, body = request(replacement_base, "/api/state", origin=replacement_base)
            assert status == 200
            assert any(item["title"] == "container integration task" for item in body["tasks"])
        finally:
            run("rm", "--force", replacement, check=False)

    invalid = run(
        "run",
        "--rm",
        "-e",
        "CC_ENV=production",
        "-e",
        "CC_CONTAINER=true",
        "-e",
        "CC_BIND_HOST=0.0.0.0",
        "-e",
        "CC_REQUIRE_TS_IDENTITY=true",
        "-e",
        "CC_EXPECTED_TS_LOGIN=synthetic@example.invalid",
        tag,
        check=False,
    )
    assert invalid.returncode != 0
    assert "existing directory" in invalid.stderr
