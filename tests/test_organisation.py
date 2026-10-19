"""Regression tests for hierarchical work-item organisation."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import cc_store as cc


def main():
    home = tempfile.mkdtemp(prefix="cc-org-")
    old = os.environ.get("CC_HOME")
    os.environ["CC_HOME"] = home
    cc.STORE_OVERRIDE["home"] = None
    try:
        cc.init()
        area = cc.create_goal("test", title="UAA", kind="area")
        project = cc.create_goal("test", title="Data policy", kind="project",
                                parent_id=area["id"],
                                done_when="Governance approach is documented.",
                                next_action="Review the data currently stored.",
                                due="2026-12-31")
        task = cc.create_task("test", title="Review stored data", kind="task",
                              parent_id=project["id"], status="inbox")
        explicit_task = cc.create_task("test", title="Deadline override", kind="task",
                                       parent_id=project["id"], status="inbox",
                                       due="2026-10-25")
        question = cc.create_task("test", title="Is Notion permitted?", kind="question",
                                  parent_id=project["id"], status="waiting",
                                  waiting_on="Data policy decision")

        assert area["kind"] == "area" and area["parent_id"] is None
        assert project["kind"] == "project" and project["parent_id"] == area["id"]
        assert project["due"] == "2026-12-31"
        assert task["parent_id"] == project["id"] and task["status"] == "inbox"
        state = cc.build_state()
        tree_task = next(n for n in state["organisation"][0]["children"][0]["children"]
                         if n["title"] == "Review stored data")
        assert tree_task["due"] == "2026-12-31" and tree_task["due_source"] == "inherited"
        tree_override = next(n for n in state["organisation"][0]["children"][0]["children"]
                             if n["title"] == "Deadline override")
        assert tree_override["due"] == "2026-10-25" and tree_override["due_source"] == "explicit"
        state_task = next(t for t in state["no_date_tasks"] + state["upcoming_tasks"]
                          if t["id"] == explicit_task["id"])
        assert state_task["due"] == "2026-10-25" and state_task["due_source"] == "explicit"
        assert question["kind"] == "question" and question["waiting_on"] == "Data policy decision"

        try:
            cc.create_task("test", title="orphan", parent_id="20260101-aaaaaa")
        except cc.CCError:
            pass
        else:
            raise AssertionError("orphan parent was accepted")
        print("ALL ORGANISATION TESTS PASSED")
        return 0
    finally:
        if old is None:
            os.environ.pop("CC_HOME", None)
        else:
            os.environ["CC_HOME"] = old
        cc.STORE_OVERRIDE["home"] = None
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
