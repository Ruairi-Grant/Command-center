"""Command Centre — dashboard server (stdlib only, localhost-only).

Serves a small read/complete interface over the same JSONL files the
Hermes skill uses. NO arbitrary command execution, NO access to Hermes
tools, NO terminal, NO file writes outside the shared write layer.

Allowed operations only:
  GET  /                  the HTML page
  GET  /api/state         computed state (read-only, safe to cache)
  POST /api/tasks/create  {title, notes?, priority?, due?, tags?}
  POST /api/tasks/complete  {id}  complete one task
  POST /api/commitments/complete {id}

Binds 127.0.0.1 only — never 0.0.0.0. No auth is needed at this phase
because nothing is exposed off-machine. When remote access is approved
later, add auth and a private transport BEFORE changing the bind address.

Security behaviour (hardening, 2026-10):
  - Host headers are validated against a loopback allowlist; unexpected
    hosts get 403.
  - POST requests with a cross-origin Origin are rejected (403); a
    missing Origin is allowed (normal for same-origin fetch / API
    clients). This is explicit validation, not reliance on preflight.
  - Internal errors return a generic message; details are logged
    server-side only. Filesystem paths never reach the client.
  - The Server header does not disclose the Python version.
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cc_store as cc  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("CC_PORT", "8787"))

# Host allowlist (M2). The dashboard is served from localhost, so requests
# must carry a loopback Host header. The port is part of the Host header
# and varies (default 8787, tests use others), so hosts are matched by
# hostname only — the port is stripped before comparison. Override with
# CC_ALLOWED_HOSTS (comma-separated) if a different local hostname is ever
# used deliberately.
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "[::1]")
_extra = [h.strip() for h in os.environ.get("CC_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTNAMES = frozenset(LOCAL_HOSTNAMES) | frozenset(_extra)

# Origin policy for state-changing requests (M2):
#   - A request with no Origin header is allowed. This is the normal case
#     for the dashboard's own fetch() calls and for curl/API clients —
#     browsers only send Origin on cross-origin or form submissions, never
#     on a same-origin fetch(). Missing Origin therefore means "not a
#     browser cross-origin request" and is safe to allow.
#   - If Origin is present it MUST be a loopback origin matching this
#     server's scheme/host/port, otherwise the request is rejected. A
#     cross-origin Origin from any other site is refused.
# Validation is done explicitly here; we do NOT rely on CORS preflight
# behaviour, which is incidental and easily broken by config changes.

MAX_BODY = 10000


def _client_ip(handler):
    return handler.client_address[0] if handler.client_address else "-"


def _log(handler, msg):
    """Server-side diagnostic log. Never includes request bodies or record
    contents — only what is needed to diagnose."""
    sys.stderr.write("[dashboard] %s %s\n" % (_client_ip(handler), msg))


def _host_ok(handler):
    """True if the Host header names a loopback/local hostname."""
    host = (handler.headers.get("Host") or "").strip()
    if not host:
        return False
    # strip port (and IPv6 brackets) -> compare hostname only
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    return hostname in ALLOWED_HOSTNAMES


def _origin_ok(handler):
    """Origin policy for POST requests. Returns (ok, reason)."""
    origin = (handler.headers.get("Origin") or "").strip()
    if not origin:
        return True, "no-origin"          # same-origin fetch / API client
    # Origin looks like scheme://host[:port]
    try:
        rest = origin.split("://", 1)[1]
    except IndexError:
        return False, "malformed origin"
    hostname = rest.rsplit(":", 1)[0].strip("[]").lower()
    if hostname in ALLOWED_HOSTNAMES:
        return True, "local-origin"
    return False, "foreign-origin"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Command Centre</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; background: #10131a; color: #e6e8ee; }
  header { padding: 14px 20px; background: #161a24; border-bottom: 1px solid #262c3a;
           display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 17px; margin: 0; font-weight: 600; letter-spacing: .3px; }
  header .meta { font-size: 12px; color: #8b93a5; }
  main { padding: 18px 20px 40px; max-width: 980px; margin: 0 auto; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1.2px;
       color: #8b93a5; margin: 26px 0 10px; font-weight: 600; }
  h2 .count { color: #5b6478; }
  .card { background: #161a24; border: 1px solid #262c3a; border-radius: 10px;
          padding: 4px 14px; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; align-items: center; gap: 12px; padding: 11px 2px;
       border-bottom: 1px solid #21273480; }
  li:last-child { border-bottom: none; }
  .title { flex: 1; font-size: 14px; }
  .due { font-size: 12px; color: #8b93a5; white-space: nowrap; }
  .due.over { color: #f4877f; font-weight: 600; }
  .prio { font-size: 11px; padding: 2px 8px; border-radius: 20px; white-space: nowrap; }
  .prio.high { background: #3d2226; color: #f4877f; }
  .prio.normal { background: #232a38; color: #9aa4b8; }
  .prio.low { background: #1e2a24; color: #7fd6a4; }
  button { font: inherit; font-size: 12px; padding: 5px 12px; border-radius: 6px;
           border: 1px solid #333c4e; background: #1d2330;
           color: #cfd6e4; cursor: pointer; }
  button:hover { background: #263042; }
  button:disabled { opacity: .5; cursor: default; }
  .empty { color: #5c6478; font-size: 13px; padding: 14px 2px; }
  .toast { position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
           background: #1d2330; border: 1px solid #333c4e; padding: 9px 18px;
           border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity .25s;
           pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast.err { border-color: #f4877f; color: #f4877f; }
  .goal { padding: 10px 2px; border-bottom: 1px solid #21273480; }
  .goal:last-child { border-bottom: none; }
  .goal b { font-size: 14px; font-weight: 600; }
  .goal ul { margin-top: 6px; padding-left: 18px; list-style: disc; }
  .goal li { border: none; padding: 2px 0; font-size: 13px; color: #b6bdcf; }
  .stamp { font-size: 11px; color: #5c6478; }
  form.newtask { background: #161a24; border: 1px solid #262c3a; border-radius: 10px;
                 padding: 14px; display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
  form.newtask .field { display: flex; flex-direction: column; gap: 4px; }
  form.newtask label { font-size: 11px; color: #8b93a5; text-transform: uppercase;
                       letter-spacing: .6px; }
  form.newtask .grow { flex: 1 1 260px; }
  form.newtask input, form.newtask select, form.newtask textarea {
    font: inherit; font-size: 13px; padding: 7px 9px; border-radius: 6px;
    border: 1px solid #333c4e; background: #1d2330; color: #e6e8ee; width: 100%; }
  form.newtask textarea { resize: vertical; min-height: 34px; }
  form.newtask button[type=submit] { font-size: 13px; padding: 7px 16px; }
  form.newtask .req { color: #f4877f; }
  form.newtask .hint { font-size: 11px; color: #5c6478; }
</style>
</head>
<body>
<header>
  <h1>Command Centre</h1>
  <span class="meta" id="meta">loading…</span>
</header>
<main id="app">
  <form class="newtask" id="newtask" autocomplete="off">
    <div class="field grow">
      <label for="nt-title">Task <span class="req">*</span></label>
      <input id="nt-title" name="title" type="text" maxlength="500"
             placeholder="What needs doing?" required>
    </div>
    <div class="field" style="flex:1 1 140px">
      <label for="nt-due">Due</label>
      <input id="nt-due" name="due" type="date">
    </div>
    <div class="field" style="flex:0 0 110px">
      <label for="nt-priority">Priority</label>
      <select id="nt-priority" name="priority">
        <option value="normal" selected>Normal</option>
        <option value="high">High</option>
        <option value="low">Low</option>
      </select>
    </div>
    <div class="field grow">
      <label for="nt-notes">Notes</label>
      <input id="nt-notes" name="notes" type="text" maxlength="2000" placeholder="Optional">
    </div>
    <button type="submit">Add task</button>
    <span class="hint">status defaults to inbox</span>
  </form>
  <div id="list"><div class="empty">Loading…</div></div>
</main>
<div class="toast" id="toast"></div>
<script>
const app = document.getElementById('app');
const list = document.getElementById('list');
const form = document.getElementById('newtask');
const meta = document.getElementById('meta');
const toast = document.getElementById('toast');
let toastTimer;

function say(msg, isErr) {
  toast.textContent = msg;
  toast.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = 'toast', 2200);
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function taskLi(t, today) {
  const overdue = t.due && t.due < today;
  const prio = esc(t.priority || 'normal');
  const label = overdue ? 'Overdue' : (t.due === today ? 'Due today' : esc(t.due || ''));
  return '<li>'
    + '<span class="prio ' + prio + '">' + prio + '</span>'
    + '<span class="title">' + esc(t.title) + '</span>'
    + '<span class="due' + (overdue ? ' over' : '') + '">' + label + '</span>'
    + '<button data-id="' + esc(t.id) + '">Done</button>'
    + '</li>';
}

function commitLi(c, today) {
  const overdue = c.due && c.due < today;
  return '<li>'
    + '<span class="title">' + esc(c.what)
    + (c.whom ? ' <span class="due">with ' + esc(c.whom) + '</span>' : '') + '</span>'
    + '<span class="due' + (overdue ? ' over' : '') + '">' + esc(c.due || '') + '</span>'
    + '<button data-cid="' + esc(c.id) + '">Done</button>'
    + '</li>';
}

function section(title, items, render, today) {
  let html = '<h2>' + esc(title) + ' <span class="count">(' + items.length + ')</span></h2>';
  html += '<div class="card">';
  html += items.length ? '<ul>' + items.map(i => render(i, today)).join('') + '</ul>'
                      : '<div class="empty">Nothing here.</div>';
  html += '</div>';
  return html;
}

async function load() {
  let s;
  try {
    const r = await fetch('/api/state');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    s = await r.json();
  } catch (e) {
    list.innerHTML = '<div class="empty">Could not load state: ' + esc(e.message) + '</div>';
    return;
  }
  meta.textContent = 'Today ' + esc(s.today) + ' · updated ' + esc(s.generated_at.slice(11,16));
  let html = '';
  html += section('Overdue', s.overdue, taskLi, s.today);
  html += section('Due today', s.today_tasks, taskLi, s.today);
  html += section('Upcoming (7 days)', s.upcoming_tasks, taskLi, s.today);
  html += section('No date', s.no_date_tasks, taskLi, s.today);
  html += section('Commitments overdue', s.commitments.overdue, commitLi, s.today);
  html += section('Commitments upcoming', s.commitments.upcoming, commitLi, s.today);
  html += section('Commitments no date', s.commitments.no_date, commitLi, s.today);
  html += '<h2>Active goals <span class="count">(' + s.goals.length + ')</span></h2><div class="card">';
  html += s.goals.length
    ? s.goals.map(g =>
        '<div class="goal"><b>' + esc(g.title) + '</b>'
        + ((g.next_actions || []).length
            ? '<ul>' + g.next_actions.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>'
            : '')
        + '</div>').join('')
    : '<div class="empty">No active goals.</div>';
  html += '</div>';
  list.innerHTML = html;   // render into #list, never clobber the form
}

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  ev.stopPropagation();   // never let the submit reach the delegated click handler
  const titleEl = document.getElementById('nt-title');
  const payload = {
    title: titleEl.value.trim(),
    due: document.getElementById('nt-due').value || undefined,
    priority: document.getElementById('nt-priority').value,
    notes: document.getElementById('nt-notes').value.trim() || undefined,
  };
  if (!payload.title) { say('Title is required', true); return; }
  const btn = form.querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const r = await fetch('/api/tasks/create', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    say('Added: ' + (j.record ? j.record.title : 'task'));
    form.reset();
    document.getElementById('nt-priority').value = 'normal';
    load();
  } catch (e) {
    say('Failed: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
});

// Delegated "Done" buttons. IMPORTANT: the create form lives inside #app,
// so clicks on its controls bubble here — ignore anything in the form,
// and only act on buttons that actually carry a record id.
app.addEventListener('click', async (ev) => {
  if (form.contains(ev.target)) return;      // form handles its own controls
  const b = ev.target.closest('button[data-id], button[data-cid]');
  if (!b) return;
  const taskId = b.dataset.id, commitId = b.dataset.cid;
  const url = taskId ? '/api/tasks/complete' : '/api/commitments/complete';
  const body = JSON.stringify({ id: taskId || commitId });
  b.disabled = true;
  try {
    const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t || ('HTTP ' + r.status));
    }
    say('Marked done');
    load();
  } catch (e) {
    b.disabled = false;
    say('Failed: ' + e.message, true);
  }
});

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CommandCentre/0.1"

    def version_string(self):
        # L1: do not disclose the Python version.
        return self.server_version

    def log_message(self, fmt, *args):  # quieter logs; keep to stderr
        _log(self, fmt % args)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # M1: never return internal exception text, filesystem paths or
    # tracebacks to the client. Log the detail server-side (without
    # record contents or secrets) and return a generic message.
    def _send_error_generic(self, exc, where):
        _log(self, "ERROR in %s: %s: %s" % (where, type(exc).__name__, exc))
        self._send_json({"error": "internal server error"}, 500)

    def _guard(self):
        """M2: Host/Origin gate. Returns False (and has already responded)
        if the request must be rejected."""
        if not _host_ok(self):
            _log(self, "rejected: unexpected Host %r" % (self.headers.get("Host"),))
            self._send_json({"error": "forbidden"}, 403)
            return False
        if self.command == "POST":
            ok, why = _origin_ok(self)
            if not ok:
                _log(self, "rejected: unexpected Origin %r (%s)"
                     % (self.headers.get("Origin"), why))
                self._send_json({"error": "forbidden"}, 403)
                return False
        return True

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return None
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------- routes

    def do_GET(self):
        try:
            if not self._guard():
                return
            if self.path in ("/", "/index.html"):
                self._send_html(PAGE)
            elif self.path.startswith("/api/state"):
                try:
                    self._send_json(cc.build_state())
                except cc.CCError as exc:
                    # M1: expected application error -> generic to client,
                    # detail logged server-side.
                    _log(self, "state error: %s" % exc)
                    self._send_json({"error": "internal server error"}, 500)
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:  # L5: unexpected errors never reach the client
            self._send_error_generic(exc, "do_GET")

    def do_POST(self):
        # Only these routes exist. Nothing else executes — no shell, no
        # filesystem access, no Hermes tools. All writes go through cc_store.
        try:
            if not self._guard():
                return
            body = self._read_json()
            if not isinstance(body, dict):
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            try:
                if self.path == "/api/tasks/create":
                    fields = {}
                    for k in ("title", "notes", "priority", "due", "tags"):
                        if k in body and body[k] not in (None, ""):
                            fields[k] = body[k]
                    # status is deliberately NOT accepted from the dashboard:
                    # new tasks always start at the store's default (inbox).
                    rec = cc.create_task("dashboard", **fields)
                    self._send_json({"ok": True, "record": rec}, 201)
                    return

                rid = body.get("id")
                if self.path == "/api/tasks/complete":
                    if not isinstance(rid, str):
                        self._send_json({"error": "missing id"}, 400)
                        return
                    rec = cc.complete_task("dashboard", rid)
                elif self.path == "/api/commitments/complete":
                    if not isinstance(rid, str):
                        self._send_json({"error": "missing id"}, 400)
                        return
                    rec = cc.complete_commitment("dashboard", rid)
                else:
                    self._send_json({"error": "not found"}, 404)
                    return
                self._send_json({"ok": True, "record": rec})
            except cc.CCError as exc:
                # Expected validation error: safe to surface the message
                # (it contains field names and values, never paths).
                self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # L5
            self._send_error_generic(exc, "do_POST")


def main():
    cc.init()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("Command Centre dashboard: http://%s:%d  (localhost only)" % (HOST, PORT))
    print("Data home: %s" % cc.get_home())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
