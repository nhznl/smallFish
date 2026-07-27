# Setup

Everything here works without any API key or brokerage account.

## Prerequisites

| Tool | Minimum | Check |
|---|---|---|
| Python | 3.12 | `python3 -V` |
| Node.js | 20 (LTS) | `node -v` |
| npm | 10 | `npm -v` |
| Git | 2.30 | `git --version` |

macOS and Linux are supported; Windows through WSL 2. Full detail in
[SUPPORT_MATRIX.md](SUPPORT_MATRIX.md).

If you use a version manager, `.nvmrc` pins Node:

```bash
nvm use
```

## Install

```bash
git clone https://github.com/nhznl/smallFish.git
cd smallFish
./setup.sh
```

`setup.sh` is the only setup entry point. It:

1. checks every runtime against the support matrix, reporting all failures at
   once rather than stopping at the first;
2. creates `app.env` from `app.env.example`, pointing `SFP_DATA_DIR` and
   `SFP_LOG_DIR` at this checkout, at mode 0600;
3. creates `data/` and `logs/`;
4. creates or repairs both Python virtual environments, installs the pinned
   requirements, and runs `pip check`;
5. installs UI dependencies with `npm ci` against the committed lockfile;
6. prints a `doctor` summary and the exact next commands.

It is **non-interactive** — safe for CI and agents — and **idempotent**. Running
it again repairs anything missing and never overwrites `app.env`, deletes data,
or rebuilds a healthy environment.

A global Angular CLI is **not** required. The CLI is a devDependency reached
through npm scripts.

### Flags

| Flag | Effect |
|---|---|
| `--check` | Report prerequisites and current state, change nothing |
| `--skip-ui` | Skip the Node check and `npm ci` |
| `--skip-python` | Skip both Python environments |
| `--help` | Usage |

`PYTHON=python3.12 ./setup.sh` chooses the interpreter for the virtual
environments.

## Verify

```bash
./commands.sh doctor
```

Reports runtimes, installation, configuration, data, and optional integrations.
It is local-only — no network call, no brokerage call — and masks every secret,
so its output is safe to paste into an issue.

Read the markers as:

| Marker | Meaning |
|---|---|
| `[  ok]` | Working |
| `[warn]` | Works, but something is missing or stale |
| `[ off]` | An optional integration you have not configured. **Not a problem.** |
| `[FAIL]` | Required. smallFish will not work until it is fixed. |

## Populate data

```bash
./commands.sh bootstrap-data
```

2–5 minutes. See [DATA.md](DATA.md) for exactly what it downloads and its
partial-failure policy.

Useful flags: `--dry-run` to print the plan, `--symbols NVDA AMD` to fetch
specific symbols, `--year 2025` to fetch one year.

## Run

Single server — one process, the mode most people want:

```bash
./commands.sh build-ui
./commands.sh server --no-reload
```

<http://127.0.0.1:8000>.

Frontend development — two terminals, hot reload:

```bash
./commands.sh server
```

```bash
cd stock-app-ui && npm start
```

<http://localhost:4200>. The dev server calls the API on port 8000 directly, so
both must be running.

## Stopping and restarting

`Ctrl-C` in the server terminal. There is no daemon and no background process.

If the port is busy, something else is already bound — often a server you
forgot:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Change the port in `app.env` (`APP_PORT`) if you want to run two copies. The UI
derives the API origin from the page it was served from, so a rebuilt UI follows
the port automatically.

Nothing needs to be rerun after a restart. To pick up new prices:

```bash
./commands.sh scrape
```

## Optional integrations

None are required.

```bash
./setup-brokerages.sh status
```

See [BROKERAGES.md](BROKERAGES.md) and [CONFIGURATION.md](CONFIGURATION.md).

## Uninstall

smallFish writes nothing outside its own directory. To remove generated
artifacts but keep the source:

```bash
rm -rf utilities/.venv stock-app/.venv stock-app-ui/node_modules
rm -rf stock-app-ui/dist stock-app/static
rm -rf data logs                 # deletes your downloaded price history
rm app.env                       # deletes your credentials
```

`./setup.sh` rebuilds everything except `data/` and `app.env`; recreate those
with `./commands.sh bootstrap-data` and `./setup-brokerages.sh setup`.

To remove smallFish entirely, delete the directory. Then revoke any brokerage
access you granted — deleting the checkout does not do that. See
[BROKERAGES.md](BROKERAGES.md#revoking-access).
