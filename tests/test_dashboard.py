"""Dashboard task-creation tests.

Boots the real dashboard server against an ISOLATED temp home (so the
live store is never touched) and exercises the /api/tasks/create endpoint
plus the existing endpoints, verifying writes land in the store, appear in
build_state() and the activity log, and that bad input is rejected.

Run:  python tests/test_dashboard.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
PY = sys.executable
PORT = 8891  # avoid clashing with the standard 8787


def wait_up(url, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # server responded (even an error) -> it's up
        except Exception:
            time.sleep(0.2)
    return False


def post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return e.code, body


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def raw_request(url, method="POST", payload=None, headers=None, body=None):
    """Issue a request with arbitrary headers. Returns (status, body_text,
    response_headers)."""
    data = (
        body
        if body is not None
        else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    )
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


TEST_MAGICDNS_HOST = "dashboard.example.test"
TEST_LOGIN = "test-user@example.invalid"


def main():
    home = tempfile.mkdtemp(prefix="cc-dash-")
    env = dict(
        os.environ,
        CC_HOME=home,
        CC_PORT=str(PORT),
        CC_ALLOWED_HOSTS=TEST_MAGICDNS_HOST,
        CC_EXPECTED_TS_LOGIN=TEST_LOGIN,
    )
    srv = subprocess.Popen(
        [PY, os.path.join(LIB, "cc_dashboard.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ok = True
    try:
        assert wait_up("http://127.0.0.1:%d/api/state" % PORT), "server did not start"
        print("[boot] dashboard up on 127.0.0.1:%d (home %s)" % (PORT, home))
        base = "http://127.0.0.1:%d" % PORT

        # --- 1. successful creation ---
        st, j = post(
            base + "/api/tasks/create",
            {
                "title": "TEST - dashboard create",
                "priority": "high",
                "notes": "created via dashboard test",
                "due": "2026-10-15",
            },
        )
        assert st == 201, "expected 201, got %s: %r" % (st, j)
        rec = j["record"]
        assert rec["status"] == "active", "default status not active: %r" % rec["status"]
        assert rec["priority"] == "high" and rec["due"] == "2026-10-15"
        assert rec["title"] == "TEST - dashboard create"
        assert rec["notes"] == "created via dashboard test"
        assert rec["tags"] == []
        print("[create] 201, id=%s status=active priority=high due=2026-10-15" % rec["id"])
        tid = rec["id"]

        # --- 1b. defaults when only a title is supplied ---
        st, j = post(base + "/api/tasks/create", {"title": "TEST - defaults only"})
        assert st == 201
        assert j["record"]["status"] == "active" and j["record"]["priority"] == "normal"
        assert j["record"]["due"] is None and j["record"]["notes"] is None
        print("[create] defaults only -> status=active priority=normal due=None")

        # --- 2. task appears in dashboard state ---
        _, state = get_json(base + "/api/state")
        titles = [
            t["title"]
            for t in state["today_tasks"]
            + state["upcoming_tasks"]
            + state["no_date_tasks"]
            + state["overdue"]
        ]
        assert "TEST - dashboard create" in titles, "created task missing from state"
        assert "TEST - defaults only" in titles
        assert tid in [
            t["id"]
            for t in state["today_tasks"]
            + state["upcoming_tasks"]
            + state["no_date_tasks"]
            + state["overdue"]
        ]
        print("[state] both created tasks visible in /api/state")

        # --- 3. activity log records the create with actor=dashboard ---
        with open(os.path.join(home, "data/activity.jsonl"), encoding="utf-8") as fh:
            acts = [json.loads(line) for line in fh if line.strip()]
        mine = [a for a in acts if a["target"] == "task:" + tid]
        assert len(mine) == 1 and mine[0]["action"] == "task.create", mine
        assert mine[0]["actor"] == "dashboard", mine[0]
        print("[activity] task.create by actor=dashboard logged")

        # --- 4. missing title rejected ---
        st, j = post(base + "/api/tasks/create", {"priority": "high"})
        assert st == 400, "missing title should be 400, got %s" % st
        print("[reject] missing title -> 400 (%s)" % j.get("error"))

        # --- 4b. empty / whitespace title rejected ---
        st, j = post(base + "/api/tasks/create", {"title": "   "})
        assert st == 400, "whitespace title should be 400, got %s" % st
        print("[reject] whitespace-only title -> 400")

        # --- 5. invalid priority rejected ---
        st, j = post(base + "/api/tasks/create", {"title": "bad prio", "priority": "urgent"})
        assert st == 400 and "priority" in str(j.get("error"))
        print("[reject] invalid priority -> 400 (%s)" % j.get("error"))

        # --- 5b. invalid due date rejected ---
        st, j = post(base + "/api/tasks/create", {"title": "bad due", "due": "15/10/2026"})
        assert st == 400 and "due" in str(j.get("error"))
        print("[reject] invalid due format -> 400 (%s)" % j.get("error"))

        st, j = post(base + "/api/tasks/create", {"title": "bad due", "due": "2026-02-31"})
        assert st == 400 and "due" in str(j.get("error"))
        print("[reject] impossible calendar date -> 400 (%s)" % j.get("error"))

        # --- 6. invalid JSON body rejected ---
        req = urllib.request.Request(
            base + "/api/tasks/create",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("invalid JSON accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("[reject] malformed JSON -> 400")

        # --- 7. existing behaviour preserved: complete still works ---
        st, j = post(base + "/api/tasks/complete", {"id": tid})
        assert st == 200 and j["record"]["status"] == "done"
        assert j["record"]["completed_at"]
        print("[preserve] /api/tasks/complete still works; done task leaves open lists")
        _, state2 = get_json(base + "/api/state")
        allt = (
            state2["today_tasks"]
            + state2["upcoming_tasks"]
            + state2["no_date_tasks"]
            + state2["overdue"]
        )
        assert tid not in [t["id"] for t in allt], "completed task still open in state"

        # --- 8. no arbitrary routes ---
        for path in ("/api/tasks/delete", "/api/exec", "/api/tasks/create/evil"):
            req = urllib.request.Request(base + path, data=b"{}", method="POST")
            try:
                urllib.request.urlopen(req, timeout=10)
                raise AssertionError("unexpected route accepted: " + path)
            except urllib.error.HTTPError as e:
                assert e.code == 404, "%s returned %s" % (path, e.code)
        print("[security] unknown POST routes -> 404")

        # --- 9. localhost-only bind ---
        if shutil.which("ss"):
            out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
            listening = [
                line for line in out.splitlines() if ":%d" % PORT in line and "LISTEN" in line
            ]
        else:
            out = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
            listening = [
                line for line in out.splitlines() if ":%d " % PORT in line and "LISTENING" in line
            ]
        assert listening, "no listener found"
        assert all("127.0.0.1:%d" % PORT in line for line in listening), (
            "dashboard not bound to localhost: %r" % listening
        )
        print("[security] bound to 127.0.0.1 only: %s" % listening[0].strip())

        # --- 10. record count in store ---
        recs = [
            json.loads(line)
            for line in open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8")
            if line.strip()
        ]
        # 2 created ok + the completed one; rejected creates must NOT persist
        assert len(recs) == 2, "rejected creations leaked into the store: %d records" % len(recs)
        print("[integrity] store has exactly %d records (no leaked invalid writes)" % len(recs))

        # === HARDENING TESTS (M1, M2, L1, L5) ===

        # --- 11. M1: internal error returns generic message, no path ---
        # Corrupt a data file so build_state() raises, then hit /api/state.
        with open(os.path.join(home, "data/tasks.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("{ this is not valid json\n")
        st, body, hdrs = raw_request(base + "/api/state", method="GET")
        assert st == 500, "expected 500 on corrupt data, got %s" % st
        assert "internal server error" in body, body
        # no filesystem path, no line number, no raw exception text
        assert home not in body, "response leaked the filesystem path!"
        assert "not valid JSON" not in body, "response leaked raw exception text!"
        assert "Traceback" not in body and "CCError" not in body
        print("[M1] internal error -> generic 500, no path/exception leaked")

        # repair the corrupted data file so later tests work on clean data
        dpath = os.path.join(home, "data/tasks.jsonl")
        lines = [line for line in open(dpath, encoding="utf-8") if line.strip()]
        good = []
        for line in lines:
            try:
                json.loads(line)
                good.append(line)
            except ValueError:
                pass
        with open(dpath, "w", encoding="utf-8") as fh:
            fh.writelines(good)

        # --- 12. M2: legitimate local request accepted (Host + Origin) ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 allowed"},
            headers={
                "Host": "127.0.0.1:%d" % PORT,
                "Origin": "http://127.0.0.1:%d" % PORT,
                "Content-Type": "application/json",
            },
        )
        assert st == 201, "legitimate local request rejected: %s %s" % (st, body)
        print("[M2] local Host + local Origin accepted (201)")

        # --- 12a. M2: the PC's exact MagicDNS hostname is accepted ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 magicdns host"},
            headers={
                "Host": TEST_MAGICDNS_HOST,
                "Origin": "https://" + TEST_MAGICDNS_HOST,
                "Content-Type": "application/json",
            },
        )
        assert st == 201, "MagicDNS Host+Origin rejected: %s %s" % (st, body)
        print("[M2] PC MagicDNS Host + matching Origin accepted (201)")

        # --- 12b. M2: a DIFFERENT tailnet hostname must still be rejected ---
        before = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 wrong tailnet host"},
            headers={
                "Host": "other.example.test",
                "Origin": "https://other.example.test",
                "Content-Type": "application/json",
            },
        )
        assert st == 403, "another tailnet host was accepted: %s %s" % (st, body)
        after = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        assert before == after, "rejected wrong-tailnet request mutated the store!"
        print("[M2] other tailnet hostname -> 403, no write (exact host only)")

        # --- 12c. M2: a WILDCARD/subdomain must NOT match (no wildcarding) ---
        for bad_host in (
            "evil.example.test",
            "dashboard.example.test.evil.com",
            "sub.dashboard.example.test",
        ):
            st, body, hdrs = raw_request(
                base + "/api/tasks/create",
                payload={"title": "M2 wildcard probe"},
                headers={"Host": bad_host, "Content-Type": "application/json"},
            )
            assert st == 403, "wildcard/subdomain %r was accepted: %s" % (bad_host, st)
        print("[M2] subdomain/wildcard Host variants -> 403 (no wildcard match)")

        # --- 12b. M2: request with no Origin accepted (same-origin fetch / curl) ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 no origin"},
            headers={"Host": "127.0.0.1:%d" % PORT, "Content-Type": "application/json"},
        )
        assert st == 201, "no-Origin local request rejected: %s %s" % (st, body)
        print("[M2] local request with no Origin accepted (documented policy)")

        # --- 12c. M2: localhost Host name accepted ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 localhost host"},
            headers={"Host": "localhost:%d" % PORT, "Content-Type": "application/json"},
        )
        assert st == 201, "localhost Host rejected: %s %s" % (st, body)
        print("[M2] Host: localhost accepted")

        # --- 13. M2: unexpected Host rejected (403), no write happens ---
        before = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 evil host"},
            headers={"Host": "evil.example.com", "Content-Type": "application/json"},
        )
        assert st == 403, "unexpected Host accepted: %s %s" % (st, body)
        after = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        assert before == after, "rejected request still mutated the store!"
        print("[M2] unexpected Host -> 403, no write")

        # --- 13b. M2: unexpected Host rejected on GET too ---
        st, body, hdrs = raw_request(
            base + "/api/state", method="GET", headers={"Host": "evil.example.com"}
        )
        assert st == 403, "GET with unexpected Host accepted: %s" % st
        print("[M2] unexpected Host on GET -> 403")

        # --- 14. M2: cross-origin POST rejected (403), no write happens ---
        before = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "M2 cross origin"},
            headers={
                "Host": "127.0.0.1:%d" % PORT,
                "Origin": "http://evil.example.com",
                "Content-Type": "application/json",
            },
        )
        assert st == 403, "cross-origin POST accepted: %s %s" % (st, body)
        after = len(open(os.path.join(home, "data/tasks.jsonl"), encoding="utf-8").readlines())
        assert before == after, "rejected cross-origin request still mutated the store!"
        print("[M2] cross-origin POST -> 403, no write")

        # --- 14b. M2: cross-origin complete rejected ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/complete",
            payload={"id": tid},
            headers={
                "Host": "127.0.0.1:%d" % PORT,
                "Origin": "http://evil.example.com",
                "Content-Type": "application/json",
            },
        )
        assert st == 403, "cross-origin complete accepted: %s %s" % (st, body)
        print("[M2] cross-origin complete -> 403")

        # --- 15. L1: Server header does not disclose Python version ---
        st, body, hdrs = raw_request(base + "/", method="GET")
        server = hdrs.get("Server", "")
        assert "Python" not in server, "Server header leaks Python version: %r" % server
        assert server.strip() == "CommandCentre/0.1", "unexpected Server header: %r" % server
        print("[L1] Server header = %r (no Python version)" % server)

        # --- 16. L5: unexpected exception -> generic 500, no internals ---
        # Corrupt goals.jsonl and POST a create. The store raises a CCError
        # from load_kind, which surfaces as 400. Assert the body contains no
        # filesystem path (the M1 gap found in review: _load_file errors
        # must reference basenames only).
        with open(os.path.join(home, "data/goals.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("{ broken\n")
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "L5 probe"},
            headers={"Host": "127.0.0.1:%d" % PORT, "Content-Type": "application/json"},
        )
        assert st in (400, 500), "unexpected status: %s" % st
        assert "Traceback" not in body and "CCError" not in body
        assert home not in body, "error leaked filesystem path: %s" % body
        assert "goals.jsonl" in body, "expected the filename in the message: %s" % body
        print("[L5/M1] store error -> %s, filename only (no path): %s" % (st, body.strip()[:90]))

        # --- 17. binding unchanged ---
        if shutil.which("ss"):
            out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
            listening = [
                line for line in out.splitlines() if ":%d" % PORT in line and "LISTEN" in line
            ]
        else:
            out = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
            listening = [
                line for line in out.splitlines() if ":%d " % PORT in line and "LISTENING" in line
            ]
        assert listening and all("127.0.0.1:%d" % PORT in line for line in listening), (
            "dashboard not bound to localhost: %r" % listening
        )
        print("[bind] still 127.0.0.1 only: %s" % listening[0].strip())

        # repair any data files earlier fault-injection tests corrupted, so
        # the identity tests below run against clean data
        for _k in ("tasks", "goals", "commitments"):
            _fp = os.path.join(home, "data", _k + ".jsonl")
            _good = []
            for _l in open(_fp, encoding="utf-8"):
                if not _l.strip():
                    continue
                try:
                    json.loads(_l)
                    _good.append(_l)
                except ValueError:
                    pass
            with open(_fp, "w", encoding="utf-8") as _fh:
                _fh.writelines(_good)

        # === IDENTITY GATE TESTS (Tailscale Serve provenance) ===
        MAGIC = TEST_MAGICDNS_HOST
        LOGIN = TEST_LOGIN

        # --- 18. valid Serve request (provenance + correct identity) accepted ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "TS identity ok"},
            headers={
                "Host": MAGIC,
                "Origin": "https://" + MAGIC,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": MAGIC,
                "Tailscale-User-Login": LOGIN,
                "Content-Type": "application/json",
            },
        )
        assert st == 201, "valid Serve request rejected: %s %s" % (st, body)
        print("[identity] Serve request with correct identity -> 201")

        # --- 18b. valid identity on a state-changing complete ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/complete",
            payload={"id": tid},
            headers={
                "Host": MAGIC,
                "Origin": "https://" + MAGIC,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": MAGIC,
                "Tailscale-User-Login": LOGIN,
                "Content-Type": "application/json",
            },
        )
        assert st == 200, "Serve complete with correct identity rejected: %s %s" % (st, body)
        print("[identity] Serve complete with correct identity -> 200")

        # --- 19. missing identity on a Serve request -> rejected ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "no identity"},
            headers={
                "Host": MAGIC,
                "Origin": "https://" + MAGIC,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": MAGIC,
                "Content-Type": "application/json",
            },
        )
        assert st == 403, "Serve request without identity accepted: %s %s" % (st, body)
        print("[identity] Serve request missing identity -> 403")

        # --- 19b. wrong/unexpected identity -> rejected ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "wrong identity"},
            headers={
                "Host": MAGIC,
                "Origin": "https://" + MAGIC,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": MAGIC,
                "Tailscale-User-Login": "attacker@evil.com",
                "Content-Type": "application/json",
            },
        )
        assert st == 403, "Serve request with wrong identity accepted: %s %s" % (st, body)
        print("[identity] Serve request wrong identity -> 403")

        # --- 19c. provenance present but forwarded-host is NOT the PC -> not Serve-mode,
        #         and Host is the MagicDNS name, so it passes Host; identity not required.
        #         This documents that only EXACT provenance triggers the gate. ---
        # (covered implicitly by 12b/12c; here we confirm a mismatched forwarded
        #  host does not accidentally enter Serve mode with a bogus identity)
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "fake provenance"},
            headers={
                "Host": MAGIC,
                "Origin": "https://" + MAGIC,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "evil.example.test",
                "Tailscale-User-Login": "attacker@evil.com",
                "Content-Type": "application/json",
            },
        )
        # forwarded-host != PC -> not Serve-mode -> identity not checked ->
        # Host is the allowed MagicDNS name -> accepted. This is expected:
        # a remote caller cannot set X-Forwarded-Host (Serve strips it), and
        # the tailnet ACL already restricts reachability. Documented behaviour.
        assert st == 201, "mismatched forwarded-host should not enter Serve mode: %s %s" % (
            st,
            body,
        )
        print("[identity] non-PC forwarded-host does NOT trigger identity gate (documented)")

        # --- 20. plain localhost requests are unaffected (local mode) ---
        st, body, hdrs = raw_request(
            base + "/api/tasks/create",
            payload={"title": "local mode ok"},
            headers={"Host": "127.0.0.1:%d" % PORT, "Content-Type": "application/json"},
        )
        assert st == 201, "plain localhost request broke: %s %s" % (st, body)
        print("[identity] plain localhost request unaffected (local mode) -> 201")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(home, ignore_errors=True)

    print("\nALL DASHBOARD TESTS PASSED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
