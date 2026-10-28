"""Regression tests for hierarchical work-item organisation."""
import calendar
import os
import shutil
import sys
import tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import cc_store as cc


def months_from_today(months):
    today = date.today()
    total_months = today.year * 12 + today.month - 1 + months
    year, month_zero_based = divmod(total_months, 12)
    month = month_zero_based + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def one_month_from_today():
    return months_from_today(1)


def main():
    home = tempfile.mkdtemp(prefix="cc-org-")
    old = os.environ.get("CC_HOME")
    os.environ["CC_HOME"] = home
    cc.STORE_OVERRIDE["home"] = None
    try:
        cc.init()
        area = cc.create_goal("test", title="UAA", kind="area")
        project_due = months_from_today(2)
        project = cc.create_goal("test", title="Data policy", kind="project",
                                parent_id=area["id"],
                                done_when="Governance approach is documented.",
                                next_action="Review the data currently stored.",
                                due=project_due)
        explicit_due = one_month_from_today()
        task = cc.create_task("test", title="Review stored data", kind="task",
                              parent_id=project["id"], status="active")
        explicit_task = cc.create_task("test", title="Deadline override", kind="task",
                                       parent_id=project["id"], status="active",
                                       due=explicit_due)
        waiting_task = cc.create_task("test", title="Is Notion permitted?", kind="task",
                                      parent_id=project["id"], status="waiting",
                                      waiting_on="Data policy decision")

        assert area["kind"] == "area" and area["parent_id"] is None
        assert project["kind"] == "project" and project["parent_id"] == area["id"]
        assert project["due"] == project_due
        assert task["parent_id"] == project["id"] and task["status"] == "active"
        state = cc.build_state()
        tree_task = next(n for n in state["organisation"][0]["children"][0]["children"]
                         if n["title"] == "Review stored data")
        assert tree_task["due"] == project_due and tree_task["due_source"] == "inherited"
        tree_override = next(n for n in state["organisation"][0]["children"][0]["children"]
                             if n["title"] == "Deadline override")
        assert tree_override["due"] == explicit_due and tree_override["due_source"] == "explicit"
        date_view_ids = {
            item["id"]
            for section in ("overdue", "today_tasks", "upcoming_tasks", "no_date_tasks")
            for item in state[section]
        }
        assert explicit_task["id"] not in date_view_ids
        assert waiting_task["kind"] == "task" and waiting_task["waiting_on"] == "Data policy decision"

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
