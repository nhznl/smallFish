# Options utilities

This package owns the Wheel’s local historical analytics, exact-contract quote
collection, immutable quote archives, and archive verification.

| Module | Responsibility |
|---|---|
| `wheel.py` | Local OHLCV Wheel scan and immutable Wheel artifacts. |
| `chains.py` | Exact-contract discovery, quote enrichment, and immutable premium artifacts. |
| `tastytrade_quotes.py` | Tastytrade DXLink quote retrieval for exact provider symbols. |
| `verify_premiums.py` | Offline verification of immutable premium archives and their derived views. |
| `exchange_calendar.py` | Deterministic NYSE session calendar used for Wheel horizons. |

## What needs a credential

| Command | Needs Tastytrade? | Produces |
|---|---|---|
| `./commands.sh wheel` | **No** | The Wheel candidate screen, from the local price cache alone |
| `./commands.sh verify-premiums [run-id]` | **No** | Offline integrity check of an existing archive |
| `./commands.sh chains` | **Yes** | Exact-contract discovery plus timestamped DXLink quotes |

The Wheel screen is the credential-free half and is what a new user sees.
Quote collection is the credentialed half: `chains` writes every attempt
immutably, reports complete/partial/unavailable provider coverage, and exits
non-zero when no requested Tastytrade quote arrives. Yahoo quotes are
diagnostic-only — they cannot authorize entry economics. Running off-hours is
allowed for diagnostics, but off-hours or untimestamped observations can never
become entry-eligible.

See [`../../docs/BROKERAGES.md`](../../docs/BROKERAGES.md) for setup.

## Verification

```bash
./commands.sh verify-premiums            # the latest run
./commands.sh verify-premiums <run-id>   # a specific archive
```

Recomputes the derived dated/ENTRY/ROLL_EXIT views from the immutable archive
and fails on any mismatch. Offline, and safe to run at any time.

## Running

Run these through the stable repository commands: `./commands.sh wheel`,
`./commands.sh chains`, and `./commands.sh verify-premiums [run-id]`.
Configuration is colocated in `config/`. Shared services such as price readers,
artifact manifests, and the universe registry remain in the parent
`utilities/` package.

## Outputs

| Path | Contents |
|---|---|
| `$SFP_DATA_DIR/wheel/` | Wheel screen results, plus a per-run archive with a manifest and artifact hashes |
| `$SFP_DATA_DIR/wheel_exclusions/` | Symbols excluded from the screen, with reasons |
| `$SFP_DATA_DIR/premiums/` | Immutable timestamped quote archives and their derived views |

All git-ignored and regenerable. Formats are documented in
[`../../docs/DATA.md`](../../docs/DATA.md).

## Tests

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests/test_wheel.py \
    utilities/tests/test_chains.py utilities/tests/test_verify_premiums.py \
    utilities/tests/test_tastytrade_quotes.py
```

No test contacts Tastytrade or Yahoo. Quote providers are injected; pass a fake.
