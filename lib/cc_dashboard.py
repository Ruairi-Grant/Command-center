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
import cc_config  # noqa: E402
import cc_store as cc  # noqa: E402

UI_VERSION = "0.4.0"

CONFIG = cc_config.load()
HOST = CONFIG.bind_host
PORT = CONFIG.port

# Host allowlist. Loopback is always accepted. Remote hostnames must be
# explicitly configured; no personal infrastructure is embedded in source.
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "[::1]")
ALLOWED_HOSTNAMES = frozenset(LOCAL_HOSTNAMES) | frozenset(CONFIG.allowed_hosts)
SERVE_HOSTNAMES = frozenset(CONFIG.allowed_hosts)

# A request with Serve provenance is accepted only when the configured
# identity matches. Production configuration fails closed at startup when
# identity verification is enabled without an expected login.
EXPECTED_TS_LOGIN = CONFIG.expected_tailscale_login

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


# --- Remote-request identity gate ---------------------------------------
# A local process can forge forwarded headers; this check protects the
# configured reverse-proxy boundary, not against local attackers. Exact
# hostname and identity values are supplied through environment configuration.


def _is_serve_request(handler):
    """True when the request shows configured Tailscale Serve provenance."""
    proto = (handler.headers.get("X-Forwarded-Proto") or "").strip().lower()
    fwd_host = (handler.headers.get("X-Forwarded-Host") or "").strip().lower()
    return proto == "https" and fwd_host in SERVE_HOSTNAMES


def _identity_ok(handler):
    """Identity check for Serve-proxied requests. Returns (ok, reason)."""
    login = (handler.headers.get("Tailscale-User-Login") or "").strip()
    if not EXPECTED_TS_LOGIN:
        return False, "identity-not-configured"
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
  .toast.show { opacity: 1; pointer-events: auto; }
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
  .org-open, .task-open { border: 0; background: transparent; color: inherit; cursor: pointer; padding: 0; font: inherit; text-align: left; }
  .org-open:hover, .task-open:hover { text-decoration: underline; }
  .detail-backdrop { position: fixed; inset: 0; display: none; align-items: center; justify-content: center;
    padding: 20px; background: rgba(0,0,0,.62); z-index: 20; }
  .detail-backdrop.open { display: flex; }
  .detail-window { width: min(680px, 100%); max-height: min(760px, 90vh); overflow: auto;
    background: #161a24; border: 1px solid #3a455c; border-radius: 12px; padding: 18px;
    box-shadow: 0 20px 70px rgba(0,0,0,.45); }
  .detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
  .detail-header h2 { margin: 0; font-size: 21px; }
  .detail-meta, .detail-notes, .detail-children { margin-top: 16px; }
  .detail-meta { color: #aeb7c9; font-size: 13px; }
  .detail-notes { white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; min-height: 42px;
    background: #1d2330; border: 1px solid #2b3344; border-radius: 7px; }
  .detail-children h3 { margin: 0 0 8px; font-size: 14px; }
  .detail-child { display: block; width: 100%; margin: 5px 0; padding: 9px 10px; text-align: left;
    background: #1d2330; border: 1px solid #333c4e; border-radius: 6px; color: #e6e8ee; cursor: pointer; }
  .detail-actions { display: flex; justify-content: flex-end; gap: 7px; margin-top: 18px; }
  .detail-actions .org-action { margin-left: 0; font-size: 12px; padding: 6px 10px; }
  .detail-actions .danger { border-color: #8a4545; color: #f4877f; }

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
    <div class="field" style="flex:1 1 140px" id="nt-follow-up-wrap" hidden>
      <label for="nt-follow-up">Follow up</label>
      <input id="nt-follow-up" name="follow_up_on" type="date">
      <button id="nt-clear-follow-up" type="button" class="clear-date">Clear</button>
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
<div class="detail-backdrop" id="org-detail" hidden>
  <section class="detail-window" role="dialog" aria-modal="true" aria-labelledby="org-detail-title">
    <div class="detail-header">
      <h2 id="org-detail-title">Item details</h2>
      <button id="org-detail-close" type="button">Close</button>
    </div>
    <div class="detail-meta" id="org-detail-meta"></div>
    <h3>Notes</h3>
    <div class="detail-notes" id="org-detail-notes"></div>
    <div class="detail-children" id="org-detail-children"></div>
    <div class="detail-actions" id="org-detail-actions"></div>
  </section>
</div>
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
const ntFollowUp = document.getElementById('nt-follow-up');
const ntClearFollowUp = document.getElementById('nt-clear-follow-up');
const ntFollowUpWrap = document.getElementById('nt-follow-up-wrap');
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
const orgDetail = document.getElementById('org-detail');
const orgDetailTitle = document.getElementById('org-detail-title');
const orgDetailMeta = document.getElementById('org-detail-meta');
const orgDetailNotes = document.getElementById('org-detail-notes');
const orgDetailChildren = document.getElementById('org-detail-children');
const orgDetailActions = document.getElementById('org-detail-actions');
const orgDetailClose = document.getElementById('org-detail-close');
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
    + '<button class="task-open" type="button" data-org-open="' + esc(t.id) + '">' + esc(t.title) + '</button>'
    + '<span class="due' + (overdue ? ' over' : '') + '">' + label + '</span>'
    + '<button data-id="' + esc(t.id) + '" data-previous-status="' + esc(t.status || 'active') + '">Done</button>'
    + '</li>';
}

function followUpLabel(t, today) {
  if (!t.follow_up_on) return 'No follow-up date';
  if (t.follow_up_on < today) return 'Follow-up overdue: ' + esc(t.follow_up_on);
  if (t.follow_up_on === today) return 'Follow-up today';
  return 'Follow-up: ' + esc(t.follow_up_on);
}

function waitingTaskLi(t, today) {
  const waitingSince = t.waiting_since ? 'Waiting since: ' + esc(t.waiting_since) : 'Waiting since: ' + esc(t.created || 'unknown');
  return '<li>'
    + '<button class="task-open" type="button" data-org-open="' + esc(t.id) + '">' + esc(t.title) + '</button>'
    + '<span class="waiting-on">Waiting on: ' + esc(t.waiting_on || 'external dependency') + '</span>'
    + '<span class="waiting-since">' + waitingSince + '</span>'
    + '<span class="follow-up">' + followUpLabel(t, today) + '</span>'
    + '<button data-id="' + esc(t.id) + '" data-previous-status="waiting">Done</button>'
    + '</li>';
}

function waitingSection(waiting, followups, today) {
  const scheduled = new Set(followups.map(item => item.id));
  const unscheduled = waiting.filter(item => !scheduled.has(item.id));
  let html = '<h2>Waiting <span class="count">(' + waiting.length + ')</span></h2><div class="card">';
  if (followups.length) {
    html += '<h3>Follow-ups</h3><ul>' + followups.map(i => waitingTaskLi(i, today)).join('') + '</ul>';
  }
  if (unscheduled.length) {
    html += '<h3>No follow-up date</h3><ul>' + unscheduled.map(i => waitingTaskLi(i, today)).join('') + '</ul>';
  }
  if (!waiting.length) html += '<div class="empty">Nothing here.</div>';
  return html + '</div>';
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
  const selectedStatus = ntKindStatus.value;
  const statusOptions = isTask
    ? ['active', 'waiting', 'done', 'cancelled']
    : ['active', 'done', 'cancelled'];
  ntDueWrap.hidden = isArea;
  ntStatusWrap.hidden = isArea;
  ntKindStatus.innerHTML = statusOptions
    .map(v => '<option value="' + v + '">' + v + '</option>').join('');
  ntKindStatus.value = statusOptions.includes(selectedStatus) ? selectedStatus : 'active';
  const isWaiting = isTask && ntKindStatus.value === 'waiting';
  ntWaitingWrap.hidden = !isWaiting;
  ntFollowUpWrap.hidden = !isWaiting;
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

function startUnifiedEdit(node, desiredStatus) {
  if (!node) return;
  ntId.value = node.id;
  ntKind.value = node.kind;
  ntKind.disabled = true;
  ntKindStatus.innerHTML = (node.kind === 'project'
    ? ['active', 'done', 'cancelled']
    : ['active', 'waiting', 'done', 'cancelled'])
    .map(v => '<option value="' + v + '">' + v + '</option>').join('');
  ntKindStatus.value = desiredStatus || node.status || 'active';
  syncNewItemFields();
  document.getElementById('nt-title').value = node.title || '';
  ntDue.value = node.due_source === 'explicit' ? (node.due || '') : '';
  ntWaiting.value = node.waiting_on || '';
  ntFollowUp.value = node.follow_up_on || '';
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
  const wait = n.kind === 'task' && n.status === 'active'
    ? '<button class="org-action" type="button" data-org-wait="' + esc(n.id) + '">Mark waiting</button>' : '';
  const resume = n.kind === 'task' && n.status === 'waiting'
    ? '<button class="org-action" type="button" data-org-resume="' + esc(n.id) + '">Resume</button>' : '';
  return done + wait + resume + '<button class="org-action" type="button" data-org-edit="' + esc(n.id) + '">Edit</button>'
    + '<button class="org-action danger" type="button" data-org-delete="' + esc(n.id) + '">Delete</button>';
}

function openOrganisationDetail(id) {
  const node = findOrganisationNode(currentOrganisation, id);
  if (!node) return;
  orgDetail.dataset.nodeId = node.id;
  orgDetailTitle.textContent = node.title;
  const status = node.kind === 'area' ? '' : ' · ' + (node.status || '');
  const due = node.due ? ' · Due ' + node.due + (node.due_source === 'inherited' ? ' (inherited)' : '') : '';
  const waiting = node.waiting_on ? ' · Waiting on ' + node.waiting_on : '';
  const waitingSince = node.waiting_since ? ' · Waiting since ' + node.waiting_since : '';
  const followUp = node.follow_up_on ? ' · Follow up ' + node.follow_up_on : '';
  orgDetailMeta.textContent = node.kind + status + due + waiting + waitingSince + followUp;
  orgDetailNotes.innerHTML = node.notes ? esc(node.notes) : '<span class="empty">No notes.</span>';
  const children = node.child_headers || node.children || [];
  orgDetailChildren.innerHTML = children.length
    ? '<h3>Children</h3>' + children.map(child =>
        '<button class="detail-child" type="button" data-org-detail-child="' + esc(child.id) + '">' +
        esc(child.title) + ' · ' + esc(child.kind) + (child.status ? ' · ' + esc(child.status) : '') + '</button>').join('')
    : '';
  orgDetailActions.innerHTML = orgControls(node);
  orgDetail.hidden = false;
  orgDetail.classList.add('open');
  orgDetailClose.focus();
}

function closeOrganisationDetail() {
  orgDetail.classList.remove('open');
  orgDetail.hidden = true;
  delete orgDetail.dataset.nodeId;
}

function renderOrganisation(organisation, expandedIds) {
  function renderNode(n) {
    const label = '<button class="org-open" type="button" data-org-open="' + esc(n.id) + '">' + esc(n.title) + '</button>'
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
ntClearFollowUp.addEventListener('click', () => {
  ntFollowUp.value = '';
  ntFollowUp.focus();
});
ntCancel.addEventListener('click', resetUnifiedForm);
orgAdd.addEventListener('click', () => {
  resetUnifiedForm();
  form.scrollIntoView({behavior: 'smooth', block: 'start'});
  document.getElementById('nt-title').focus();
});
async function resumeOrganisationItem(id, button) {
  if (button) button.disabled = true;
  try {
    const r = await fetch('/api/organisation/update', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: id, status: 'active'}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    closeOrganisationDetail();
    say('Resumed');
    load();
  } catch (e) {
    if (button) button.disabled = false;
    say('Resume failed: ' + e.message, true);
  }
}

orgTree.addEventListener('click', async (ev) => {
  const opener = ev.target.closest('button[data-org-open]');
  if (opener) {
    ev.preventDefault();
    ev.stopPropagation();
    openOrganisationDetail(opener.dataset.orgOpen);
    return;
  }
  const wait = ev.target.closest('button[data-org-wait]');
  if (wait) {
    ev.stopPropagation();
    startUnifiedEdit(findOrganisationNode(currentOrganisation, wait.dataset.orgWait), 'waiting');
    return;
  }
  const resume = ev.target.closest('button[data-org-resume]');
  if (resume) {
    ev.stopPropagation();
    await resumeOrganisationItem(resume.dataset.orgResume, resume);
    return;
  }
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

orgDetailClose.addEventListener('click', closeOrganisationDetail);
orgDetail.addEventListener('click', async (ev) => {
  if (ev.target === orgDetail) { closeOrganisationDetail(); return; }
  ev.stopPropagation();
  const child = ev.target.closest('button[data-org-detail-child]');
  if (child) {
    openOrganisationDetail(child.dataset.orgDetailChild);
    return;
  }
  const wait = ev.target.closest('button[data-org-wait]');
  if (wait) {
    startUnifiedEdit(findOrganisationNode(currentOrganisation, wait.dataset.orgWait), 'waiting');
    closeOrganisationDetail();
    return;
  }
  const resume = ev.target.closest('button[data-org-resume]');
  if (resume) {
    await resumeOrganisationItem(resume.dataset.orgResume, resume);
    return;
  }
  const complete = ev.target.closest('button[data-org-complete]');
  if (complete) {
    complete.disabled = true;
    const kind = complete.dataset.orgKind;
    const url = kind === 'task' ? '/api/tasks/complete' : '/api/organisation/update';
    const body = kind === 'task' ? {id: complete.dataset.orgComplete} : {id: complete.dataset.orgComplete, status: 'done'};
    try {
      const r = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      closeOrganisationDetail();
      say('Marked done', false, {id: complete.dataset.orgComplete, kind: kind});
      load();
    } catch (e) { complete.disabled = false; say('Failed: ' + e.message, true); }
    return;
  }
  const del = ev.target.closest('button[data-org-delete]');
  if (del) {
    if (!window.confirm('Delete this item? Children must be moved or deleted first.')) return;
    try {
      const r = await fetch('/api/organisation/delete', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({id: del.dataset.orgDelete}),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      closeOrganisationDetail();
      say('Deleted'); load();
    } catch (e) { say('Delete failed: ' + e.message, true); }
    return;
  }
  const edit = ev.target.closest('button[data-org-edit]');
  if (edit) {
    closeOrganisationDetail();
    startUnifiedEdit(findOrganisationNode(currentOrganisation, edit.dataset.orgEdit));
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !orgDetail.hidden) closeOrganisationDetail();
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
  html += waitingSection(s.waiting || [], s.waiting_followups || [], s.today);
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
  if (kind === 'task') {
    payload.waiting_on = ntWaiting.value.trim() || null;
    if (payload.status === 'waiting') payload.follow_up_on = ntFollowUp.value || null;
  }
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
    const completionUndo = editing && payload.status === 'done' && j.record
      ? {id: j.record.id, kind: kind} : null;
    say(editing ? (completionUndo ? 'Marked done' : 'Saved changes') : 'Added: ' + (j.record ? j.record.title : kind), false, completionUndo);
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
  const opener = ev.target.closest('button[data-org-open]');
  if (opener) {
    ev.preventDefault();
    ev.stopPropagation();
    openOrganisationDetail(opener.dataset.orgOpen);
    return;
  }
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


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


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
                        "next_action", "done_when", "waiting_on", "follow_up_on", "notes", "due", "target"
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
                        "next_action", "done_when", "waiting_on", "follow_up_on", "notes", "due", "target"
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
        _probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _probe.bind((HOST, PORT))
        _probe.close()
    except OSError:
        print("Another dashboard already listening on %s:%d — exiting." % (HOST, PORT),
              flush=True)
        return 0
    srv = ReusableHTTPServer((HOST, PORT), Handler)
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
