# Command Centre WSL development

The supported development checkout is the native WSL filesystem:
`~/projects/command-center`.

## First setup

```bash
cd ~/projects/command-center
uv sync --group dev
```

The production application remains standard-library-only. `uv` manages the
isolated development environment and locks the test/lint tools in `uv.lock`.

## Checks

```bash
uv run python tests/run_all.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The script runner remains the compatibility command for the existing suites.
`pytest` is an additional collection/reporting interface, not a replacement
for the established tests.

## Local dashboard

Use a data directory outside the repository and synthetic records only:

```bash
export CC_ENV=development
export CC_DATA_DIR="$HOME/.local/share/command-centre/dev/data"
export CC_BACKUP_DIR="$HOME/.local/share/command-centre/dev/backups"
export CC_BIND_HOST=127.0.0.1
export CC_PORT=8787
uv run python lib/cc_dashboard.py
```

Stop it with Ctrl-C. The dashboard is loopback-only by default. Do not set
production Tailscale identity values in this checkout.

## VS Code

From Ubuntu, run:

```bash
cd ~/projects/command-center
code .
```

Choose the `uv` environment when prompted. The integrated terminal must report
Ubuntu's Python, not the Windows interpreter.

## Hermes

Run Hermes from the same WSL shell and start it in this directory:

```bash
cd ~/projects/command-center
hermes
```

Hermes configuration and credentials remain in the WSL Hermes home and are
independent of the Windows installation. Do not copy the Windows `.env`, auth
files or operational data.
