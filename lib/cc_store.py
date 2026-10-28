"""Command Centre — shared write layer.

Single source of truth for all mutations to the operational records
(tasks, goals, commitments) and the activity log. Used by BOTH the
Hermes command-centre skill and the dashboard backend — never duplicate
write logic anywhere else.

Design guarantees:
  - Validation before any write (rejects malformed records).
  - Serialised mutations: a cross-process file lock wraps every mutation
    so concurrent writers (Hermes chat + dashboard) cannot interleave.
  - Atomic persistence: records are written to a temp file in the same
    directory, fsynced, then os.replace()'d into place — no partial
    writes on crash.
  - Backups: the full data/ directory is snapshotted before every
    mutation batch.
  - Append-only activity log: every mutation is appended (never
    rewritten) with actor, action, target and a short detail. No
    secrets, no full record contents.

Storage layout (default CC_HOME = this file's parent's parent):
  data/tasks.jsonl
  data/goals.jsonl
  data/commitments.jsonl
  data/activity.jsonl
  backups/<UTC-timestamp>/   (snapshots of data/)
  .cc.lock                   (advisory lock file)

JSONL now; the *interface* (load_*/save_* behind functions, stable
record shapes) is deliberately kept so a SQLite backend can replace
the file layer later without touching callers.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import datetime as dt
import contextlib
import time

try:
    import msvcrt  # Windows file locking
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None

try:
    import fcntl  # POSIX file locking
except ImportError:  # pragma: no cover
    fcntl = None

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOME = os.path.dirname(_HERE)

KINDS = ("tasks", "goals", "commitments")

STATUSES = ("active", "waiting", "done", "cancelled")
PRIORITIES = ("high", "normal", "low")
GOAL_STATUSES = ("active", "done", "cancelled")
WORK_KINDS = ("area", "project")
TASK_KINDS = ("task",)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^\d{8}-[0-9a-f]{6}$")


class CCError(Exception):
    """Validation or operational error. Safe to surface to the user."""


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso_now():
    return utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------- paths

# Runtime store override, set via the dashboard's store-switch endpoint.
# Deliberately NOT persisted: a reboot returns to CC_HOME (prod), so you
# can never accidentally leave the live dashboard pointing at dev. Only
# the exact names "prod" and "dev" are accepted; the override resolves to
# a sibling directory, never an arbitrary path.
STORE_OVERRIDE = {"home": None}
STORE_NAMES = ("prod", "dev")


def _configured_home():
    return os.environ.get("CC_HOME", os.path.join(DEFAULT_HOME, "command-center-prod"))


def _named_store_home(name):
    """Resolve a named sibling store from the configured PROD home."""
    configured = os.path.normpath(_configured_home())
    parent = os.path.dirname(configured)
    return os.path.join(parent, "command-center-%s" % name)


def set_store(name):
    """Set the runtime store ('prod'/'dev') or None to revert to CC_HOME."""
    if name is None:
        STORE_OVERRIDE["home"] = None
        return _configured_home()
    if name not in STORE_NAMES:
        raise ValueError("store must be one of %s" % (STORE_NAMES,))
    home = _named_store_home(name)
    STORE_OVERRIDE["home"] = home
    return home


def current_store_name():
    """Return the active store's exact public name."""
    home = STORE_OVERRIDE.get("home") or _configured_home()
    base = os.path.basename(os.path.normpath(home))
    if base.endswith("-prod"):
        return "prod"
    if base.endswith("-dev"):
        return "dev"
    return "prod" if home == _named_store_home("prod") else "custom"


def get_home():
    override = STORE_OVERRIDE.get("home")
    if override:
        return override
    return os.environ.get("CC_HOME", DEFAULT_HOME)


def data_dir(home=None):
    return os.path.join(home or get_home(), "data")


def backup_dir(home=None):
    return os.path.join(home or get_home(), "backups")


def data_file(kind, home=None):
    return os.path.join(data_dir(home), kind + ".jsonl")


def lock_path(home=None):
    return os.path.join(home or get_home(), ".cc.lock")


def backup_lock_path(home=None):
    return os.path.join(home or get_home(), ".cc.lock.backups")


# ------------------------------------------------------------ validation

def _require_str(rec, field, maxlen=500):
    v = rec.get(field)
    if not isinstance(v, str) or not v.strip():
        raise CCError("field '%s' must be a non-empty string" % field)
    if len(v) > maxlen:
        raise CCError("field '%s' exceeds %d chars" % (field, maxlen))
    return v.strip()


def _opt_str(rec, field, maxlen=2000):
    v = rec.get(field)
    if v is None:
        return None
    if not isinstance(v, str):
        raise CCError("field '%s' must be a string" % field)
    if len(v) > maxlen:
        raise CCError("field '%s' exceeds %d chars" % (field, maxlen))
    return v


def _opt_date(rec, field):
    v = rec.get(field)
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not DATE_RE.match(v):
        raise CCError("field '%s' must be YYYY-MM-DD or null" % field)
    try:
        dt.date.fromisoformat(v)
    except ValueError:
        raise CCError("field '%s' is not a real calendar date" % field)
    return v


def _opt_choice(rec, field, choices):
    v = rec.get(field)
    if v in (None, ""):
        return None
    if v not in choices:
        raise CCError("field '%s' must be one of %s" % (field, "/".join(choices)))
    return v


def _check_id(rid):
    if not isinstance(rid, str) or not ID_RE.match(rid):
        raise CCError("invalid id '%r' (expected YYYYMMDD-xxxxxx)" % (rid,))


def validate_task(rec, partial=False):
    """Return a normalised copy of a task record or raise CCError."""
    if not isinstance(rec, dict):
        raise CCError("task must be a JSON object")
    out = {}
    if not partial or "title" in rec:
        out["title"] = _require_str(rec, "title")
    out["notes"] = _opt_str(rec, "notes")
    out["due"] = _opt_date(rec, "due")
    out["status"] = _opt_choice(rec, "status", STATUSES)
    out["priority"] = _opt_choice(rec, "priority", PRIORITIES)
    out["kind"] = _opt_choice(rec, "kind", TASK_KINDS)
    parent_id = rec.get("parent_id")
    if parent_id is not None:
        _check_id(parent_id)
        out["parent_id"] = parent_id
    out["waiting_on"] = _opt_str(rec, "waiting_on", 1000)
    out["done_when"] = _opt_str(rec, "done_when", 2000)
    out["next_action"] = _opt_str(rec, "next_action", 500)
    gid = rec.get("goal_id")
    if gid:
        out["goal_id"] = str(gid)
    tags = rec.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise CCError("tags must be a list of strings")
        out["tags"] = tags
    if "id" in rec:
        _check_id(rec["id"])
    if not partial:
        out["notes"] = out.get("notes") or None
        out["due"] = out.get("due") or None
        out["priority"] = out.get("priority") or "normal"
        out["status"] = out.get("status") or "active"
        out["kind"] = out.get("kind") or "task"
        out["parent_id"] = out.get("parent_id") or None
        out["waiting_on"] = out.get("waiting_on") or None
        out["done_when"] = out.get("done_when") or None
        out["next_action"] = out.get("next_action") or None
        out["tags"] = out.get("tags") or []
    return out


def validate_goal(rec, partial=False):
    if not isinstance(rec, dict):
        raise CCError("goal must be a JSON object")
    out = {}
    if not partial or "title" in rec:
        out["title"] = _require_str(rec, "title")
    out["status"] = _opt_choice(rec, "status", GOAL_STATUSES)
    out["kind"] = _opt_choice(rec, "kind", WORK_KINDS)
    parent_id = rec.get("parent_id")
    if parent_id is not None:
        _check_id(parent_id)
        out["parent_id"] = parent_id
    out["done_when"] = _opt_str(rec, "done_when", 2000)
    out["next_action"] = _opt_str(rec, "next_action", 500)
    out["target"] = _opt_date(rec, "target")
    out["due"] = _opt_date(rec, "due")
    out["notes"] = _opt_str(rec, "notes")
    na = rec.get("next_actions")
    if na is not None:
        if not isinstance(na, list) or not all(isinstance(a, str) for a in na):
            raise CCError("next_actions must be a list of strings")
        out["next_actions"] = na
    if not partial and "id" in rec:
        _check_id(rec["id"])
    if not partial:
        out["kind"] = out.get("kind") or "goal"
        out["parent_id"] = out.get("parent_id") or None
        out["done_when"] = out.get("done_when") or None
        out["next_action"] = out.get("next_action") or None
        out["target"] = out.get("target") or None
        out["due"] = out.get("due") or None
    return out


def validate_commitment(rec, partial=False):
    if not isinstance(rec, dict):
        raise CCError("commitment must be a JSON object")
    out = {}
    if not partial or "what" in rec:
        out["what"] = _require_str(rec, "what")
    out["whom"] = _opt_str(rec, "whom", 200)
    out["due"] = _opt_date(rec, "due")
    out["done"] = rec.get("done")
    if out.get("done") is not None and not isinstance(out["done"], bool):
        raise CCError("done must be a boolean")
    out["notes"] = _opt_str(rec, "notes")
    if not partial and "id" in rec:
        _check_id(rec["id"])
    return {k: v for k, v in out.items() if v is not None or k in ("due", "done")}


VALIDATORS = {
    "tasks": validate_task,
    "goals": validate_goal,
    "commitments": validate_commitment,
}


# ------------------------------------------------------------------ I/O

def _load_file(path, retries=0):
    """Read a JSONL file tolerantly: skip blank lines, raise on bad JSON.

    Errors reference the file by BASENAME only, so an exception message can
    safely reach a user or an HTTP client without disclosing the absolute
    filesystem path (see M1 in the security review).

    On Windows, a file can be transiently unreadable while another thread
    is mid-copy into it (the copier holds a share mode that denies
    concurrent opens). `retries` re-attempts the open a few times with a
    short backoff to ride out that window; after the retries are exhausted
    the error is raised (or returned) as below.
    """
    if not os.path.exists(path):
        return []
    attempt = 0
    while True:
        try:
            return _load_file_once(path)
        except (PermissionError, OSError) as exc:
            if attempt >= retries:
                raise
            attempt += 1
            time.sleep(0.05 * attempt)


def _load_file_once(path):
    records = []
    shown = os.path.basename(path)
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CCError("%s:%d is not valid JSON: %s" % (shown, lineno, exc))
    return records


def load_kind(kind, home=None):
    if kind not in KINDS:
        raise CCError("unknown kind '%s'" % kind)
    return _load_file(data_file(kind, home))


def _atomic_write_lines(path, records):
    """Write records as JSONL atomically (temp file + fsync + os.replace)."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True))
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on Windows and POSIX
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def new_id():
    return utcnow().strftime("%Y%m%d") + "-" + os.urandom(3).hex()


def _backup_snapshot(home=None):
    home = home or get_home()
    src = data_dir(home)
    if not os.path.isdir(src):
        return None
    base = utcnow().strftime("%Y%m%dT%H%M%SZ")
    bdir = backup_dir(home)
    # Snapshot names have 1-second resolution. Two mutations in the same
    # second must NOT merge into one directory (exist_ok=True would let a
    # later mutation overwrite an existing snapshot's files). Claim the
    # base-stamped dir atomically with os.mkdir; if it is taken, retry
    # with -2, -3, ... until one succeeds. os.mkdir is atomic, so this is
    # race-free even under concurrent mutations.
    n = 1
    while True:
        name = base if n == 1 else "%s-%d" % (base, n)
        dest = os.path.join(bdir, name)
        try:
            os.mkdir(dest)          # atomic claim; fails if it exists
            break
        except FileExistsError:
            n += 1
            if n > 10000:
                raise
    # Copy each file into a TEMP file inside the snapshot dir, then rename
    # it into place. A snapshot becomes visible (and therefore verifiable
    # and prunable) only once every file is fully written, so a concurrent
    # reader never sees a half-copied file. This is the atomic-visibility
    # fix for the Windows copy-lock race.
    for fname in os.listdir(src):
        if fname.endswith(".jsonl"):
            srcf = os.path.join(src, fname)
            dstf = os.path.join(dest, fname)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dest)
            os.close(fd)
            try:
                shutil.copy2(srcf, tmp)
                os.replace(tmp, dstf)   # atomic reveal
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
    return dest


# ------------------------------------------------------- backup retention

BACKUP_KEEP_DEFAULT = 20
SNAP_RE = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")

# Files that make up a complete snapshot. The activity log is included so a
# snapshot that is missing or has corrupted it fails verification.
VERIFY_KINDS = KINDS + ("activity",)


def get_backup_keep():
    """How many snapshots to retain. Env override; default is conservative."""
    v = os.environ.get("CC_BACKUP_KEEP")
    if v:
        try:
            n = int(v)
            if n >= 1:
                return n
        except ValueError:
            pass
    return BACKUP_KEEP_DEFAULT


def list_snapshots(home=None):
    """Eligible backup snapshot dirs, oldest first.

    Only directories whose name matches the snapshot stamp are eligible;
    anything else under backups/ is left untouched. Name order equals
    chronological order for the %Y%m%dT%H%M%SZ format.
    """
    home = home or get_home()
    bdir = backup_dir(home)
    if not os.path.isdir(bdir):
        return []
    out = []
    for name in os.listdir(bdir):
        p = os.path.join(bdir, name)
        if os.path.isdir(p) and SNAP_RE.match(name):
            out.append((name, p))
    # Chronological order: primary key is the base stamp, secondary key is
    # the numeric same-second suffix (so -2 sorts before -10).
    def _key(t):
        m = SNAP_RE.match(t[0])
        suffix = int(m.group(1)[1:]) if m.group(1) else 0
        return (m.group(0).split("-")[0], suffix)
    out.sort(key=_key)
    return out


def verify_snapshot(path):
    """A snapshot is restorable only if every required file exists and parses.

    Checks tasks, goals, commitments AND activity.jsonl — the activity log
    is part of the snapshot (see _backup_snapshot) and a snapshot missing
    or corrupting it is not a complete backup.

    Returns (ok, reason). Three outcomes:
      - ok=False, reason names a genuinely missing or corrupt file -> the
        snapshot is bad and pruning must be blocked.
      - ok=False, reason is 'unreadable: ...' -> the file could not be read
        (transiently) and we CANNOT vouch for it. Callers that prune must
        treat this as "not verified" and skip pruning rather than lose the
        snapshot. Distinguishing the two lets a transient Windows lock
        never be mistaken for corruption.
      - ok=True -> verified.
    """
    for kind in VERIFY_KINDS:
        f = os.path.join(path, kind + ".jsonl")
        if not os.path.exists(f):
            return False, "missing %s.jsonl" % kind
        try:
            _load_file(f, retries=3)
        except CCError as exc:
            return False, "%s: %s" % (kind, exc)
        except (PermissionError, OSError) as exc:
            # Transient or persistent read failure. Not proof of
            # corruption, but not verifiable either -> do not prune.
            return False, "unreadable: %s" % exc
    return True, "ok"


def prune_backups(home=None, keep=None):
    """Remove old eligible snapshots, keeping the newest `keep`.

    Safety rules:
      - The newest snapshot is VERIFIED first. If it cannot be verified,
        nothing is pruned at all — a failed verification must never cost
        us a backup.
      - Only eligible snapshot dirs (name matches SNAP_RE) are removed;
        live data, the activity log and any non-snapshot entries are
        never touched.
      - Failures are collected and reported, never raised, so the caller
        (the mutation that just produced the new backup) still succeeds.

    Returns a report: {kept, removed, verified, errors}.
    """
    home = home or get_home()
    keep = keep if keep is not None else get_backup_keep()
    snaps = list_snapshots(home)
    report = {"kept": [], "removed": [], "verified": None, "errors": []}

    if not snaps:
        return report

    newest_name, newest_path = snaps[-1]
    ok, why = verify_snapshot(newest_path)
    report["verified"] = newest_name
    if not ok:
        # Refuse to prune when we cannot vouch for the new backup.
        report["errors"].append(
            "newest snapshot %s failed verification (%s); pruning skipped"
            % (newest_name, why))
        return report

    removable = snaps[:-keep] if len(snaps) > keep else []
    for name, path in removable:
        try:
            shutil.rmtree(path)
            report["removed"].append(name)
        except OSError as exc:
            report["errors"].append("failed to remove %s: %s" % (name, exc))
    report["kept"] = [n for n, _ in snaps if n not in report["removed"]]
    return report


def _append_activity(actor, action, target, detail, home=None):
    line = {"ts": iso_now(), "actor": actor, "action": action,
            "target": target, "detail": str(detail)[:200]}
    path = os.path.join(data_dir(home or get_home()), "activity.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------- lock helpers

# Two locks guard two independent regions:
#   .cc.lock          -> data/   (serialises record mutations)
#   .cc.lock.backups  -> backups/ (serialises snapshot publish/verify/prune/restore-read)
#
# LOCK ORDERING (deadlock avoidance): whenever BOTH locks are held, the
# backups lock is ALWAYS acquired first, then the data lock. Every code
# path follows this single fixed order, so a circular wait is impossible.
# msvcrt LK_LOCK blocks (it does not fail) on contention, so waiting for
# the backups lock can never deadlock.

@contextlib.contextmanager
def _file_lock(path):
    """Advisory cross-process lock on a dedicated lock file."""
    fh = open(path, "a+b")
    try:
        if msvcrt:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        elif fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        yield fh
    finally:
        if msvcrt:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        elif fcntl:
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


@contextlib.contextmanager
def backups_lock(home=None):
    """Serialise all access to the backups/ directory.

    Every operation that publishes a snapshot, verifies one, prunes, or
    reads one for restore MUST hold this lock, so a snapshot can never be
    deleted while it is being published, verified or restored.
    """
    home = home or get_home()
    os.makedirs(backup_dir(home), exist_ok=True)
    with _file_lock(backup_lock_path(home)):
        yield


@contextlib.contextmanager
def mutation(actor, action, target, home=None):
    """Serialised, backed-up, logged mutation context.

    Yields the home path. Use inside: mutate files via _atomic_write_lines.
    Backup + activity log happen automatically; roll back on exception.
    """
    home = home or get_home()
    os.makedirs(data_dir(home), exist_ok=True)
    os.makedirs(backup_dir(home), exist_ok=True)
    prune_report = None
    # LOCK ORDER: backups lock FIRST, then data lock (fixed order -> no
    # deadlock). The backups lock covers snapshot publication and pruning,
    # so no other thread can delete a snapshot we are publishing/verifying.
    with backups_lock(home):
        snap = _backup_snapshot(home)
        with _file_lock(lock_path(home)):
            before = {}
            for kind in KINDS:
                p = data_file(kind, home)
                before[kind] = _load_file(p) if os.path.exists(p) else []
            try:
                yield home
            except BaseException:
                # restore pre-mutation state on failure
                for kind in KINDS:
                    _atomic_write_lines(data_file(kind, home), before[kind])
                raise
            _append_activity(actor, action, target, "ok", home)
            # Prune while holding the backups lock: snapshot publication
            # and pruning are mutually excluded, so a snapshot can never be
            # deleted mid-publish. prune_backups verifies the newest
            # snapshot and reports failures rather than raising.
            if snap:
                prune_report = prune_backups(home)


# ------------------------------------------------------------- operations

def _find(records, rid):
    for rec in records:
        if rec.get("id") == rid:
            return rec
    return None


def _unfinished_children(rid, home):
    records = load_kind("goals", home) + load_kind("tasks", home)
    return [r for r in records if r.get("parent_id") == rid
            and r.get("status") not in ("done", "cancelled")]


def _validate_parent_relationship(kind, parent_id, home, moving_id=None):
    if parent_id is None:
        return
    records = load_kind("goals", home) + load_kind("tasks", home)
    by_id = {r.get("id"): r for r in records}
    parent = by_id.get(parent_id)
    if parent is None:
        raise CCError("parent %s not found" % parent_id)
    parent_kind = parent.get("kind")
    allowed = {
        "area": set(),
        "project": {"area"},
        "task": {"area", "project", "task"},
    }
    if kind not in allowed or parent_kind not in allowed[kind]:
        raise CCError("%s cannot have parent kind %s" % (kind, parent_kind))
    if kind == "task" and parent_kind == "task":
        grandparent = by_id.get(parent.get("parent_id"))
        if grandparent is not None and grandparent.get("kind") == "task":
            raise CCError("a subtask cannot own another subtask")
    seen = set()
    cursor = parent
    while cursor is not None:
        cursor_id = cursor.get("id")
        if cursor_id in seen:
            raise CCError("circular hierarchy")
        seen.add(cursor_id)
        if moving_id and cursor_id == moving_id:
            raise CCError("circular hierarchy")
        cursor = by_id.get(cursor.get("parent_id"))


def _require_parent_exists(parent_id, home):
    if parent_id is None:
        return
    _validate_parent_relationship("task", parent_id, home)


def create_task(actor, **fields):
    home = get_home()
    rec = validate_task(fields)
    if rec.get("status") == "waiting" and not (rec.get("waiting_on") or "").strip():
        raise CCError("waiting_on is required when status is waiting")
    _require_parent_exists(rec.get("parent_id"), home)
    rec["id"] = new_id()
    rec["status"] = rec.get("status") or "active"
    rec["priority"] = rec.get("priority") or "normal"
    rec["tags"] = rec.get("tags") or []
    rec["created"] = iso_now()
    rec["updated"] = rec["created"]
    rec["completed_at"] = None
    with mutation(actor, "task.create", "task:" + rec["id"], home):
        records = load_kind("tasks", home)
        records.append(rec)
        _atomic_write_lines(data_file("tasks", home), records)
    return rec


def update_task(actor, rid, **patch):
    _check_id(rid)
    clean = validate_task(patch, partial=True)
    clean.pop("id", None)
    home = get_home()
    if "parent_id" in clean:
        _validate_parent_relationship("task", clean["parent_id"], home, moving_id=rid)
    if "waiting_on" in clean and not (clean.get("waiting_on") or "").strip():
        clean["waiting_on"] = None
    with mutation(actor, "task.update", "task:" + rid, home):
        records = load_kind("tasks", home)
        rec = _find(records, rid)
        if rec is None:
            raise CCError("task %s not found" % rid)
        merged = dict(rec)
        merged.update(clean)
        old_status = rec.get("status") or "active"
        if merged.get("status") == "waiting" and not (merged.get("waiting_on") or "").strip():
            raise CCError("waiting_on is required when status is waiting")
        if merged.get("status") in ("done", "cancelled") and _unfinished_children(rid, home):
            raise CCError("cannot complete or cancel an item with unfinished children")
        for k, v in clean.items():
            if k in patch:  # only overwrite fields explicitly provided
                rec[k] = v
        if clean.get("status") == "done" and old_status != "done":
            rec["previous_status"] = old_status
            rec["completed_at"] = iso_now()
        elif clean.get("status") == "active" and old_status == "done":
            rec["status"] = rec.get("previous_status") or "active"
            rec["previous_status"] = None
            rec["completed_at"] = None
        rec["updated"] = iso_now()
        _atomic_write_lines(data_file("tasks", home), records)
    return rec


def complete_task(actor, rid):
    home = get_home()
    with mutation(actor, "task.complete", "task:" + rid, home):
        records = load_kind("tasks", home)
        rec = _find(records, rid)
        if rec is None:
            raise CCError("task %s not found" % rid)
        if _unfinished_children(rid, home):
            raise CCError("cannot complete an item with unfinished children")
        rec["previous_status"] = rec.get("status") or "active"
        rec["status"] = "done"
        rec["completed_at"] = iso_now()
        rec["updated"] = rec["completed_at"]
        _atomic_write_lines(data_file("tasks", home), records)
    return rec


def create_goal(actor, **fields):
    home = get_home()
    rec = validate_goal(fields)
    rec["id"] = new_id()
    rec["kind"] = rec.get("kind") or "goal"
    if rec["kind"] == "area":
        if fields.get("status") not in (None, ""):
            raise CCError("areas do not have a status")
        _validate_parent_relationship("area", rec.get("parent_id"), home)
        rec.pop("status", None)
    elif rec["kind"] == "project":
        _validate_parent_relationship("project", rec.get("parent_id"), home)
        rec["status"] = rec.get("status") or "active"
    else:
        _require_parent_exists(rec.get("parent_id"), home)
        rec["status"] = rec.get("status") or "active"
    rec.setdefault("next_actions", [])
    rec["created"] = iso_now()
    with mutation(actor, "goal.create", "goal:" + rec["id"], home):
        records = load_kind("goals", home)
        records.append(rec)
        _atomic_write_lines(data_file("goals", home), records)
    return rec


def update_goal(actor, rid, **patch):
    _check_id(rid)
    clean = validate_goal(patch, partial=True)
    clean.pop("id", None)
    home = get_home()
    records = load_kind("goals", home)
    rec = _find(records, rid)
    if rec is None:
        raise CCError("goal %s not found" % rid)
    kind = rec.get("kind")
    if "kind" in patch and patch["kind"] != kind:
        raise CCError("changing item type is not supported")
    if kind == "area":
        if patch.get("status") not in (None, ""):
            raise CCError("areas do not have a status")
        if "parent_id" in patch:
            _validate_parent_relationship("area", patch["parent_id"], home, moving_id=rid)
    elif kind == "project":
        if "status" in patch and patch["status"] not in GOAL_STATUSES:
            raise CCError("project status is invalid")
        if "parent_id" in patch:
            _validate_parent_relationship("project", patch["parent_id"], home, moving_id=rid)
        if patch.get("status") in ("done", "cancelled") and _unfinished_children(rid, home):
            raise CCError("cannot complete or cancel an item with unfinished children")
    with mutation(actor, "goal.update", "goal:" + rid, home):
        records = load_kind("goals", home)
        rec = _find(records, rid)
        old_status = rec.get("status") if rec else None
        for k, v in clean.items():
            if k in patch:
                rec[k] = v
        if rec.get("kind") == "area":
            rec.pop("status", None)
        if kind == "project" and patch.get("status") == "done" and old_status != "done":
            rec["previous_status"] = old_status or "active"
            rec["completed_at"] = iso_now()
        elif kind == "project" and patch.get("status") == "active" and old_status == "done":
            rec["status"] = rec.get("previous_status") or "active"
            rec["previous_status"] = None
            rec["completed_at"] = None
        _atomic_write_lines(data_file("goals", home), records)
    return rec


def restore_task(actor, rid):
    _check_id(rid)
    home = get_home()
    with mutation(actor, "task.restore", "task:" + rid, home):
        records = load_kind("tasks", home)
        rec = _find(records, rid)
        if rec is None:
            raise CCError("task %s not found" % rid)
        rec["status"] = rec.get("previous_status") or "active"
        rec["previous_status"] = None
        rec["completed_at"] = None
        rec["updated"] = iso_now()
        _atomic_write_lines(data_file("tasks", home), records)
    return rec


def delete_organisation(actor, rid):
    _check_id(rid)
    home = get_home()
    for kind in ("goals", "tasks"):
        records = load_kind(kind, home)
        rec = _find(records, rid)
        if rec is None:
            continue
        if _unfinished_children(rid, home) or any(
                r.get("parent_id") == rid for r in load_kind("goals", home) + load_kind("tasks", home)):
            raise CCError("cannot delete an item with children")
        with mutation(actor, "organisation.delete", kind + ":" + rid, home):
            records = load_kind(kind, home)
            records = [r for r in records if r.get("id") != rid]
            _atomic_write_lines(data_file(kind, home), records)
        return {"id": rid, "deleted": True}
    raise CCError("organisation item %s not found" % rid)
def create_commitment(actor, **fields):
    home = get_home()
    rec = validate_commitment(fields)
    rec["id"] = new_id()
    rec["done"] = False
    rec["created"] = iso_now()
    with mutation(actor, "commitment.create", "commitment:" + rec["id"], home):
        records = load_kind("commitments", home)
        records.append(rec)
        _atomic_write_lines(data_file("commitments", home), records)
    return rec


def complete_commitment(actor, rid):
    _check_id(rid)
    home = get_home()
    with mutation(actor, "commitment.complete", "commitment:" + rid, home):
        records = load_kind("commitments", home)
        rec = _find(records, rid)
        if rec is None:
            raise CCError("commitment %s not found" % rid)
        rec["done"] = True
        _atomic_write_lines(data_file("commitments", home), records)
    return rec


def create_organisation(actor, **fields):
    kind = fields.get("kind")
    if kind == "area":
        if fields.get("parent_id") is not None:
            raise CCError("areas cannot have parents")
        if fields.get("status") not in (None, ""):
            raise CCError("areas do not have a status")
        return create_goal(actor, **fields)
    if kind == "project":
        if fields.get("status") not in (None, "", *GOAL_STATUSES):
            raise CCError("project status is invalid")
        return create_goal(actor, **fields)
    if kind == "task":
        return create_task(actor, **fields)
    raise CCError("field 'kind' must be one of area/project/task")


def update_organisation(actor, rid, **patch):
    _check_id(rid)
    home = get_home()
    goals = load_kind("goals", home)
    tasks = load_kind("tasks", home)
    rec = _find(goals, rid)
    if rec is not None:
        allowed = {k: v for k, v in patch.items()
                   if k in ("title", "status", "kind", "parent_id", "done_when", "next_action", "target", "due", "notes")}
        return update_goal(actor, rid, **allowed)
    rec = _find(tasks, rid)
    if rec is not None:
        allowed = {k: v for k, v in patch.items()
                   if k in ("title", "status", "kind", "parent_id", "waiting_on", "done_when", "next_action", "notes", "due", "priority")}
        return update_task(actor, rid, **allowed)
    raise CCError("organisation item %s not found" % rid)


# ---------------------------------------------------------------- views

def today():
    return dt.date.today()


def open_tasks(tasks=None):
    return [t for t in (tasks if tasks is not None else load_kind("tasks"))
            if t.get("status") in ("active", "waiting") ]


def due_on_or_before(t, day):
    d = t.get("due")
    return bool(d) and d <= day.isoformat()


def _inherited_project_due(rec, records_by_id):
    """Return the nearest ancestor project's due date, if any."""
    seen = set()
    parent_id = rec.get("parent_id")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = records_by_id.get(parent_id)
        if parent is None:
            break
        if parent.get("kind") == "project" and parent.get("due"):
            return parent["due"]
        parent_id = parent.get("parent_id")
    return None


def _task_views_with_inherited_due(tasks, goals):
    records_by_id = {r["id"]: r for r in goals + tasks}
    views = []
    for task in tasks:
        view = dict(task)
        if not view.get("due"):
            inherited = _inherited_project_due(view, records_by_id)
            if inherited:
                view["due"] = inherited
                view["due_source"] = "inherited"
        else:
            view["due_source"] = "explicit"
        views.append(view)
    return views


def _organisation_tree(tasks, goals):
    all_records = [r for r in goals + tasks if r.get("kind")]
    records = [r for r in all_records if r.get("status") not in ("done", "cancelled")]
    by_parent = {}
    all_by_parent = {}
    for rec in records:
        by_parent.setdefault(rec.get("parent_id"), []).append(rec)
    for rec in all_records:
        all_by_parent.setdefault(rec.get("parent_id"), []).append(rec)

    def node(rec, inherited_project_due=None):
        own_due = rec.get("due")
        project_due = own_due if rec.get("kind") == "project" and own_due else inherited_project_due
        effective_due = own_due or inherited_project_due
        due_source = "explicit" if own_due else ("inherited" if inherited_project_due else None)
        children = sorted(by_parent.get(rec.get("id"), []),
                          key=lambda x: (x.get("status") == "done", x.get("title", "").lower()))
        child_headers = sorted(all_by_parent.get(rec.get("id"), []),
                               key=lambda x: (x.get("status") in ("done", "cancelled"), x.get("title", "").lower()))
        return {
            "id": rec["id"],
            "title": rec.get("title", ""),
            "kind": rec.get("kind"),
            "parent_id": rec.get("parent_id"),
            "status": rec.get("status"),
            "priority": rec.get("priority"),
            "notes": rec.get("notes"),
            "due": effective_due,
            "due_source": due_source,
            "target": rec.get("target"),
            "next_action": rec.get("next_action"),
            "waiting_on": rec.get("waiting_on"),
            "done_when": rec.get("done_when"),
            "child_headers": [
                {"id": child["id"], "title": child.get("title", ""),
                 "kind": child.get("kind"), "status": child.get("status")}
                for child in child_headers
            ],
            "children": [node(child, project_due) for child in children],
        }

    roots = sorted(by_parent.get(None, []), key=lambda x: x.get("title", "").lower())
    return [node(root) for root in roots]


def build_state(home=None):
    """Compute the dashboard state view from the raw files.

    Read-only. Both the dashboard and scheduled briefings can call this.
    """
    home = home or get_home()
    day = today()
    soon = (day + dt.timedelta(days=7)).isoformat()
    tasks = load_kind("tasks", home)
    commitments = load_kind("commitments", home)
    goals = load_kind("goals", home)
    task_views = _task_views_with_inherited_due(tasks, goals)

    def sortkey(t):
        prio = {"high": 0, "normal": 1, "low": 2}.get(t.get("priority"), 1)
        return (t.get("due") or "9999-12-31", prio, t.get("title", ""))

    active = sorted(open_tasks(task_views), key=sortkey)
    return {
        "generated_at": iso_now(),
        "today": day.isoformat(),
        "waiting": [t for t in active if t.get("status") == "waiting"],
        "today_tasks": [t for t in active if t.get("due") == day.isoformat()],
        "overdue": [t for t in active if due_on_or_before(t, day) and t.get("due") < day.isoformat()],
        "upcoming_tasks": [t for t in active if t.get("due") and day.isoformat() < t["due"] <= soon],
        "no_date_tasks": [t for t in active if not t.get("due")],
        "commitments": {
            "overdue": [c for c in commitments if not c.get("done") and c.get("due") and c["due"] < day.isoformat()],
            "upcoming": [c for c in commitments if not c.get("done") and c.get("due") and day.isoformat() <= c["due"] <= soon],
            "no_date": [c for c in commitments if not c.get("done") and not c.get("due")],
        },
        "goals": [g for g in goals if g.get("status") == "active"],
        "organisation": _organisation_tree(tasks, goals),
    }


# ---------------------------------------------------------------- init

def init(home=None):
    home = home or get_home()
    os.makedirs(data_dir(home), exist_ok=True)
    os.makedirs(backup_dir(home), exist_ok=True)
    for kind in KINDS:
        p = data_file(kind, home)
        if not os.path.exists(p):
            open(p, "a", encoding="utf-8").close()
    p = os.path.join(data_dir(home), "activity.jsonl")
    if not os.path.exists(p):
        open(p, "a", encoding="utf-8").close()
    return home


# ------------------------------------------------------------------ CLI

def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _flag(args, name, default=None):
    """Value of --name, or default when absent."""
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            raise CCError("missing value for %s" % name)
        return args[i + 1]
    return default


def _positional(args, before_flags):
    """First non-flag token (used for record ids like `tasks complete ID`)."""
    skip = set()
    for i, a in enumerate(args):
        if a.startswith("--"):
            skip.add(i + 1)
    for i, a in enumerate(args):
        if not a.startswith("--") and i not in skip and i < before_flags:
            return a
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    actor = os.environ.get("CC_ACTOR", "cli")
    try:
        if cmd == "init":
            _print_json({"home": init()})

        elif cmd == "tasks":
            if rest and rest[0] == "list":
                recs = load_kind("tasks")
                s = _flag(rest, "--status")
                if s:
                    recs = [r for r in recs if r.get("status") == s]
                _print_json(recs)
            elif rest and rest[0] == "add":
                _print_json(create_task(actor, **_parse_fields(rest[1:])))
            elif rest and rest[0] == "get":
                rid = _flag(rest, "--id") or (rest[1] if len(rest) > 1 else None)
                rec = _find(load_kind("tasks"), rid)
                if rec is None:
                    raise CCError("task %s not found" % rid)
                _print_json(rec)
            elif rest and rest[0] == "complete":
                rid = _flag(rest, "--id") or (rest[1] if len(rest) > 1 else None)
                _print_json(complete_task(actor, rid))
            elif rest and rest[0] == "update":
                rid = _flag(rest, "--id") or (rest[1] if len(rest) > 1 else None)
                _print_json(update_task(actor, rid, **_parse_fields(rest[1:])))
            else:
                raise CCError("tasks requires a subcommand: list|add|get|complete|update")

        elif cmd == "goals":
            if rest and rest[0] == "list":
                _print_json(load_kind("goals"))
            elif rest and rest[0] == "add":
                _print_json(create_goal(actor, **_parse_fields(rest[1:])))
            elif rest and rest[0] == "update":
                rid = _flag(rest, "--id") or (rest[1] if len(rest) > 1 else None)
                _print_json(update_goal(actor, rid, **_parse_fields(rest[1:])))
            else:
                raise CCError("goals requires a subcommand: list|add|update")

        elif cmd == "commitments":
            if rest and rest[0] == "list":
                _print_json(load_kind("commitments"))
            elif rest and rest[0] == "add":
                _print_json(create_commitment(actor, **_parse_fields(rest[1:])))
            elif rest and rest[0] == "complete":
                rid = _flag(rest, "--id") or (rest[1] if len(rest) > 1 else None)
                _print_json(complete_commitment(actor, rid))
            else:
                raise CCError("commitments requires a subcommand: list|add|complete")

        elif cmd == "state":
            _print_json(build_state())

        elif cmd == "activity":
            n = int(_flag(rest, "--n", "20"))
            lines = _load_file(os.path.join(data_dir(), "activity.jsonl"))
            _print_json(lines[-n:])

        elif cmd == "restore":
            src = _flag(rest, "--from")
            if not src or "--yes" not in rest:
                raise CCError("restore requires --from <dir> --yes")
            home = get_home()
            # Hold the backups lock across the READ so a concurrent prune
            # cannot delete the snapshot between listing it and reading it.
            # Buffer contents into memory, then write under the data lock.
            # LOCK ORDER: backups lock, then data lock (see mutation()).
            with backups_lock(home):
                buffered = {}
                for kind in KINDS:
                    srcf = os.path.join(src, kind + ".jsonl")
                    if not os.path.exists(srcf):
                        raise CCError("backup %s missing %s" % (src, kind))
                    recs = _load_file(srcf)
                    validator = VALIDATORS[kind]
                    for i, rec in enumerate(recs):
                        try:
                            validator(rec)
                        except CCError as exc:
                            raise CCError(
                                "backup has an invalid %s record at line %d: %s"
                                % (kind, i + 1, exc))
                    buffered[kind] = recs
            with mutation("restore", "restore", src, home):
                for kind in KINDS:
                    _atomic_write_lines(data_file(kind, home), buffered[kind])
            _print_json({"restored_from": src, "kinds": list(KINDS)})

        elif cmd == "backups":
            sub = rest[0] if rest else "list"
            if sub == "list":
                with backups_lock():
                    snaps = [(n, verify_snapshot(p)[0], verify_snapshot(p)[1])
                             for n, p in list_snapshots()]
                _print_json([{"name": n, "ok": ok, "reason": why}
                             for n, ok, why in snaps])
            elif sub == "prune":
                keep = int(_flag(rest, "--keep", str(get_backup_keep())))
                with backups_lock():
                    rep = prune_backups(keep=keep)
                _print_json(rep)
                if rep["errors"]:
                    return 2
            else:
                raise CCError("backups requires a subcommand: list|prune")
        else:
            raise CCError("unknown command '%s'" % cmd)
    except CCError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


def _parse_fields(args):
    """Parse --key value flags into kwargs (values kept as strings)."""
    kw = {}
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("--"):
            raise CCError("unexpected argument '%s'" % a)
        key = a[2:].replace("-", "_")
        i += 1
        if i >= len(args):
            raise CCError("missing value for %s" % a)
        val = args[i]
        i += 1
        if key == "next_action":
            kw.setdefault("next_actions", []).append(val)
        else:
            kw[key] = val
    return kw


if __name__ == "__main__":
    sys.exit(main())
