# Command Centre Recovery Plan

Status: implemented and verified

## Verified current state

- `master` HEAD is `3640ad5`; the working tree is not clean.
- `lib/cc_dashboard.py` has staged changes that remove the committed Tailscale MagicDNS allowlist, remote identity gate, and duplicate-instance guard. These changes are not part of the intended PROD/DEV UI switch and currently break the existing dashboard security tests.
- Unstaged changes add partial runtime-store support to `lib/cc_store.py`, an incomplete `/api/store/switch` branch to `lib/cc_dashboard.py`, and a PROD default in `run_dashboard.bat`.
- The dashboard HTML currently has no store badge or switch button/handler.
- The current test run passes `test_cc.py` and `test_backups.py`, then fails in `test_dashboard.py` when the exact MagicDNS host is rejected (403 instead of 201). Frontend tests were not reached in that chained run.
- `command-center-prod` and `command-center-dev` are contained inside the
  repository directory and hold the operational stores. No production
  migration of records has been performed.
- Untracked scratch files remain in the repository: `_apply_switch.py`, `_clean_dashboard.py`, `_probe_init_prod.py`, `_probe_store.py`, and `CURRENT_STATE_RECAP.md`.

## Recovery approach

1. Preserve evidence: do not alter live/dev JSONL data, backups, locks, or external Task Scheduler/Tailscale configuration.
2. Reconcile the repository to the last known-good committed security/hosting baseline, retaining only changes required for the approved PROD/DEV switch. Do not silently discard unrelated user work; review and resolve the staged/unstaged changes explicitly.
3. Implement the switch as a runtime-only store selection with exact `prod`/`dev` names, defaulting to the configured PROD store on process start. Never accept an arbitrary filesystem path and never persist the selection.
4. Add a read endpoint for the current store and a state-changing endpoint for switching stores, with explicit route handling and no writes outside `cc_store.py`.
5. Add a visible PROD/DEV badge/button to the page. On switch, update the badge and reload state so the displayed records and subsequent writes come from the selected store. Keep form and completion event handling isolated.
6. Add regression coverage for: default PROD selection; GET current store; valid switch to DEV and back; invalid names/path attempts; writes landing in the selected store; runtime reset on a fresh process; badge/handler wiring; and security/host behavior.
7. Run the complete suites: `test_cc.py`, `test_backups.py`, `test_dashboard.py`, and `test_dashboard_frontend.py`, plus a focused real-server switch test. Verify the working tree diff and that no operational data was changed.
8. Remove scratch files only after confirming they are not needed; do not delete or migrate records/backups without separate approval.

## Scope exclusions

- No migration or cleanup of PROD/DEV data.
- No changes to Tailscale, Task Scheduler, remote access, authentication, or bind address.
- No scheduled jobs, external integrations, or dependency installs.
- No commit, reset, or destructive Git operation unless separately approved.

## Approval gate

Implementation is paused until the user approves this plan. The first implementation milestone will be repository-baseline reconciliation and the runtime store-switch tests; code changes will be reported before proceeding to UI changes.
