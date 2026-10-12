# Command Centre — Next-Step Plan

Status: agreed plan, awaiting approval to execute. No steps below have been run.

## Current state (as of 2026-10-10)

- Phase 0 and the local dashboard portion of Phase 1 are COMPLETE.
- The dashboard server is STOPPED (the test process was killed deliberately).
- Only TEST records exist in the store: 2 tasks, 1 goal, 0 commitments.
  All are clearly labelled "Test ..." and contain no real personal data.
- Chat-to-store has NOT yet been verified end-to-end: task capture so far
  was demonstrated via the cc_store.py CLI, not via a live Hermes chat
  message routed through the command-centre skill.
- The command-centre skill is installed and loaded
  (~/AppData/Local/hermes/skills/command-centre/SKILL.md).
- No Hermes configuration was changed. No packages installed. No accounts
  connected. No scheduled jobs created. No external integrations.

## Built so far (for reference)

- lib/cc_store.py — shared write layer (validation, msvcrt locking, atomic
  writes, backup-before-mutation, append-only activity log, CLI).
- lib/cc_dashboard.py — standalone stdlib dashboard, localhost-only
  (127.0.0.1:8787). Read-only state + complete operations only; no
  arbitrary execution, no Hermes tool access.
- tests/test_cc.py — concurrency + durability test (passing).
- data/*.jsonl — portable human-readable records (test data only).

## Planned steps (in order; each gated on the previous being tested)

1. Verify live chat-to-skill-to-store.
   - Exercise task creation, read-back, partial update and completion via
     an actual Hermes chat turn using the command-centre skill.
   - Use clearly identified test data (e.g. titles prefixed "TEST-").
   - Confirm each action lands as a structured record, updates correctly,
     and is written to the activity log with the right actor.

2. Add a dashboard task-creation form.
   - A minimal create-task UI on the dashboard.
   - ALL writes routed through cc_store.py (same validation, locking,
     atomic writes, backup, activity log). No direct file writes.

3. Implement backup retention and verify recovery safety.
   - Backups are currently one snapshot per mutation (wasteful at volume).
   - Add retention/pruning, then re-verify restore reproduces a snapshot
     exactly and reverts post-backup changes.

4. Evaluate authenticated private remote access.
   - ONLY after steps 1-3 are tested.
   - Add authentication and a private transport (VPN or authenticated
     reverse proxy) BEFORE any non-localhost binding. No public URL,
     no port forwarding, no exposure without explicit approval.

5. DEFERRED (not to be started without a separate decision):
   - Scheduled briefings / cron.
   - Obsidian integration.
   - SQLite migration evaluation.

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

## How to resume

  cd C:/Users/ruair/command-center
  python lib/cc_store.py state        # see current test data
  python lib/cc_dashboard.py          # then open http://127.0.0.1:8787

Then approve step 1 to begin.
