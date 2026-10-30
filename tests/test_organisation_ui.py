"""Regression coverage for the hierarchical organisation dashboard view."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
PY = sys.executable
PORT = 8898
BASE = "http://127.0.0.1:%d" % PORT


def post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode())


def find_node(nodes, node_id):
    for node in nodes:
        if node["id"] == node_id:
            return node
        found = find_node(node.get("children", []), node_id)
        if found:
            return found
    return None


def main():
    root = Path(tempfile.mkdtemp(prefix="cc-org-ui-"))
    prod = root / "command-center-prod"
    env = dict(
        os.environ,
        CC_HOME=str(prod),
        CC_DATA_DIR=str(prod),
        CC_BACKUP_DIR=str(prod / "backups"),
        CC_PORT=str(PORT),
    )
    srv = subprocess.Popen(
        [PY, str(LIB / "cc_dashboard.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
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
        assert state["organisation"] == []

        status, area_result = post_json(
            BASE + "/api/organisation/create",
            {"kind": "area", "title": "Notes area", "notes": "Area note"},
        )
        assert status == 201
        area_id = area_result["record"]["id"]
        status, project_result = post_json(
            BASE + "/api/organisation/create",
            {
                "kind": "project",
                "title": "Notes project",
                "parent_id": area_id,
                "notes": "Project note",
            },
        )
        assert status == 201
        project_id = project_result["record"]["id"]
        status, task_result = post_json(
            BASE + "/api/organisation/create",
            {"kind": "task", "title": "Notes task", "parent_id": project_id, "notes": "Task note"},
        )
        assert status == 201
        task_id = task_result["record"]["id"]

        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as response:
            state = json.loads(response.read().decode())
        org = state["organisation"]
        assert isinstance(org, list)
        area_node = find_node(org, area_id)
        project_node = find_node(org, project_id)
        task_node = find_node(org, task_id)
        assert area_node["notes"] == "Area note"
        assert project_node["notes"] == "Project note"
        assert task_node["notes"] == "Task note"
        status, updated = post_json(
            BASE + "/api/organisation/update", {"id": task_id, "notes": "Updated task note"}
        )
        assert status == 200 and updated["record"]["notes"] == "Updated task note"
        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as response:
            state = json.loads(response.read().decode())
        assert find_node(state["organisation"], task_id)["notes"] == "Updated task note"
        status, waiting_result = post_json(
            BASE + "/api/organisation/create",
            {
                "kind": "task",
                "title": "Waiting task",
                "parent_id": project_id,
                "status": "waiting",
                "waiting_on": "CPR clinic",
                "follow_up_on": "2099-01-02",
            },
        )
        assert status == 201
        waiting_id = waiting_result["record"]["id"]
        assert waiting_result["record"]["waiting_since"]
        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as response:
            state = json.loads(response.read().decode())
        waiting_node = find_node(state["organisation"], waiting_id)
        assert waiting_node["follow_up_on"] == "2099-01-02"
        assert waiting_node["waiting_on"] == "CPR clinic"
        assert waiting_id in [item["id"] for item in state["waiting_followups"]]
        status, resumed = post_json(
            BASE + "/api/organisation/update", {"id": waiting_id, "status": "active"}
        )
        assert status == 200
        assert resumed["record"]["waiting_on"] is None
        assert resumed["record"]["follow_up_on"] is None
        assert resumed["record"]["waiting_since"] is None

        status, done_child_result = post_json(
            BASE + "/api/organisation/create",
            {
                "kind": "task",
                "title": "Completed child",
                "parent_id": project_id,
                "notes": "Completed note",
            },
        )
        assert status == 201
        done_child_id = done_child_result["record"]["id"]
        status, _ = post_json(BASE + "/api/tasks/complete", {"id": done_child_id})
        assert status == 200
        with urllib.request.urlopen(BASE + "/api/state", timeout=10) as response:
            state = json.loads(response.read().decode())
        project_node = find_node(state["organisation"], project_id)
        child_headers = {child["id"]: child for child in project_node["child_headers"]}
        assert child_headers[done_child_id]["title"] == "Completed child"
        assert child_headers[done_child_id]["status"] == "done"
        assert all("children" in node and "kind" in node for node in org)

        with urllib.request.urlopen(BASE + "/", timeout=10) as response:
            html = response.read().decode()
        assert 'id="organisation"' in html
        assert 'id="nt-parent"' in html
        assert 'id="org-add"' in html
        assert 'id="nt-kind"' in html
        assert 'id="nt-id"' in html
        assert 'id="org-editor"' not in html
        assert 'id="org-parent-select"' not in html
        assert 'id="nt-due"' in html
        assert "section('No date'" not in html
        assert "'next'" not in html
        assert "Next action" not in html
        assert 'id="org-next"' not in html
        assert 'class="org-next"' not in html
        assert "due_source" in html
        assert "startUnifiedEdit" in html
        assert (
            "const url = editing ? '/api/organisation/update' : '/api/organisation/create'" in html
        )
        assert "ntSubmit.textContent = 'Save changes'" in html
        assert "orgEditor" not in html
        assert 'id="org-due"' not in html
        assert "const isSubtask" in html
        assert 'id="nt-follow-up"' in html
        assert "data-org-wait" in html
        assert "data-org-resume" in html
        assert "waiting_followups" in html
        assert "follow_up_on" in html
        assert "Waiting since" in html
        assert "Mark waiting" in html
        assert "Resume" in html
        assert 'id="org-detail"' in html
        assert 'id="org-detail-notes"' in html
        assert "data-org-open" in html
        assert "openOrganisationDetail" in html
        assert "Children" in html
        assert "child_headers" in html
        assert "data-org-detail-child" in html
        render_start = html.index("function renderOrganisation(")
        render_end = html.index("function buildParentOptions", render_start)
        assert "notes" not in html[render_start:render_end]
        org_click_start = html.index("orgTree.addEventListener('click'")
        org_click_end = html.index("async function load()", org_click_start)
        org_click_src = html[org_click_start:org_click_end]
        assert "data-org-open" in org_click_src
        assert "ev.stopPropagation()" in org_click_src
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
