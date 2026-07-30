# Troubleshooting

Start here:

```bash
./commands.sh doctor
```

It reports runtimes, installation, configuration, data, and integrations. It
makes no network call and masks every secret, so its output is safe to paste
into an issue.

`[ off]` is not a failure — it marks an optional integration you have not
configured.

## Setup

### `setup.sh` reports an unsupported runtime

It lists every failure at once. Install the versions in
[SUPPORT_MATRIX.md](SUPPORT_MATRIX.md) — `pyenv` or `asdf` for Python, `nvm` for
Node (`nvm use` reads the committed `.nvmrc`) — then rerun.

### `setup.sh` fails creating a virtual environment

On Debian and Ubuntu, `python3-venv` is a separate package:

```bash
sudo apt install python3-venv
```

Otherwise delete the environment and rerun; setup rebuilds it:

```bash
rm -rf utilities/.venv stock-app/.venv && ./setup.sh
```

### `npm ci` fails

`npm ci` requires `package-lock.json` and `package.json` to agree. If you edited
`package.json`, run `npm install` once to resync the lockfile and commit both.

To rebuild from scratch:

```bash
rm -rf stock-app-ui/node_modules && ./setup.sh
```

### `pip check` reports inconsistent dependencies

Usually a half-installed environment. Delete it and rerun `./setup.sh`.

### `commands.sh` reports a missing `app.env`

Run `./setup.sh`. It creates one from the template without overwriting an
existing file.

## Data

### Every view is empty after setup

Expected — you have not downloaded anything yet:

```bash
./commands.sh bootstrap-data
```

The UI now says this explicitly rather than showing a blank table.

### `bootstrap-data` reports failed symbols

A symbol can legitimately return nothing: delisted, renamed, or listed after the
window began. Those are reported and tolerated. The command fails only if SPY is
missing or more than 20% of symbols produced nothing — which usually means a
network or provider problem. Rerun; completed symbols are not disturbed.

### `bootstrap-data` is slow or rate-limited

Yahoo Finance throttles. Raise the delay rather than the thread count:

```bash
./commands.sh bootstrap-data --delay 1.0 --threads 3
```

### Sectors says no snapshot yet

The snapshot is derived, not downloaded:

```bash
./commands.sh sector-rotation
```

### Prices look stale

`prices_stale` is set relative to the last expected trading session. Update:

```bash
./commands.sh scrape
```

Nothing on a weekend or a market holiday is normal. If a symbol has been silent
for more than `stale_after_days` (default 10) it is retired as delisted and
recorded in `data/retired_symbols.csv`.

### `Cache root not found`

`SFP_DATA_DIR` points somewhere that does not exist. Check `app.env`, or run
`./setup.sh` to recreate the directories.

## API

### The port is already in use

Something is already bound — often a server you forgot:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Stop it, or set a different `APP_PORT` in `app.env`. The built UI derives its
API origin from the page it was served from, so it follows the port
automatically.

### `/api/studies` returns 503

The catalog is unavailable *and* invalid. An absent artifact falls back to the
bundled copy, so a 503 means the JSON in your data root is malformed or
inconsistent with its records. Validate and rebuild:

```bash
./commands.sh studies validate
./commands.sh studies build
```

If you have no pinned evidence, delete the broken files from
`$SFP_DATA_DIR/studies/` and the bundled artifacts will be used.

### A route shows raw JSON instead of the dashboard

`/portfolios` is both an Angular route and an API path. In single-server mode,
browser navigations to that path are served the SPA (`index.html`) via
`SPA_ROUTE_COLLISIONS` in `stock-app/app/main.py`, while JSON clients still
reach the API. `/options` used to collide the same way; the JSON collection
there is retired, so the SPA catch-all serves that path unconditionally.

If you still see raw JSON on `/portfolios`, rebuild the UI and restart:

```bash
./commands.sh build-ui && ./commands.sh server --no-reload
```

Confirm the process listening on the port is the one you just started
(`lsof -nP -iTCP:8000 -sTCP:LISTEN`).
### `Built UI unavailable`

You are running single-server without a build:

```bash
./commands.sh build-ui
```

Not needed if you use `npm start` on port 4200.

## UI

### The dev server shows no data

Both processes must run: `./commands.sh server` **and** `npm start`. The dev
server on 4200 calls the API on 8000 directly, so `CORS_ORIGINS` in `app.env`
must include `http://localhost:4200`.

### The browser shows a stale version after a rebuild

Hard-reload to bypass the cache: Cmd/Ctrl-Shift-R.

### The Wheel tab is empty

The wheel screen is a batch job:

```bash
./commands.sh wheel
```

Option quotes additionally need Tastytrade.

## Integrations

### Everything shows "not set up"

That is the supported default. Every brokerage feature is optional. Configure
one only if you want it:

```bash
./setup-brokerages.sh status
```

### A provider says partly configured

Both settings in a pair are required — `TT_CLIENT_SECRET` *and*
`TT_REFRESH_TOKEN`; `SNAPTRADE_CLIENT_ID` *and* `SNAPTRADE_CONSUMER_KEY`. Rerun
`./setup-brokerages.sh setup <provider>`.

### `verify` reports an authentication error

Usually an expired or revoked refresh token, or credentials from the other
environment (`sandbox` vs `live`). Rerun `setup` to replace them. Only the error
type is shown, because provider messages can embed tokens; the detail is in the
server logs.

### SnapTrade verifies but shows no holdings

The credentials work; no brokerage is linked. Personal (`PERS-`) keys link on
the SnapTrade dashboard; commercial keys use the connection portal. See
[BROKERAGES.md](BROKERAGES.md).

### `retirement-risk` capability is incomplete with only SnapTrade

Correct and deliberate. SnapTrade holdings work without Tastytrade, but
exact-contract Greeks and market-metric beta for retirement options need
Tastytrade as well. Connect Tastytrade to enrich those market-data inputs;
the capability id remains `retirement-risk` for compatibility.

## Tests

### `test_study_catalog.py` skips a test

Expected on a clean clone. The byte-for-byte reproduction needs pinned evidence
under the git-ignored `data/`, which only exists where the studies were run. The
published-artifact tests still cover what the API serves.

### Angular tests fail to launch a browser

Karma needs Chrome or Chromium. Set `CHROME_BIN` if it is not discovered:

```bash
export CHROME_BIN=$(which chromium)
```

### A test fails only on your machine

Check you are using the right interpreter — `stock-app/.venv` for
`stock-app/tests`, `utilities/.venv` for `utilities/tests`. Then confirm no
stale server or leftover `data/` state is involved.

## Reporting a problem

Include your OS, Python and Node versions, the exact command, and the full
`./commands.sh doctor` output. Redact anything else.

Never attach `app.env`, anything from `data/ledger_trading/` or
`data/ledger_retirement/`, or a screenshot showing real positions. For a
security issue, do not open a public issue at all — see
[../SECURITY.md](../SECURITY.md).
