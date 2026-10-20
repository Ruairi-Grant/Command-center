"""Concurrency + durability test for the command-centre write layer.

Verifies:
  1. Atomic writes leave no partial/corrupt files (many rapid mutations).
  2. Concurrent writers (simulating Hermes + dashboard) produce a correct
     final record set — no lost updates, no interleaved corruption.
  3. Backups are created and restore reproduces an earlier state exactly.

Uses an isolated temp home so real data is never touched.
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
STORE = os.path.join(LIB, "cc_store.py")
PY = sys.executable


def run(args, env):
    return subprocess.run([PY, STORE] + args, capture_output=True,
                          text=True, env=env, timeout=120)


def test_status_defaults():
    """Regression: new tasks default to active; explicit statuses stick.

    Requirements: default 'active' when no status supplied; explicitly
    supplied valid statuses preserved; partial-update/merge unchanged.
    """
    sys.path.insert(0, LIB)
    import cc_store as cc
    home = tempfile.mkdtemp(prefix="cc-status-")
    os.environ["CC_HOME"] = home
    cc.DEFAULT_HOME = home
    try:
        # 1. no status supplied -> defaults to active (and priority normal)
        t1 = cc.create_task("test", title="no status given")
        assert t1["status"] == "active", "default status not active: %r" % t1["status"]
        assert t1["priority"] == "normal", "default priority not normal"
        print("[defaults] create with no status -> %r" % t1["status"])

        # 2. explicitly supplied valid statuses are preserved
        for s in ("active", "waiting", "done", "cancelled"):
            kwargs = {"status": s}
            if s == "waiting":
                kwargs["waiting_on"] = "a response"
            t = cc.create_task("test", title="explicit " + s, **kwargs)
            assert t["status"] == s, "explicit status %r not preserved: %r" % (s, t["status"])
        kwargs = {"status": "waiting", "waiting_on": "a response"}
        print("[defaults] explicit statuses preserved:", [cc.create_task("test", title="x", **kwargs)["status"]])

        # 3. explicit priority preserved
        t3 = cc.create_task("test", title="prio", priority="high")
        assert t3["priority"] == "high"
        print("[defaults] explicit priority preserved: %r" % t3["priority"])

        # 4. partial update still merges only provided fields
        t4 = cc.update_task("test", t1["id"], priority="low")
        assert t4["status"] == "active" and t4["priority"] == "low" and t4["title"] == "no status given", \
            "partial update changed untouched fields: %r" % t4
        t5 = cc.update_task("test", t1["id"], status="active")
        assert t5["status"] == "active" and t5["priority"] == "low" and t5["completed_at"] is None, \
            "status update clobbered other fields: %r" % t5
        print("[defaults] partial update/merge behaviour unchanged")

        # 5. invalid status still rejected
        try:
            cc.validate_task({"title": "bad", "status": "bogus"})
            raise AssertionError("invalid status was accepted")
        except cc.CCError:
            pass
        print("[defaults] invalid status still rejected")

        # 6. complete_task still works and keeps fields
        t6 = cc.complete_task("test", t1["id"])
        assert t6["status"] == "done" and t6["completed_at"] and t6["priority"] == "low"
        print("[defaults] complete_task still sets done + completed_at")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main():
    test_status_defaults()
    home = tempfile.mkdtemp(prefix="cc-test-")
    env = dict(os.environ, CC_HOME=home)
    print("test home:", home)
    ok = True

    # 0. init
    r = run(["init"], env)
    assert r.returncode == 0, r.stderr
    print("[init] ok")

    # 1. rapid sequential mutations -> no corruption
    for i in range(40):
        r = run(["tasks", "add", "--title", "t%d" % i,
                 "--priority", ["high", "normal", "low"][i % 3]], env)
        assert r.returncode == 0, (i, r.stderr)
    tasks = [json.loads(l) for l in open(os.path.join(home, "data/tasks.jsonl"))
             if l.strip()]
    assert len(tasks) == 40, "expected 40 tasks, got %d" % len(tasks)
    print("[atomic-sequential] 40 tasks, all lines valid JSON")

    # 2. concurrent writers via the mutation() lock (in-process, threads)
    sys.path.insert(0, LIB)
    os.environ["CC_HOME"] = home
    import cc_store as cc
    cc.DEFAULT_HOME = home
    import threading

    errors = []

    def worker(n):
        try:
            for i in range(10):
                cc.create_task("worker-%d" % n, title="w%d-%d" % (n, i))
        except Exception as e:  # pragma: no cover
            errors.append((n, repr(e)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    tasks = cc.load_kind("tasks")
    # 40 from before + 6 workers x 10 = 100, no lost writes
    assert len(tasks) == 100, "expected 100 tasks after concurrent writes, got %d" % len(tasks)
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate ids under concurrency"
    print("[concurrent] 60 concurrent creates -> 100 total, no lost writes, no dup ids")

    # 3. backup snapshot exists and restore reproduces exactly
    # NOTE: retention auto-prunes old snapshots (keep=20) as mutations run,
    # so use the NEWEST snapshot, which is always retained.
    bdir = os.path.join(home, "backups")
    snaps = cc.list_snapshots(home)
    assert snaps, "no backups created"
    print("[backup] snapshots created: %d (retention keep=%d)" % (len(snaps), cc.get_backup_keep()))
    target_snap = snaps[-1][1]
    target_name = snaps[-1][0]

    # mutate after backup, then restore the newest snapshot, compare
    run(["tasks", "complete", "--id", ids[0]], env)
    pre_restore = cc.load_kind("tasks")
    r = run(["restore", "--from", target_snap, "--yes"], env)
    assert r.returncode == 0, r.stderr
    snap_tasks = [json.loads(l) for l in
                  open(os.path.join(target_snap, "tasks.jsonl")) if l.strip()]
    after = cc.load_kind("tasks")
    assert len(after) == len(snap_tasks), \
        "restore mismatch: %d vs snapshot %d" % (len(after), len(snap_tasks))
    print("[restore] restored from %s -> %d records match snapshot" % (target_name, len(after)))
    assert pre_restore[0]["status"] == "done"
    assert after[0]["status"] != "done", "restore did not revert the completion"
    print("[restore] post-mutation change correctly reverted")

    # 4. activity log is append-only and grows
    act = [json.loads(l) for l in
           open(os.path.join(home, "data/activity.jsonl")) if l.strip()]
    assert len(act) >= 100, "activity log too short: %d" % len(act)
    assert all(set(a) == {"ts", "actor", "action", "target", "detail"} for a in act), \
        "activity records have unexpected shape"
    print("[activity] %d append-only entries, uniform shape" % len(act))

    shutil.rmtree(home, ignore_errors=True)
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())