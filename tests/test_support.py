"""Shared helpers for the specification-driven tests.

New tests use these helpers instead of touching the operational stores. The
legacy suites remain independent while they are migrated incrementally.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import cc_store as cc  # noqa: E402


class IsolatedStoreTestCase(unittest.TestCase):
    """Give each test a fresh PROD-shaped home and reset global store state."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cc-spec-")
        self.old_home = os.environ.get("CC_HOME")
        self.old_data_dir = os.environ.get("CC_DATA_DIR")
        self.old_backup_dir = os.environ.get("CC_BACKUP_DIR")
        self.old_default = cc.DEFAULT_HOME
        os.environ["CC_HOME"] = self.home
        os.environ["CC_DATA_DIR"] = self.home
        os.environ["CC_BACKUP_DIR"] = os.path.join(self.home, "backups")
        cc.DEFAULT_HOME = self.home
        cc.init(self.home)

    def tearDown(self):
        cc.DEFAULT_HOME = self.old_default
        if self.old_home is None:
            os.environ.pop("CC_HOME", None)
        else:
            os.environ["CC_HOME"] = self.old_home
        for name, value in (
            ("CC_DATA_DIR", self.old_data_dir),
            ("CC_BACKUP_DIR", self.old_backup_dir),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.home, ignore_errors=True)
