# Command Centre

A small personal command centre for task and life administration. A
shared file-based store, a localhost web dashboard, and (optionally)
secure phone access over a private Tailscale network.

Everything is plain Python standard library — no external packages, no
database server, no framework. Data is stored as portable, human-readable
JSONL files you can read and edit with any text editor.

## Architecture

```
 The optional remote endpoint is deployment-specific and must be supplied through
configuration; no personal hostname or address is part of the source tree.
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
  handful of explicit operations (no shell, no arbitrary file access). Its
  storage dependency is fixed when the process starts; it does not expose a
  runtime PROD/DEV switch or accept storage paths over HTTP.
- `tests/test_cc.py`, `test_backups.py`, `test_dashboard.py`,
  `test_dashboard_frontend.py`, and `test_dashboard_store_switch.py` cover
  the store, dashboard, frontend wiring, fixed-store isolation, and health
  endpoints.

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

Remote access is deployment-specific. Configure an exact trusted hostname with
`CC_ALLOWED_HOSTS` and, when using Tailscale Serve identity verification,
configure `CC_EXPECTED_TS_LOGIN` and `CC_REQUIRE_TS_IDENTITY=true`.

Do not place real hostnames, identities, addresses or credentials in the
repository. The dashboard remains loopback-only unless a separately approved
private transport is configured.

The dashboard uses the explicitly configured `CC_DATA_DIR` and
`CC_BACKUP_DIR` locations for the process lifetime. Development and CI should
supply temporary or synthetic external directories; production must supply both
locations explicitly. The HTTP API has no store-switch endpoint and never
accepts an arbitrary storage path.

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

The approved UI specification requires new tasks to default to status `active` and priority `normal`. Existing compatibility records may still contain legacy fields.

## Tests

All tests run against isolated temporary data, so they never touch your
real records. The consolidated test plan and specification matrix are in
`docs/test-plan.md`.

Run the complete suite with:

```
python tests/run_all.py
```

The existing individual suites can also be run directly:

```
python tests/test_cc.py                  # store: validation, concurrency, durability
python tests/test_backups.py             # backups: retention, restore, races
python tests/test_dashboard.py           # dashboard API: endpoints, Host/Origin, errors
python tests/test_dashboard_frontend.py  # dashboard page wiring
python tests/test_dashboard_store_switch.py  # isolated PROD/DEV switching
python tests/test_organisation.py          # hierarchy and parent validation
python tests/test_organisation_ui.py       # organisation API and tree UI
python tests/test_organisation_edit.py    # create/update organisation records
```

## Security model (read this)

- The dashboard has no application login. It is safe because it binds to
  `127.0.0.1` only and is reachable remotely solely through Tailscale
  Serve, which is tailnet-only.
- Remote requests arriving through a separately configured private transport must
  carry the intended identity. The exact trusted hostname and expected identity
  are supplied through configuration, never embedded in source.
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
```

## Container workflow

The application image contains only the standard-library runtime and source
under `lib/`. It runs as UID/GID `10001`, is read-only except for explicitly
mounted data and backup directories, and listens on `0.0.0.0` inside the
container. Compose publishes it only on the host loopback interface.

The development Compose file uses synthetic named volumes:

```bash
docker compose up --build
curl http://127.0.0.1:8787/health/ready
docker compose logs -f command-centre
docker compose down
```

The production example is `compose.production.example.yaml`. It requires
pre-created external data and backup directories, exact reverse-proxy host and
identity settings, and an immutable image reference such as
`ghcr.io/ruairi-grant/command-centre@sha256:<digest>`. Missing production
mounts fail closed; the image never creates a production store inside its
filesystem.

Pull requests build and test AMD64 and ARM64 images on native GitHub-hosted
runners, validate both Compose files, and scan images for fixable HIGH and
CRITICAL vulnerabilities. Merges to `master` publish a multi-platform image to
GHCR tagged with the source commit, with BuildKit SBOM and provenance
attestations. The deployment artifact is always selected by its immutable
digest, not by a mutable tag. The ROCK 5B does not build images and is not
modified by this workflow.

The container workflow is `.github/workflows/container.yml`. Container tests
are opt-in locally:

```bash
CC_RUN_CONTAINER_TESTS=1 pytest tests/test_container.py -q
```

Normal WSL development continues to use `uv`, direct Python execution, the
legacy runner, pytest, Ruff and mypy; Docker Compose is an integration-test
environment rather than a prerequisite for every edit.
