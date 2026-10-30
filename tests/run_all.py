"""Run the complete repository test suite with the standard library only.

The specification tests are included deliberately: during TDD setup they are
expected to be RED until the corresponding product behavior is implemented.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
LEGACY = [
    "test_cc.py",
    "test_backups.py",
    "test_dashboard.py",
    "test_dashboard_frontend.py",
    "test_dashboard_store_switch.py",
    "test_organisation.py",
    "test_organisation_ui.py",
    "test_organisation_edit.py",
]


def run(label, command):
    print("\n=== %s ===" % label, flush=True)
    result = subprocess.run(command, cwd=str(ROOT), text=True)
    print("=== %s: %s ===" % (label, "PASS" if result.returncode == 0 else "FAIL"), flush=True)
    return result.returncode


def main():
    failures = 0
    for name in LEGACY:
        failures += int(run(name, [sys.executable, str(TESTS / name)]) != 0)
    failures += int(
        run(
            "test_spec_hierarchy.py",
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_spec_hierarchy.py",
                "-v",
            ],
        )
        != 0
    )
    print("\n%s test command(s) failed" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
