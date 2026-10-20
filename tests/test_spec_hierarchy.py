"""First specification-driven red tests.

These tests intentionally describe approved behavior that the current product
code does not yet implement. They are the first RED batch for the next
red-green-refactor cycles; this file must not be made green by weakening the
assertions or changing product code in the test-infrastructure phase.
"""
import sys
import os
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_support import IsolatedStoreTestCase  # noqa: E402
import cc_store as cc  # noqa: E402


class HierarchyContractTests(IsolatedStoreTestCase):
    def test_HIER_001_area_cannot_have_parent_or_status(self):
        root = cc.create_organisation("test", title="Root", kind="area")
        with self.assertRaises(cc.CCError):
            cc.create_organisation("test", title="Nested", kind="area",
                                   parent_id=root["id"])
        with self.assertRaises(cc.CCError):
            cc.create_organisation("test", title="Status area", kind="area",
                                   status="active")

    def test_HIER_002_project_can_be_root_or_under_area_but_not_project(self):
        area = cc.create_organisation("test", title="Area", kind="area")
        cc.create_organisation("test", title="Root project", kind="project")
        parent = cc.create_organisation("test", title="Area project", kind="project",
                                        parent_id=area["id"])
        with self.assertRaises(cc.CCError):
            cc.create_organisation("test", title="Nested project", kind="project",
                                   parent_id=parent["id"])

    def test_HIER_003_task_accepts_only_allowed_parent_types(self):
        area = cc.create_organisation("test", title="Area", kind="area")
        project = cc.create_organisation("test", title="Project", kind="project")
        root_task = cc.create_organisation("test", title="Root task", kind="task")
        for title, parent_id in (
            ("Area task", area["id"]),
            ("Project task", project["id"]),
            ("Subtask", root_task["id"]),
        ):
            record = cc.create_organisation("test", title=title, kind="task",
                                             parent_id=parent_id)
            self.assertEqual(record["parent_id"], parent_id)

    def test_HIER_009_subtasks_cannot_own_subtasks(self):
        root_task = cc.create_organisation("test", title="Root task", kind="task")
        subtask = cc.create_organisation("test", title="Subtask", kind="task",
                                         parent_id=root_task["id"])
        with self.assertRaises(cc.CCError):
            cc.create_organisation("test", title="Nested subtask", kind="task",
                                   parent_id=subtask["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", root_task["id"], parent_id=subtask["id"])

    def test_HIER_005_circular_parent_move_is_rejected(self):
        area = cc.create_organisation("test", title="Area", kind="area")
        task = cc.create_organisation("test", title="Task", kind="task",
                                      parent_id=area["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", area["id"], parent_id=task["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", task["id"], parent_id=task["id"])


class LifecycleContractTests(IsolatedStoreTestCase):
    def test_LIFE_001_parent_completion_and_deletion_require_resolved_children(self):
        project = cc.create_organisation("test", title="Project", kind="project")
        child = cc.create_organisation("test", title="Child", kind="task",
                                       parent_id=project["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", project["id"], status="done")
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", project["id"], status="cancelled")
        with self.assertRaises(cc.CCError):
            cc.delete_organisation("test", project["id"])
        cc.complete_task("test", child["id"])
        done = cc.update_organisation("test", project["id"], status="done")
        self.assertEqual(done["status"], "done")
        with self.assertRaises(cc.CCError):
            cc.delete_organisation("test", project["id"])

    def test_LIFE_006_undo_restores_previous_status_for_waiting_task(self):
        task = cc.create_organisation("test", title="Blocked", kind="task",
                                      status="waiting", waiting_on="A response")
        completed = cc.complete_task("test", task["id"])
        self.assertEqual(completed["status"], "done")
        self.assertIsNotNone(completed["completed_at"])
        restored = cc.restore_task("test", task["id"])
        self.assertEqual(restored["status"], "waiting")
        self.assertEqual(restored["waiting_on"], "A response")
        self.assertIsNone(restored["completed_at"])

    def test_LIFE_007_done_and_cancelled_items_are_hidden_but_stored(self):
        done = cc.create_organisation("test", title="Done task", kind="task")
        cancelled = cc.create_organisation("test", title="Cancelled task", kind="task")
        cc.complete_task("test", done["id"])
        cc.update_organisation("test", cancelled["id"], status="cancelled")
        state = cc.build_state()
        tree_ids = set()
        def collect(nodes):
            for node in nodes:
                tree_ids.add(node["id"])
                collect(node.get("children", []))
        collect(state["organisation"])
        self.assertNotIn(done["id"], tree_ids)
        self.assertNotIn(cancelled["id"], tree_ids)
        self.assertEqual(cc._find(cc.load_kind("tasks"), done["id"])["status"], "done")
        self.assertEqual(cc._find(cc.load_kind("tasks"), cancelled["id"])["status"], "cancelled")

    def test_LIFE_008_project_completion_has_timestamp_and_can_be_undone(self):
        project = cc.create_organisation("test", title="Project", kind="project")
        completed = cc.update_organisation("test", project["id"], status="done")
        self.assertIsNotNone(completed.get("completed_at"))
        restored = cc.update_organisation("test", project["id"], status="active")
        self.assertEqual(restored["status"], "active")
        self.assertIsNone(restored.get("completed_at"))

    def test_DATE_003_waiting_tasks_are_also_in_effective_date_view(self):
        due = (cc.today() - dt.timedelta(days=1)).isoformat()
        task = cc.create_organisation("test", title="Blocked", kind="task",
                                      status="waiting", waiting_on="Response", due=due)
        state = cc.build_state()
        self.assertIn(task["id"], [t["id"] for t in state["waiting"]])
        self.assertIn(task["id"], [t["id"] for t in state["overdue"]])

    def test_LIFE_005_completion_can_be_restored(self):
        task = cc.create_organisation("test", title="Task", kind="task")
        completed = cc.complete_task("test", task["id"])
        self.assertEqual(completed["status"], "done")
        self.assertIsNotNone(completed["completed_at"])
        restored = cc.restore_task("test", task["id"])
        self.assertEqual(restored["status"], "active")
        self.assertIsNone(restored["completed_at"])

    def test_LIFE_002_projects_support_active_done_cancelled(self):
        for status in ("active", "done", "cancelled"):
            record = cc.create_organisation("test", title=status,
                                            kind="project", status=status)
            self.assertEqual(record["status"], status)

    def test_LIFE_003_tasks_default_active_and_support_only_four_statuses(self):
        record = cc.create_organisation("test", title="Default task", kind="task")
        self.assertEqual(record["status"], "active")
        for status in ("active", "waiting", "done", "cancelled"):
            fields = {"status": status}
            if status == "waiting":
                fields["waiting_on"] = "A response"
            record = cc.create_organisation("test", title=status, kind="task",
                                            **fields)
            self.assertEqual(record["status"], status)
        for obsolete in ("inbox", "someday"):
            with self.assertRaises(cc.CCError):
                cc.create_organisation("test", title=obsolete, kind="task",
                                       status=obsolete)

    def test_LIFE_004_waiting_task_requires_waiting_on(self):
        with self.assertRaises(cc.CCError):
            cc.create_organisation("test", title="Blocked", kind="task",
                                   status="waiting")
        record = cc.create_organisation("test", title="Blocked", kind="task",
                                        status="waiting", waiting_on="A response")
        self.assertEqual(record["waiting_on"], "A response")


if __name__ == "__main__":
    import unittest
    unittest.main()
