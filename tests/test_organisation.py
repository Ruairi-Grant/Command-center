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
                                next_action="Review the data currently stored.")
        task = cc.create_task("test", title="Review stored data", kind="task",
                              parent_id=project["id"], status="next")
        question = cc.create_task("test", title="Is Notion permitted?", kind="question",
                                  parent_id=project["id"], status="waiting",
                                  waiting_on="Data policy decision")

        assert area["kind"] == "area" and area["parent_id"] is None
        assert project["kind"] == "project" and project["parent_id"] == area["id"]
        assert task["parent_id"] == project["id"] and task["status"] == "next"
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
