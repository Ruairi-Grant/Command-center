# Command Centre — Next-Step Plan

Status: Steps 1-4 COMPLETE and verified. Runtime PROD/DEV dashboard switching COMPLETE and verified. Step 5 deferred.

## Current state (updated 2026-10-12)

- Steps 1-4 of the original plan are DONE and verified.
- The store currently holds TEST records plus real tasks added during
  verification (e.g. "TEST - chat-to-store verification"). No production
  migration of records has been performed.
- The command-centre skill is installed
  (~/AppData/Local/hermes/skills/command-centre/SKILL.md).
- Code is under git (master). Key commits: de8d1ad (initial),
  d3c9ad9 (MagicDNS Host/Origin allowlist for Tailscale Serve).
- Operational data (data/, backups/) and lock files are NOT tracked.

## Built so far (for reference)

- lib/cc_store.py — shared write layer (validation, msvcrt locking, atomic
  writes, backup-before-mutation, append-only activity log, CLI,
  verify-before-prune retention, dedicated backups lock).
- lib/cc_dashboard.py — standalone stdlib dashboard, localhost-only
  (127.0.0.1:8787) plus a task-creation form. Read/state + narrow write
  operations only; no arbitrary execution, no Hermes tool access.
- tests/test_cc.py, test_backups.py, test_dashboard.py,
  test_dashboard_frontend.py — all passing.
- data/*.jsonl — portable human-readable records.

## Completed steps

1. Verify live chat-to-skill-to-store. DONE — task create/read/update/
   complete exercised through the skill against cc_store; verified the
   inbox default fix (status no longer null on create).

2. Dashboard task-creation form. DONE — create form added; all writes go
   through cc_store; new POST /api/tasks/create endpoint.

3. Backup retention and recovery. DONE — verify-before-prune retention
   (keep=20 default, CC_BACKUP_KEEP override); collision-proof same-second
   snapshot naming; atomic snapshot visibility; dedicated backups lock
   (.cc.lock.backups) serialising publish/verify/prune/restore with a fixed
   lock order (backups before data). Race and durability tests pass.

4. Authenticated private remote access. DONE (network-level, no app auth).
   - Dashboard stays bound to 127.0.0.1:8787 (hardcoded, unchanged).
   - Tailscale Serve (tailnet-only, NOT Funnel) proxies
     https://desktop-s7i2r4o.tail7d2d07.ts.net:443 -> http://127.0.0.1:8787.
   - Dashboard Host/Origin allowlist extended with the exact PC MagicDNS
     hostname (no wildcard) so proxied requests are not rejected.
   - Access control is enforced by Tailscale network policy ONLY: tailnet
     ACL restricted to phone (100.70.29.52) -> PC (100.104.50.68) on
     tcp:443. The dashboard authenticates nobody.
   - Phone access verified working by the user.
   - SECURITY NOTE: this design does NOT protect against a malicious
     process already running locally on the PC (it can call
     127.0.0.1:8787 directly and forge any header, incl. Tailscale-* ones).
     This was an explicit accepted trade-off in favour of Tailscale
     network-level control.

## Deferred (step 5) — needs a separate decision

- Scheduled briefings / cron.
- Obsidian integration.
- SQLite migration evaluation.

## Optional follow-ups (not blocking)

- Switch the tailnet ACL from device IPs to tags (tag:cc-phone ->
  tag:cc-pc, tcp:443) so access survives a phone IP change.
- Enable Tailscale device approval in the admin console (not visible from
  the CLI).
- Decide on dashboard auto-start (currently manual: python
  lib/cc_dashboard.py) so it survives reboots.

## Standing constraints (carry forward)

- Operational data stays portable and readable independently of Hermes.
- JSONL now; stable interface allows a future SQLite migration.
- Protect against concurrent updates and partial writes (locking +
  atomic writes already in place).
- Activity log stays append-only, no secrets, no unnecessary personal info.
- Dashboard backend stays narrowly scoped: explicit operations only,
  never arbitrary terminal commands or general Hermes tool execution.
- Dashboard stays local-only until step 4 is explicitly approved.
- Consequential actions (delete, bulk change, restore) require explicit
  approval; routine ops may be autonomous within these boundaries.
- No scheduled jobs, account connections, system-wide package installs,
  Hermes config changes, or external service integrations without approval.

## How to run

  cd C:/Users/ruair/command-center
  python lib/cc_store.py state        # see current data
  python lib/cc_dashboard.py          # local: http://127.0.0.1:8787
                                      # phone: https://desktop-s7i2r4o.tail7d2d07.ts.net
                                      #        (via Tailscale Serve, tailnet-only)

Tests: python tests/test_cc.py ; tests/test_backups.py ;
       tests/test_dashboard.py ; tests/test_dashboard_frontend.py

See README.md for architecture and usage.
