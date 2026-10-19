# Command Centre

A small personal command centre for task and life administration. A
shared file-based store, a localhost web dashboard, and (optionally)
secure phone access over a private Tailscale network.

Everything is plain Python standard library — no external packages, no
database server, no framework. Data is stored as portable, human-readable
JSONL files you can read and edit with any text editor.

## Architecture

```
 Phone (browser, on the tailnet)
    |  https://desktop-s7i2r4o.tail7d2d07.ts.net
    v
 Tailscale Serve  (tailnet-only reverse proxy, terminates TLS)
    |  proxies over loopback
    v
 Dashboard  lib/cc_dashboard.py   -- binds 127.0.0.1:8787 ONLY
    |  every write goes through the shared layer
    v
 Store      lib/cc_store.py       -- validation, locking, atomic writes,
    |                               backups, activity log
    v
 Data       data/*.jsonl  +  backups/  +  activity log
```

Two independent components share one write layer:

- `lib/cc_store.py` — the single source of truth for all mutations.
  Used by BOTH the dashboard and the Hermes `command-centre` skill, so
  write logic is never duplicated. It validates every record, serialises
  concurrent writers with a cross-process lock, writes atomically
  (temp file + fsync + rename), snapshots the data before every mutation,
  prunes old snapshots on a keep-N policy, and appends every action to an
  activity log.
- `lib/cc_dashboard.py` — a standalone stdlib HTTP server. It never
  touches the data files directly; it calls `cc_store`. It exposes only a
  handful of explicit operations (no shell, no arbitrary file access),
  including the runtime-only PROD/DEV store switch.
- `tests/test_cc.py`, `test_backups.py`, `test_dashboard.py`,
  `test_dashboard_frontend.py`, and `test_dashboard_store_switch.py` cover
  the store, dashboard, frontend wiring, and isolated store switching.

The Hermes `command-centre` skill (installed separately) drives the same
`cc_store` layer from chat, so capturing a task in conversation and
ticking it off on the dashboard operate on the same records.

## Data

Stored under `data/` as JSONL (one JSON object per line):

- `tasks.jsonl` — `{id, title, notes, status, priority, due, goal_id,
  tags, created, updated, completed_at}`
- `goals.jsonl` — `{id, title, status, next_actions, notes, created}`
- `commitments.jsonl` — `{id, what, whom, due, done, notes, created}`
- `activity.jsonl` — append-only log of every change:
  `{ts, actor, action, target, detail}` (actor is `cli`, `dashboard`,
  etc.)

Backups live under `backups/` — one timestamped snapshot of `data/` is
taken before every mutation. Retention keeps the most recent 20 snapshots
(override with `CC_BACKUP_KEEP`). These directories are intentionally NOT
in git; the data is yours and portable, not source code.

## Running it

Local (on the PC):

```
cd C:/git_repos/command-center
python lib/cc_dashboard.py
# open http://127.0.0.1:8787
```

The dashboard always binds to `127.0.0.1` — it is never exposed on the
network by itself.

From your phone (optional, via Tailscale):

- Tailscale Serve is configured to proxy
  `https://desktop-s7i2r4o.tail7d2d07.ts.net` (tailnet only) to
  `http://127.0.0.1:8787`.
- Open that URL in your phone's browser while connected to the tailnet.
- **Turn OFF "Private DNS" on the phone** (Android: Settings → Network &
  internet → Private DNS → Off). Android's DNS-over-TLS hijacks name
  resolution and breaks the MagicDNS hostname lookup, so the page will
  hang and fail with "cannot be reached" if Private DNS is on. With it
  off, the hostname resolves through the tailnet and the page loads.
  (If you ever re-enable it, `https://100.104.50.68` — the PC's tailnet
  IP — still works as a fallback.)

The dashboard supports a runtime-only data-store switch. The launcher
sets `CC_HOME` to the contained `command-center-prod` directory by default;
the page header shows `STORE: PROD` or `STORE: DEV` and the button switches
between the contained `command-center-prod` and `command-center-dev` stores. The selection is
held only in the running process and resets to PROD after a restart. All
writes continue through `cc_store`; arbitrary filesystem paths are never
accepted.

## Using the store from the command line

```
python lib/cc_store.py state                      # computed overview
python lib/cc_store.py tasks list [--status S]
python lib/cc_store.py tasks add --title "..." [--due YYYY-MM-DD]
                                      [--priority high|normal|low] [--notes "..."]
python lib/cc_store.py tasks update --id ID [--status S] [--priority P] ...
python lib/cc_store.py tasks complete --id ID
python lib/cc_store.py goals list|add|update
python lib/cc_store.py commitments list|add|complete
python lib/cc_store.py activity --n 20            # recent changes
python lib/cc_store.py backups list               # snapshot status
python lib/cc_store.py backups prune [--keep N]   # prune old snapshots
python lib/cc_store.py restore --from backups/<dir> --yes   # DESTRUCTIVE
```

New tasks default to status `inbox` and priority `normal`.

## Tests

All tests run against isolated temporary data, so they never touch your
real records.

```
python tests/test_cc.py                  # store: validation, concurrency, durability
python tests/test_backups.py             # backups: retention, restore, races
python tests/test_dashboard.py           # dashboard API: endpoints, Host/Origin, errors
python tests/test_dashboard_frontend.py  # dashboard page wiring
python tests/test_dashboard_store_switch.py  # isolated PROD/DEV switching
python tests/test_organisation.py          # hierarchy and parent validation
```

## Security model (read this)

- The dashboard has no application login. It is safe because it binds to
  `127.0.0.1` only and is reachable remotely solely through Tailscale
  Serve, which is tailnet-only.
- Remote requests arriving through Serve must carry the intended Tailscale
  identity: the dashboard checks `X-Forwarded-Proto`/`X-Forwarded-Host`
  for Serve provenance and requires `Tailscale-User-Login` to match the
  expected account, else 403. This was verified empirically against the
  installed Tailscale: Serve strips client-supplied identity/forwarded
  headers, so a remote caller cannot forge a different identity through
  Serve.
- Access is additionally restricted at the network layer by the Tailscale
  policy (tailnet ACL limits which device may reach the PC on tcp:443),
  independent of the application check.
- Consequence: this protects against remote/network attackers and other
  tailnet devices, but it does NOT protect against a malicious process
  already running on the PC — such a process can call `127.0.0.1:8787`
  directly and forge any header, including the Serve/identity ones. This
  is a documented, accepted trade-off for a single-user personal tool.
- The dashboard validates the `Host` and `Origin` headers (loopback plus
  the PC's exact MagicDNS hostname) to block cross-origin browser abuse,
  and returns generic errors so internal paths are never leaked.
- Writes are narrow: create/complete only. There is no delete, restore, or
  prune exposed over HTTP — destructive operations are CLI-only and should
  be run deliberately.

## Files

```
lib/cc_store.py            shared write layer + CLI
lib/cc_dashboard.py        localhost web dashboard
tests/                     test suites
data/                      your records (not in git)
backups/                   snapshots (not in git)
NEXT_STEPS.md              plan status and roadmap
```

## Roadmap (deferred)

Scheduled briefings, Obsidian integration, and a possible SQLite backend
are considered but not built. See `NEXT_STEPS.md`.
