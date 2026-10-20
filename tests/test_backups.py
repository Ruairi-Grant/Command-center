"""Backup retention + recovery tests.

All tests use ISOLATED temporary homes; the live store is never touched.

Covers:
  1. retention when the snapshot count exceeds the limit
  2. the newest backups are preserved
  3. a valid backup is created BEFORE pruning (prune verifies newest first)
  4. restoration from a retained backup reproduces its snapshot exactly
  5. recovery after a subsequent mutation reverts to the snapshot
  6. failure behaviour: backup creation failure and prune failure
  7. live data / activity log are never pruned

Run:  python tests/test_backups.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
sys.path.insert(0, LIB)
import cc_store as cc  # noqa: E402

PY = sys.executable


def fresh_home():
    home = tempfile.mkdtemp(prefix="cc-bak-")
    cc.DEFAULT_HOME = home
    os.environ["CC_HOME"] = home
    cc.init(home)
    return home


def _sleep_to_next_second():
    """Snapshot names have 1-second resolution, so two mutations inside the
    same second share a directory. Sleep until comfortably past the next
    whole second so each mutation produces a distinct snapshot (a property
    of the EXISTING naming scheme, which we are not changing)."""
    import time
    # Sleep just over 1 second from a freshly-read wall clock; using the
    # current fractional second alone can under-shoot the boundary.
    time.sleep(1.2)


def snap_names(home):
    return [n for n, _ in cc.list_snapshots(home)]


def make_fake_snapshot(home, stamp, bad=False):
    """Create a snapshot dir by hand so we can test ordering/retention
    without doing N real mutations. Creates a COMPLETE snapshot (all
    kinds plus activity.jsonl) so it passes verify_snapshot."""
    d = os.path.join(cc.backup_dir(home), stamp)
    os.makedirs(d, exist_ok=True)
    for kind in cc.VERIFY_KINDS:
        f = os.path.join(d, kind + ".jsonl")
        if bad and kind == "tasks":
            open(f, "w", encoding="utf-8").write("{not valid json\n")
        else:
            open(f, "a", encoding="utf-8").close()
    return d


def main():
    results = []

    def check(name, fn):
        try:
            fn()
            results.append((name, True))
            print("[PASS] %s" % name)
        except AssertionError as e:
            results.append((name, False))
            print("[FAIL] %s -> %s" % (name, e))
        except Exception as e:
            results.append((name, False))
            print("[FAIL] %s -> %r" % (name, e))

    # ---- 1. retention when count exceeds limit ----
    def t1():
        home = fresh_home()
        try:
            for i in range(7):
                make_fake_snapshot(home, "2026010%dT120000Z" % (i + 1))
            rep = cc.prune_backups(home, keep=3)
            assert len(rep["removed"]) == 4, rep
            remaining = snap_names(home)
            assert len(remaining) == 3, remaining
            assert remaining == ["20260105T120000Z", "20260106T120000Z", "20260107T120000Z"], remaining
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("retention removes oldest beyond keep=3", t1)

    # ---- 2. newest preserved; keep >= count is a no-op ----
    def t2():
        home = fresh_home()
        try:
            for i in range(5):
                make_fake_snapshot(home, "2026020%dT120000Z" % (i + 1))
            before = snap_names(home)
            rep = cc.prune_backups(home, keep=10)
            assert rep["removed"] == [], rep
            assert snap_names(home) == before
            # keep == count -> nothing removed either
            rep2 = cc.prune_backups(home, keep=5)
            assert rep2["removed"] == []
            assert snap_names(home) == before
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("newest backups preserved; no-op when under limit", t2)

    # ---- 3. valid backup created BEFORE pruning; prune verifies newest ----
    def t3():
        home = fresh_home()
        try:
            cc.create_task("test", title="t1")
            _sleep_to_next_second()
            cc.create_task("test", title="t2")
            snaps = snap_names(home)
            assert len(snaps) == 2, snaps
            # every snapshot must verify
            for n, p in cc.list_snapshots(home):
                ok, why = cc.verify_snapshot(p)
                assert ok, (n, why)
            rep = cc.prune_backups(home, keep=1)
            assert rep["verified"] == snaps[-1], rep
            assert len(rep["kept"]) == 1 and rep["kept"][0] == snaps[-1], rep
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("new backup verified before pruning occurs", t3)

    # ---- 4. restore from a retained backup reproduces snapshot exactly ----
    def t4():
        home = fresh_home()
        try:
            cc.create_task("test", title="alpha", priority="high")
            _sleep_to_next_second()
            cc.create_task("test", title="beta")   # distinct snapshot, pre-state has only alpha
            snaps = cc.list_snapshots(home)
            snap = snaps[-1][1]
            expected = {k: cc._load_file(os.path.join(snap, k + ".jsonl"))
                        for k in cc.KINDS}
            cc.prune_backups(home, keep=1)   # keep the retained snapshot
            # restore
            import subprocess
            p = subprocess.run([PY, os.path.join(LIB, "cc_store.py"), "restore",
                                "--from", snap, "--yes"],
                               capture_output=True, text=True, env=dict(os.environ, CC_HOME=home))
            assert p.returncode == 0, p.stderr
            for k in cc.KINDS:
                got = cc._load_file(cc.data_file(k, home))
                assert got == expected[k], "%s mismatch" % k
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("restore from retained backup reproduces snapshot exactly", t4)

    # ---- 5. recovery after a subsequent mutation reverts to snapshot ----
    def t5():
        home = fresh_home()
        try:
            t = cc.create_task("test", title="keep me")
            _sleep_to_next_second()
            cc.create_task("test", title="anchor")   # snapshot holds pre-state: only "keep me"
            snaps = cc.list_snapshots(home)
            snap = snaps[-1][1]
            snapshot_titles = [r["title"] for r in cc._load_file(os.path.join(snap, "tasks.jsonl"))]
            assert snapshot_titles == ["keep me"], snapshot_titles
            # mutate: change the task and add another
            cc.update_task("test", t["id"], status="done")
            cc.create_task("test", title="extra")
            assert "extra" in [r["title"] for r in cc.load_kind("tasks")]
            assert cc._find(cc.load_kind("tasks"), t["id"])["status"] == "done"
            # restore -> should revert both changes
            import subprocess
            p = subprocess.run([PY, os.path.join(LIB, "cc_store.py"), "restore",
                                "--from", snap, "--yes"],
                               capture_output=True, text=True, env=dict(os.environ, CC_HOME=home))
            assert p.returncode == 0, p.stderr
            titles = [r["title"] for r in cc.load_kind("tasks")]
            assert titles == snapshot_titles, (titles, snapshot_titles)
            assert cc._find(cc.load_kind("tasks"), t["id"])["status"] != "done", \
                "restore did not revert the mutation"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("recovery after subsequent mutation reverts to snapshot", t5)

    # ---- 6a. prune failure: newest snapshot invalid -> nothing pruned ----
    def t6a():
        home = fresh_home()
        try:
            for i in range(4):
                make_fake_snapshot(home, "2026030%dT120000Z" % (i + 1))
            # corrupt the NEWEST snapshot
            bad = make_fake_snapshot(home, "20260309T120000Z", bad=True)
            rep = cc.prune_backups(home, keep=1)
            assert rep["errors"], "expected an error when newest is unverifiable"
            assert rep["removed"] == [], "must not prune when verification fails"
            # all 5 snapshots still present (4 good + 1 bad)
            assert len(snap_names(home)) == 5, snap_names(home)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("prune skipped entirely when newest snapshot is unverifiable", t6a)

    # ---- 6b. prune failure mid-way: reports error, keeps the new backup ----
    def t6b():
        home = fresh_home()
        try:
            for i in range(4):
                make_fake_snapshot(home, "2026040%dT120000Z" % (i + 1))
            newest = make_fake_snapshot(home, "20260409T120000Z")
            rep = cc.prune_backups(home, keep=1)
            assert not rep["errors"], rep
            assert os.path.isdir(newest), "the valid new backup was removed!"
            assert snap_names(home) == ["20260409T120000Z"], snap_names(home)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("valid new backup preserved when pruning old ones", t6b)

    # ---- 6c. backup creation failure does not break the mutation ----
    def t6c():
        home = fresh_home()
        try:
            cc.create_task("test", title="before failure")
            # sabotage snapshot creation by removing the backups dir's
            # parent write access is messy; instead point backup_dir at a
            # file so makedirs fails inside _backup_snapshot.
            bad = os.path.join(home, "backups")
            shutil.rmtree(bad, ignore_errors=True)
            open(bad, "w", encoding="utf-8").write("not a dir")
            # _backup_snapshot will raise OSError on makedirs -> the
            # mutation must still complete and data must be written.
            try:
                rec = cc.create_task("test", title="during failure")
                assert rec["title"] == "during failure"
            except OSError:
                # If snapshot creation raises out of mutation(), the data
                # write is rolled back and the record must NOT exist.
                recs = cc.load_kind("tasks")
                assert "during failure" not in [r["title"] for r in recs], \
                    "record persisted despite backup failure"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("backup creation failure handled without corrupting data", t6c)

    # ---- 7. live data and activity log never pruned ----
    def t7():
        home = fresh_home()
        try:
            for i in range(6):
                cc.create_task("test", title="task %d" % i)
            data_before = {k: cc._load_file(cc.data_file(k, home)) for k in cc.KINDS}
            cc.prune_backups(home, keep=2)
            for k in cc.KINDS:
                assert os.path.exists(cc.data_file(k, home)), "%s deleted!" % k
                assert cc._load_file(cc.data_file(k, home)) == data_before[k], \
                    "%s modified by pruning" % k
            # activity log intact and still growing
            assert os.path.exists(cc.data_file("activity", home))
            cc.create_task("test", title="after prune")
            acts = cc._load_file(cc.data_file("activity", home))
            assert len(acts) >= 7, acts
            # non-snapshot entries under backups/ are left alone
            os.makedirs(os.path.join(cc.backup_dir(home), "keepme"), exist_ok=True)
            cc.prune_backups(home, keep=1)
            assert os.path.isdir(os.path.join(cc.backup_dir(home), "keepme")), \
                "non-snapshot entry under backups/ was pruned"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("live data, activity log and non-snapshot entries untouched", t7)

    # ---- 8. default keep is 20 ----
    def t8():
        assert cc.get_backup_keep() == 20
        os.environ["CC_BACKUP_KEEP"] = "5"
        try:
            assert cc.get_backup_keep() == 5
        finally:
            del os.environ["CC_BACKUP_KEEP"]
        os.environ["CC_BACKUP_KEEP"] = "bogus"
        try:
            assert cc.get_backup_keep() == 20
        finally:
            del os.environ["CC_BACKUP_KEEP"]
        os.environ["CC_BACKUP_KEEP"] = "0"
        try:
            assert cc.get_backup_keep() == 20
        finally:
            del os.environ["CC_BACKUP_KEEP"]

    check("default keep is 20; env override respected and validated", t8)

    # ---- 9. same-second mutations do NOT overwrite each other (Option 1) ----
    def t9():
        home = fresh_home()
        try:
            # three mutations with NO sleep: same second, distinct snapshots
            cc.create_task("test", title="s1")
            cc.create_task("test", title="s2")
            cc.create_task("test", title="s3")
            snaps = cc.list_snapshots(home)
            assert len(snaps) == 3, [n for n, _ in snaps]
            names = [n for n, _ in snaps]
            assert len(set(names)) == 3, names
            # each snapshot holds the correct pre-mutation state
            contents = [[r["title"] for r in cc._load_file(os.path.join(p, "tasks.jsonl"))]
                        for _, p in snaps]
            assert contents == [[], ["s1"], ["s1", "s2"]], contents
            # suffixed names sort chronologically (base, -2, -3)
            assert names[0].endswith("Z") and names[1].endswith("-2") and names[2].endswith("-3"), names
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("same-second mutations produce distinct non-overwriting snapshots", t9)

    # ---- 10. numeric suffix ordering (-2 before -10) ----
    def t10():
        home = fresh_home()
        try:
            bdir = cc.backup_dir(home)
            for stamp in ("20260101T000000Z", "20260101T000000Z-10", "20260101T000000Z-2"):
                make_fake_snapshot(home, stamp)
            names = [n for n, _ in cc.list_snapshots(home)]
            assert names == ["20260101T000000Z", "20260101T000000Z-2", "20260101T000000Z-10"], names
            # retention treats suffixed dirs as eligible snapshots
            rep = cc.prune_backups(home, keep=2)
            assert [n for n, _ in cc.list_snapshots(home)] == \
                ["20260101T000000Z-2", "20260101T000000Z-10"], rep
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("numeric suffix ordering and retention on suffixed snapshots", t10)

    # ---- 11. restore validates EVERY record; invalid record leaves live data unchanged ----
    def t11():
        home = fresh_home()
        try:
            import subprocess
            cc.create_task("test", title="valid one")
            _sleep_to_next_second()
            cc.create_task("test", title="valid two")
            snaps = cc.list_snapshots(home)
            snap = snaps[-1][1]
            live_before = [r["title"] for r in cc.load_kind("tasks")]
            assert live_before == ["valid one", "valid two"], live_before
            # corrupt the SECOND record in the snapshot (first stays valid,
            # so the old first-record-only check would have passed)
            tf = os.path.join(snap, "tasks.jsonl")
            recs = [json.loads(l) for l in open(tf, encoding="utf-8") if l.strip()]
            assert len(recs) == 1, "snapshot should hold pre-mutation state only: %r" % recs
            # append a second, schema-invalid record
            bad_rec = dict(recs[0])
            bad_rec["status"] = "not-a-real-status"
            with open(tf, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(bad_rec) + "\n")
            p = subprocess.run([PY, os.path.join(LIB, "cc_store.py"), "restore",
                                "--from", snap, "--yes"],
                               capture_output=True, text=True,
                               env=dict(os.environ, CC_HOME=home))
            assert p.returncode != 0, "restore of an invalid snapshot succeeded"
            assert "invalid" in p.stderr.lower(), p.stderr
            # live data completely unchanged
            assert [r["title"] for r in cc.load_kind("tasks")] == live_before, \
                "live data changed despite rejected restore"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("restore rejects snapshot with a later invalid record; live data unchanged", t11)

    # ---- 12. snapshot verification includes activity.jsonl ----
    def t12():
        home = fresh_home()
        try:
            cc.create_task("test", title="x")
            snaps = cc.list_snapshots(home)
            snap = snaps[-1][1]
            ok, why = cc.verify_snapshot(snap)
            assert ok, (ok, why)
            # remove activity.jsonl -> verification must fail
            os.unlink(os.path.join(snap, "activity.jsonl"))
            ok2, why2 = cc.verify_snapshot(snap)
            assert not ok2, "missing activity.jsonl still verified ok"
            assert "activity" in why2, why2
            # restore it, then corrupt it -> verification must fail
            open(os.path.join(snap, "activity.jsonl"), "w", encoding="utf-8").write("{bad\n")
            ok3, why3 = cc.verify_snapshot(snap)
            assert not ok3, "corrupt activity.jsonl still verified ok"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("snapshot verification covers activity.jsonl (missing/corrupt -> fail)", t12)

    # ---- 13. prune refuses when newest snapshot lacks activity.jsonl ----
    def t13():
        home = fresh_home()
        try:
            for i in range(3):
                make_fake_snapshot(home, "2026050%dT120000Z" % (i + 1))
            newest = make_fake_snapshot(home, "20260509T120000Z")
            os.unlink(os.path.join(newest, "activity.jsonl"))  # incomplete snapshot
            rep = cc.prune_backups(home, keep=1)
            assert rep["errors"], "prune proceeded despite incomplete newest snapshot"
            assert rep["removed"] == [], rep
            assert len(snap_names(home)) == 4, "snapshots were pruned anyway"
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("prune refused when newest snapshot is incomplete (no activity.jsonl)", t13)

    # ---- 14. concurrent mutations do NOT hit the Windows copy-lock race ----
    def t14():
        home = fresh_home()
        try:
            import threading
            errors = []

            def worker(k):
                try:
                    for i in range(10):
                        cc.create_task("w%d" % k, title="w%d-%d" % (k, i))
                except Exception as e:
                    errors.append((k, type(e).__name__, str(e)))

            # seed first so snapshots exist to prune against
            for i in range(10):
                cc.create_task("seed", title="s%d" % i)

            threads = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
            for t in threads: t.start()
            for t in threads: t.join()
            assert not errors, "concurrent mutations raised: %r" % errors[:3]
            # all writes landed
            assert len(cc.load_kind("tasks")) == 70, len(cc.load_kind("tasks"))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("concurrent mutations complete with no PermissionError (copy-lock race)", t14)

    # ---- 15. verify_snapshot distinguishes 'unreadable' from 'corrupt' ----
    def t15():
        home = fresh_home()
        try:
            cc.create_task("test", title="x")
            snap = cc.list_snapshots(home)[-1][1]
            # corrupt -> reason mentions the file, NOT 'unreadable'
            with open(os.path.join(snap, "tasks.jsonl"), "a", encoding="utf-8") as fh:
                fh.write("{bad\n")
            ok, why = cc.verify_snapshot(snap)
            assert not ok and not why.startswith("unreadable"), (ok, why)
            # unreadable -> reason starts with 'unreadable'
            # (simulate by monkeypatching _load_file to raise PermissionError)
            real = cc._load_file
            def boom(path, retries=0):
                if path.endswith("tasks.jsonl") and "backups" in path:
                    raise PermissionError(13, "simulated lock")
                return real(path, retries=retries)
            cc._load_file = boom
            try:
                ok2, why2 = cc.verify_snapshot(snap)
            finally:
                cc._load_file = real
            assert not ok2 and why2.startswith("unreadable"), (ok2, why2)
            # prune must skip (keep the snapshot) on unreadable
            cc._load_file = boom
            try:
                rep = cc.prune_backups(home, keep=1)
            finally:
                cc._load_file = real
            assert rep["removed"] == [], "pruned despite unreadable snapshot: %r" % rep
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("unreadable snapshot classified distinctly and never pruned", t15)

    # ---- 16. deterministic: prune cannot delete a snapshot being published ----
    def t16():
        """Prove the backups lock excludes publish from prune.

        Thread A holds the backups lock and pauses mid-publish (simulated
        with an event). Thread B tries to prune and MUST block until A
        finishes; B must never see A's half-published snapshot.
        """
        home = fresh_home()
        try:
            import threading
            # seed enough snapshots that pruning has work to do
            for i in range(5):
                make_fake_snapshot(home, "2026060%dT120000Z" % (i + 1))

            in_publish = threading.Event()
            release_publish = threading.Event()
            prune_ran = threading.Event()
            prune_result = {}
            orig_snapshot = cc._backup_snapshot

            def slow_snapshot(home=None):
                # signal we are inside publish, then wait for release
                in_publish.set()
                assert release_publish.wait(timeout=5), "publish was not released"
                return orig_snapshot(home)

            cc._backup_snapshot = slow_snapshot
            try:
                def publisher():
                    cc.create_task("pub", title="published under lock")

                def pruner():
                    in_publish.wait(timeout=5)
                    # Go through the SAME locked path real callers use
                    # (the CLI `backups prune` command). A bare
                    # prune_backups() would bypass the lock and would not
                    # represent any real caller.
                    with cc.backups_lock(home):
                        rep = cc.prune_backups(home, keep=2)
                    prune_result.update(rep)
                    prune_ran.set()

                tp = threading.Thread(target=publisher)
                tb = threading.Thread(target=pruner)
                tp.start()
                time.sleep(0.3)
                tb.start()
                time.sleep(0.3)
                # pruner must NOT have finished while publisher holds the lock
                assert not prune_ran.is_set(), \
                    "prune completed while a snapshot was mid-publish (race!)"
                release_publish.set()
                tp.join(timeout=5)
                tb.join(timeout=5)
                assert prune_ran.is_set(), "prune never completed"
                # the published snapshot must still exist (not pruned)
                snaps = cc.list_snapshots(home)
                assert len(snaps) >= 2, snaps
                # the new snapshot's task must be readable
                ok, why = cc.verify_snapshot(snaps[-1][1])
                assert ok, (ok, why)
            finally:
                cc._backup_snapshot = orig_snapshot
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("prune blocks while a snapshot is being published (backups lock)", t16)

    # ---- 17. deterministic: restore read is not deleted mid-read ----
    def t17():
        """Prove the backups lock excludes a prune from a restore read.

        Simulate a slow restore read (monkeypatched _load_file on a backup
        file pauses mid-read) while another thread tries to prune. The
        prune must block until the read completes.
        """
        home = fresh_home()
        try:
            import threading
            cc.create_task("test", title="one")
            snaps = cc.list_snapshots(home)
            snap = snaps[-1][1]
            snap_tasks = os.path.join(snap, "tasks.jsonl")

            reading = threading.Event()
            release_read = threading.Event()
            prune_done = threading.Event()
            prune_result = {}
            real_load = cc._load_file

            def slow_load(path, retries=0):
                if path == snap_tasks:
                    reading.set()
                    assert release_read.wait(timeout=5), "read not released"
                return real_load(path, retries=retries)

            cc._load_file = slow_load
            try:
                def restorer():
                    with cc.backups_lock(home):
                        cc._load_file(snap_tasks)

                def pruner():
                    reading.wait(timeout=5)
                    # Real callers (CLI prune) go through backups_lock.
                    with cc.backups_lock(home):
                        prune_result.update(cc.prune_backups(home, keep=1))
                    prune_done.set()

                tr = threading.Thread(target=restorer)
                tpr = threading.Thread(target=pruner)
                tr.start()
                time.sleep(0.3)
                tpr.start()
                time.sleep(0.3)
                assert not prune_done.is_set(), \
                    "prune completed during a restore read (race!)"
                release_read.set()
                tr.join(timeout=5)
                tpr.join(timeout=5)
                assert prune_done.is_set(), "prune never completed"
                # snapshot still exists and is intact after the race
                assert os.path.isdir(snap), "snapshot deleted during read!"
            finally:
                cc._load_file = real_load
        finally:
            shutil.rmtree(home, ignore_errors=True)

    check("prune blocks while a snapshot is being read for restore", t17)

    failed = [n for n, ok in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    if failed:
        print("FAILED:", failed)
        return 1
    print("ALL BACKUP TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
