"""First specification-driven red tests.

These tests intentionally describe approved behavior that the current product
code does not yet implement. They are the first RED batch for the next
red-green-refactor cycles; this file must not be made green by weakening the
assertions or changing product code in the test-infrastructure phase.
"""
import sys
import os

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

    def test_HIER_005_circular_parent_move_is_rejected(self):
        area = cc.create_organisation("test", title="Area", kind="area")
        task = cc.create_organisation("test", title="Task", kind="task",
                                      parent_id=area["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", area["id"], parent_id=task["id"])
        with self.assertRaises(cc.CCError):
            cc.update_organisation("test", task["id"], parent_id=task["id"])


class LifecycleContractTests(IsolatedStoreTestCase):
    def test_LIFE_002_projects_support_active_done_cancelled(self):
        for status in ("active", "done", "cancelled"):
            record = cc.create_organisation("test", title=status,
                                            kind="project", status=status)
            self.assertEqual(record["status"], status)

    def test_LIFE_003_tasks_default_active_and_support_only_four_statuses(self):
        record = cc.create_organisation("test", title="Default task", kind="task")
        self.assertEqual(record["status"], "active")
        for status in ("active", "waiting", "done", "cancelled"):
            record = cc.create_organisation("test", title=status, kind="task",
                                            status=status)
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
