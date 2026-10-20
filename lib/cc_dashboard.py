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
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cc_store as cc  # noqa: E402

UI_VERSION = "0.4.0"

HOST = "127.0.0.1"
PORT = int(os.environ.get("CC_PORT", "8787"))

# Host allowlist. The dashboard is served from localhost, so requests
# normally carry a loopback Host header. The port is part of the Host
# header and varies (default 8787, tests use others), so hosts are matched
# by hostname only — the port is stripped before comparison.
#
# PC_MAGICDNS_HOST is this machine's exact Tailscale MagicDNS hostname.
# It is added so that requests proxied through Tailscale Serve (which
# forwards the tailnet hostname) are not rejected by the Host/Origin
# checks. This is an EXACT hostname, never a wildcard. NOTE: adding it
# here is NOT authentication — it only stops the anti-cross-origin checks
# from blocking legitimate proxied traffic. Access control is a separate,
# still-pending concern (see Phase 4).
PC_MAGICDNS_HOST = "desktop-s7i2r4o.tail7d2d07.ts.net"
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "[::1]")
_extra = [h.strip() for h in os.environ.get("CC_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTNAMES = frozenset(LOCAL_HOSTNAMES) | {PC_MAGICDNS_HOST} | frozenset(_extra)

# Origin policy for state-changing requests:
#   - A request with no Origin header is allowed. This is the normal case
#     for the dashboard's own fetch() calls and for curl/API clients —
#     browsers only send Origin on cross-origin or form submissions, never
#     on a same-origin fetch(). Missing Origin therefore means "not a
#     browser cross-origin request" and is safe to allow.
#   - If Origin is present it MUST name an allowed hostname: loopback, or
#     the PC's exact MagicDNS hostname (so the page served through
#     Tailscale Serve can make its own same-origin fetch() calls). Any
#     other cross-origin request is refused.
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


# --- Remote-request identity gate (Tailscale Serve) ---------------------
#
# The dashboard has NO application login. Remote access is protected by
# requiring that requests arriving THROUGH Tailscale Serve carry the
# intended Tailscale identity. Design and its limits, established by
# empirical test against the installed Tailscale 1.104.1:
#
#   * Serve STRIPS any client-supplied Tailscale-User-Login /
#     X-Forwarded-For headers and replaces them with the real ones, so a
#     remote tailnet caller cannot forge the identity through Serve.
#   * A local process can still hit 127.0.0.1:8787 directly and forge any
#     header. So an identity header ALONE is NOT proof of provenance.
#
# Therefore we only enforce identity on requests that show Serve
# provenance: X-Forwarded-Proto == https AND X-Forwarded-Host == the PC's
# MagicDNS hostname. Such requests MUST also carry
# Tailscale-User-Login == EXPECTED_TS_LOGIN. Plain localhost requests
# (no such forwarded headers) are local-mode and behave as before.
#
# A local attacker CAN set these forwarded headers themselves to enter
# remote mode and present a valid-looking identity — this gate does NOT
# stop a malicious local process (documented, accepted limitation). It
# DOES stop every remote tailnet device except the intended identity,
# which is the boundary this feature is responsible for.
EXPECTED_TS_LOGIN = "ruairi.j.grant@gmail.com"


def _is_serve_request(handler):
    """True when the request shows Tailscale Serve provenance."""
    proto = (handler.headers.get("X-Forwarded-Proto") or "").strip().lower()
    fwd_host = (handler.headers.get("X-Forwarded-Host") or "").strip().lower()
    return proto == "https" and fwd_host == PC_MAGICDNS_HOST


def _identity_ok(handler):
    """Identity check for Serve-proxied requests. Returns (ok, reason)."""
    login = (handler.headers.get("Tailscale-User-Login") or "").strip()
    if not login:
        return False, "missing-identity"
    if login.lower() != EXPECTED_TS_LOGIN.lower():
        return False, "unexpected-identity"
    return True, "ok"

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
  header .store { margin-left: auto; font-weight: 700; }
  header .store.prod { color: #7fd6a4; }
  header .store.dev { color: #f4c77f; }
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
  .stamp { font-size: 11px; color: #5c6478; }
  .organisation { margin: 18px 0 24px; }
  .organisation h2 { margin-top: 0; }
  .org-tree { background: #161a24; border: 1px solid #262c3a; border-radius: 10px; padding: 20px; }
  .org-node { margin: 10px 0; padding: 10px 12px; background: #1b202c; border: 1px solid #2b3344; border-radius: 8px; }
  .org-node > summary { cursor: pointer; list-style-position: outside; padding: 3px 0; }
  .org-node > summary::marker { color: #68738a; }
  .org-label { font-size: 15px; }
  .org-kind, .org-status { font-size: 11px; margin-left: 8px; padding: 3px 7px; border-radius: 10px; color: #aeb7c9; background: #2a3242; }
  .org-status.waiting { color: #f4c77f; }
  .org-waiting { margin: 8px 0 6px 22px; font-size: 13px; color: #a3adbf; }
  .org-due { margin: 8px 0 6px 22px; font-size: 13px; color: #d4b978; }
  .org-children { margin: 12px 0 0 20px; }
  .org-action { font-size: 10px; padding: 2px 6px; margin-left: 5px; }
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
<header data-ui-version="__UI_VERSION__">
  <h1>Command Centre <span class="ui-version">UI v__UI_VERSION__</span></h1>
  <span class="meta" id="meta">loading…</span>
  <button class="store" id="store" type="button" title="Switch data store">STORE: loading…</button>
</header>
<main id="app">
  <form class="newtask" id="newtask" autocomplete="off">
    <input id="nt-id" type="hidden">
    <div class="field" style="flex:0 0 130px">
      <label for="nt-kind">Item type</label>
      <select id="nt-kind" name="kind">
        <option value="task" selected>Task</option>
        <option value="project">Project</option>
        <option value="area">Area</option>
      </select>
    </div>
    <div class="field grow">
      <label id="nt-title-label" for="nt-title">Title <span class="req">*</span></label>
      <input id="nt-title" name="title" type="text" maxlength="500"
             placeholder="What needs doing?" required>
    </div>
    <div class="field" style="flex:1 1 140px" id="nt-due-wrap">
      <label for="nt-due">Due</label>
      <input id="nt-due" name="due" type="date">
      <button id="nt-clear-due" type="button" class="clear-date">Clear</button>
    </div>
    <div class="field" style="flex:0 0 140px" id="nt-status-wrap">
      <label for="nt-status">Status</label>
      <select id="nt-status" name="status"></select>
    </div>
    <div class="field" style="flex:0 0 190px">
      <label for="nt-parent">Parent</label>
      <select id="nt-parent" name="parent_id"><option value="">No parent</option></select>
    </div>
    <div class="field grow" id="nt-waiting-wrap" hidden>
      <label for="nt-waiting">Waiting on</label>
      <input id="nt-waiting" name="waiting_on" maxlength="1000" placeholder="Person, system, or event">
    </div>
    <div class="field grow">
      <label for="nt-notes">Notes</label>
      <input id="nt-notes" name="notes" type="text" maxlength="2000" placeholder="Optional">
    </div>
    <button id="nt-submit" type="submit">Add item</button>
    <button id="nt-cancel" type="button" hidden>Cancel edit</button>
    <span class="hint">Create an area, project, task, or subtask</span>
  </form>
  <section class="organisation" id="organisation">
    <h2>Organisation <button id="org-add" class="org-action" type="button">Add item</button></h2>
    <div class="org-tree" id="organisation-tree"><div class="empty">Loading…</div></div>
  </section>
  <div id="list"><div class="empty">Loading…</div></div>
</main>
<div class="toast" id="toast"></div>
<script>
const app = document.getElementById('app');
const list = document.getElementById('list');
const form = document.getElementById('newtask');
const ntId = document.getElementById('nt-id');
const ntKind = document.getElementById('nt-kind');
const ntKindStatus = document.getElementById('nt-status');
const ntDue = document.getElementById('nt-due');
const ntClearDue = document.getElementById('nt-clear-due');
const ntDueWrap = document.getElementById('nt-due-wrap');
const ntStatusWrap = document.getElementById('nt-status-wrap');
const ntWaitingWrap = document.getElementById('nt-waiting-wrap');
const ntWaiting = document.getElementById('nt-waiting');
const ntParent = document.getElementById('nt-parent');
const ntSubmit = document.getElementById('nt-submit');
const ntCancel = document.getElementById('nt-cancel');
const orgTree = document.getElementById('organisation-tree');
const orgAdd = document.getElementById('org-add');
const meta = document.getElementById('meta');
const store = document.getElementById('store');
const toast = document.getElementById('toast');
let toastTimer;
let currentOrganisation = [];

function say(msg, isErr, undo) {
  toast.innerHTML = esc(msg);
  if (undo) toast.innerHTML += ' <button type="button" data-undo-id="' + esc(undo.id) + '" data-undo-kind="' + esc(undo.kind) + '">Undo</button>';
  toast.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = 'toast', 5000);
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
    + '<button data-id="' + esc(t.id) + '" data-previous-status="' + esc(t.status || 'active') + '">Done</button>'
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

function syncNewItemFields() {
  const kind = ntKind.value;
  const isTask = kind === 'task';
  const isArea = kind === 'area';
  ntDueWrap.hidden = isArea;
  ntStatusWrap.hidden = isArea;
  ntWaitingWrap.hidden = !isTask || ntKindStatus.value !== 'waiting';
  ntKindStatus.innerHTML = (isTask
    ? ['active', 'waiting', 'done', 'cancelled']
    : ['active', 'done', 'cancelled'])
    .map(v => '<option value="' + v + '">' + v + '</option>').join('');
  if (!ntKindStatus.value) ntKindStatus.value = 'active';
  ntWaitingWrap.hidden = !isTask || ntKindStatus.value !== 'waiting';
}

function resetUnifiedForm() {
  ntId.value = '';
  form.reset();
  ntKind.value = 'task';
  ntKind.disabled = false;
  ntSubmit.textContent = 'Add item';
  ntCancel.hidden = true;
  syncNewItemFields();
  ntParent.innerHTML = buildParentOptions(currentOrganisation, 'task', '');
}

function startUnifiedEdit(node) {
  if (!node) return;
  ntId.value = node.id;
  ntKind.value = node.kind;
  ntKind.disabled = true;
  ntKindStatus.innerHTML = (node.kind === 'project'
    ? ['active', 'done', 'cancelled']
    : ['active', 'waiting', 'done', 'cancelled'])
    .map(v => '<option value="' + v + '">' + v + '</option>').join('');
  ntKindStatus.value = node.status || 'active';
  syncNewItemFields();
  document.getElementById('nt-title').value = node.title || '';
  ntDue.value = node.due_source === 'explicit' ? (node.due || '') : '';
  ntWaiting.value = node.waiting_on || '';
  document.getElementById('nt-notes').value = node.notes || '';
  ntParent.innerHTML = buildParentOptions(currentOrganisation, node.kind, node.id);
  ntParent.value = node.parent_id || '';
  ntSubmit.textContent = 'Save changes';
  ntCancel.hidden = false;
  form.scrollIntoView({behavior: 'smooth', block: 'start'});
  document.getElementById('nt-title').focus();
}

function orgControls(n) {
  const done = n.kind !== 'area' && n.status !== 'done' && n.status !== 'cancelled'
    ? '<button class="org-action" type="button" data-org-complete="' + esc(n.id) + '" data-org-kind="' + esc(n.kind) + '">Done</button>' : '';
  return done + '<button class="org-action" type="button" data-org-edit="' + esc(n.id) + '">Edit</button>'
    + '<button class="org-action danger" type="button" data-org-delete="' + esc(n.id) + '">Delete</button>';
}

function renderOrganisation(organisation, expandedIds) {
  function renderNode(n) {
    const label = '<span class="org-label">' + esc(n.title) + '</span>'
      + '<span class="org-kind">' + esc(n.kind) + '</span>'
      + (n.kind === 'area' ? '' : '<span class="org-status ' + esc(n.status || '') + '">' + esc(n.status || '') + '</span>')
      + orgControls(n);
    const details = (n.due ? '<div class="org-due">Due: ' + esc(n.due) + (n.due_source === 'inherited' ? ' (inherited)' : '') + '</div>' : '')
      + (n.waiting_on ? '<div class="org-waiting">Waiting on: ' + esc(n.waiting_on) + '</div>' : '');
    if (!n.children.length) return '<div class="org-node">' + label + details + '</div>';
    const open = !expandedIds || expandedIds.has(n.id) ? ' open' : '';
    return '<details class="org-node" data-org-id="' + esc(n.id) + '"' + open + '><summary>' + label + '</summary>'
      + details + '<div class="org-children">' + n.children.map(renderNode).join('') + '</div></details>';
  }
  return organisation.map(renderNode).join('') || '<div class="empty">No organisation items yet.</div>';
}

function buildParentOptions(organisation, kind, excludeId) {
  const options = ['<option value="">No parent</option>'];
  const allowed = kind === 'project' ? ['area'] : (kind === 'task' ? ['area', 'project', 'task'] : []);
  const byId = {};
  function index(nodes) {
    nodes.forEach(n => { byId[n.id] = n; index(n.children || []); });
  }
  index(organisation);
  function walk(nodes, prefix) {
    nodes.forEach(n => {
      const parent = n.parent_id ? byId[n.parent_id] : null;
      const isSubtask = n.kind === 'task' && parent && parent.kind === 'task';
      if (n.id !== excludeId && allowed.includes(n.kind) && !isSubtask) {
        options.push('<option value="' + esc(n.id) + '">' + esc(prefix + n.title) + '</option>');
        walk(n.children || [], prefix + '↳ ');
      }
    });
  }
  walk(organisation, '');
  return options.join('');
}

function populateParentOptions(organisation) {
  const selected = ntParent.value;
  ntParent.innerHTML = buildParentOptions(organisation, ntKind.value, ntId.value);
  if ([...ntParent.options].some(option => option.value === selected)) ntParent.value = selected;
}

function findOrganisationNode(nodes, id) {
  for (const n of nodes) {
    if (n.id === id) return n;
    const found = findOrganisationNode(n.children || [], id);
    if (found) return found;
  }
  return null;
}

ntKind.addEventListener('change', () => {
  syncNewItemFields();
  populateParentOptions(currentOrganisation);
});
ntKindStatus.addEventListener('change', syncNewItemFields);
ntClearDue.addEventListener('click', () => {
  ntDue.value = '';
  ntDue.focus();
});
ntCancel.addEventListener('click', resetUnifiedForm);
orgAdd.addEventListener('click', () => {
  resetUnifiedForm();
  form.scrollIntoView({behavior: 'smooth', block: 'start'});
  document.getElementById('nt-title').focus();
});
orgTree.addEventListener('click', async (ev) => {
  const complete = ev.target.closest('button[data-org-complete]');
  if (complete) {
    ev.stopPropagation();
    complete.disabled = true;
    const kind = complete.dataset.orgKind;
    const url = kind === 'task' ? '/api/tasks/complete' : '/api/organisation/update';
    const body = kind === 'task' ? {id: complete.dataset.orgComplete} : {id: complete.dataset.orgComplete, status: 'done'};
    try {
      const r = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      say('Marked done', false, {id: complete.dataset.orgComplete, kind: kind});
      load();
    } catch (e) { complete.disabled = false; say('Failed: ' + e.message, true); }
    return;
  }
  const del = ev.target.closest('button[data-org-delete]');
  if (del) {
    ev.stopPropagation();
    if (!window.confirm('Delete this item? Children must be moved or deleted first.')) return;
    try {
      const r = await fetch('/api/organisation/delete', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({id: del.dataset.orgDelete}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      say('Deleted'); load();
    } catch (e) { say('Delete failed: ' + e.message, true); }
    return;
  }
  const edit = ev.target.closest('button[data-org-edit]');
  if (edit) {
    ev.stopPropagation();
    startUnifiedEdit(findOrganisationNode(currentOrganisation, edit.dataset.orgEdit));
  }
});

async function load() {
  const scrollY = window.scrollY;
  const expandedIds = orgTree.dataset.rendered
    ? new Set([...orgTree.querySelectorAll('details[data-org-id][open]')].map(node => node.dataset.orgId))
    : null;
  let s;
  try {
    const r = await fetch('/api/state');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    s = await r.json();
  } catch (e) {
    list.innerHTML = '<div class="empty">Could not load state: ' + esc(e.message) + '</div>';
    return;
  }
  meta.textContent = 'Today ' + new Date().toLocaleDateString() + ' · refreshed ' + new Date(s.generated_at).toLocaleString();
  currentOrganisation = s.organisation || [];
  populateParentOptions(currentOrganisation);
  orgTree.innerHTML = renderOrganisation(currentOrganisation, expandedIds);
  orgTree.dataset.rendered = '1';
  window.scrollTo(0, scrollY);
  updateStore();
  let html = '';
  html += section('Waiting', s.waiting || [], taskLi, s.today);
  html += section('Overdue', s.overdue, taskLi, s.today);
  html += section('Due today', s.today_tasks, taskLi, s.today);
  html += section('Upcoming (7 days)', s.upcoming_tasks, taskLi, s.today);
  list.innerHTML = html;   // render into #list, never clobber the form
}

async function updateStore() {
  try {
    const r = await fetch('/api/store/switch');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const name = String(j.store || '').toUpperCase();
    store.textContent = 'STORE: ' + name;
    store.className = 'store ' + String(j.store || '').toLowerCase();
    store.dataset.store = String(j.store || '').toLowerCase();
  } catch (e) {
    store.textContent = 'STORE: unavailable';
    store.className = 'store';
  }
}

store.addEventListener('click', async () => {
  const current = store.dataset.store;
  const next = current === 'dev' ? 'prod' : 'dev';
  store.disabled = true;
  try {
    const r = await fetch('/api/store/switch', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({store: next}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    await updateStore();
    await load();
    say('Switched to ' + next.toUpperCase());
  } catch (e) {
    say('Store switch failed: ' + e.message, true);
  } finally {
    store.disabled = false;
  }
});

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  ev.stopPropagation();   // never let the submit reach the delegated click handler
  const kind = ntKind.value;
  const titleEl = document.getElementById('nt-title');
  const payload = {
    kind: kind,
    title: titleEl.value.trim(),
    parent_id: ntParent.value || null,
    notes: document.getElementById('nt-notes').value.trim() || null,
  };
  if (kind !== 'area') {
    payload.due = document.getElementById('nt-due').value || null;
    payload.status = ntKindStatus.value;
  }
  if (kind === 'task') payload.waiting_on = ntWaiting.value.trim() || null;
  if (kind === 'task' && payload.status === 'waiting' && !payload.waiting_on) {
    say('Waiting on is required for waiting tasks', true); return;
  }
  if (!payload.title) { say('Title is required', true); return; }
  const editing = Boolean(ntId.value);
  const url = editing ? '/api/organisation/update' : '/api/organisation/create';
  if (editing) payload.id = ntId.value;
  const btn = form.querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const r = await fetch(url, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    say(editing ? 'Saved changes' : 'Added: ' + (j.record ? j.record.title : kind));
    resetUnifiedForm();
    load();
  } catch (e) {
    say('Failed: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
});

toast.addEventListener('click', async (ev) => {
  const undo = ev.target.closest('button[data-undo-id]');
  if (!undo) return;
  undo.disabled = true;
  const kind = undo.dataset.undoKind;
  const url = kind === 'task' ? '/api/tasks/restore' : '/api/organisation/update';
  const body = kind === 'task' ? {id: undo.dataset.undoId} : {id: undo.dataset.undoId, status: 'active'};
  try {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    say('Restored');
    load();
  } catch (e) { say('Undo failed: ' + e.message, true); }
});


// so clicks on its controls bubble here — ignore anything in the form,
// and only act on buttons that actually carry a record id.
app.addEventListener('click', async (ev) => {
  if (form.contains(ev.target)) return;      // form handles its own controls
  const b = ev.target.closest('button[data-id]');
  if (!b) return;
  const taskId = b.dataset.id;
  const url = '/api/tasks/complete';
  const body = JSON.stringify({ id: taskId });
  b.disabled = true;
  try {
    const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t || ('HTTP ' + r.status));
    }
    say('Marked done', false, {id: taskId, kind: 'task'});
    load();
  } catch (e) {
    b.disabled = false;
    say('Failed: ' + e.message, true);
  }
});

syncNewItemFields();
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
        """Host/Origin gate + Serve identity gate. Returns False (and has
        already responded) if the request must be rejected."""
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
        # Serve-proxied requests must also present the intended identity.
        # Plain localhost requests are unaffected.
        if _is_serve_request(self):
            ok, why = _identity_ok(self)
            if not ok:
                _log(self, "rejected: identity check failed (%s) login=%r"
                     % (why, self.headers.get("Tailscale-User-Login")))
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
                self._send_html(PAGE.replace("__UI_VERSION__", UI_VERSION))
            elif self.path == "/api/store/switch":
                self._send_json({"store": cc.current_store_name()})
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
            if self.path == "/api/store/switch":
                body = self._read_json()
                if not isinstance(body, dict):
                    self._send_json({"error": "invalid JSON body"}, 400)
                    return
                name = body.get("store")
                if not isinstance(name, str) or name not in cc.STORE_NAMES:
                    self._send_json({"error": "store must be 'prod' or 'dev'"}, 400)
                    return
                cc.set_store(name)
                cc.init()
                self._send_json({"ok": True, "store": cc.current_store_name()})
                return
            body = self._read_json()
            if not isinstance(body, dict):
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            try:
                if self.path == "/api/organisation/create":
                    fields = {k: body[k] for k in (
                        "title", "kind", "parent_id", "status", "priority",
                        "next_action", "done_when", "waiting_on", "notes", "due", "target"
                    ) if k in body}
                    rec = cc.create_organisation("dashboard", **fields)
                    self._send_json({"ok": True, "record": rec}, 201)
                    return
                if self.path == "/api/organisation/update":
                    rid = body.get("id")
                    if not isinstance(rid, str):
                        self._send_json({"error": "missing id"}, 400)
                        return
                    fields = {k: body[k] for k in (
                        "title", "kind", "parent_id", "status", "priority",
                        "next_action", "done_when", "waiting_on", "notes", "due", "target"
                    ) if k in body}
                    rec = cc.update_organisation("dashboard", rid, **fields)
                    self._send_json({"ok": True, "record": rec})
                    return
                if self.path == "/api/organisation/delete":
                    rid = body.get("id")
                    if not isinstance(rid, str):
                        self._send_json({"error": "missing id"}, 400)
                        return
                    rec = cc.delete_organisation("dashboard", rid)
                    self._send_json({"ok": True, "record": rec})
                    return
                if self.path == "/api/tasks/restore":
                    rid = body.get("id")
                    if not isinstance(rid, str):
                        self._send_json({"error": "missing id"}, 400)
                        return
                    rec = cc.restore_task("dashboard", rid)
                    self._send_json({"ok": True, "record": rec})
                    return
                if self.path == "/api/tasks/create":
                    fields = {}
                    for k in ("title", "notes", "priority", "due", "tags", "parent_id"):
                        if k in body and body[k] not in (None, ""):
                            fields[k] = body[k]
                    # new tasks always start at active unless changed later.
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
    # Duplicate-instance guard: if something already holds our loopback
    # port, this is a second launch. Exit cleanly (0) rather than crash,
    # so Task Scheduler restart logic and manual double-starts stay tidy
    # and never disturb the healthy running instance.
    try:
        _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _probe.bind((HOST, PORT))
        _probe.close()
    except OSError:
        print("Another dashboard already listening on %s:%d — exiting." % (HOST, PORT),
              flush=True)
        return 0
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
