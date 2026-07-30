# Options activity decomposition (Phase 6) design

**Status:** 6a–6b complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) item 6

## Goal

Split [`stock-app/app/options_activity.py`](../stock-app/app/options_activity.py) (~861 lines) into focused modules under `brokerages/` while freezing CSV paths, headers, sync/manual JSON shapes (including retired `groups_*` counters), and provider boundaries. Call sites keep importing the thin facade.

## Inventory (current monolith)

| Concern | Symbols today |
|---|---|
| Schema constants + CSV headers | `SCHEMA_*`, `SOURCE`, `MANUAL_*`, `*_HEADERS` |
| Store | `_lock`, `_read_csv`, `_atomic_write` |
| Normalization | `_normalize_event/mark/combined_position/greek/beta`, `_select_transactions`, small parsers |
| Provider sync | `fetch_tastytrade`, `_fetch_option_greeks`, `_fetch_underlying_betas`, `sync`, `_trend_observations` |
| Manual CRUD | `create_manual_event`, `update_manual_event`, `delete_manual_event` |
| Repair helpers | `import_broker_events`, `remove_symbols` |

**Call sites (facade stays):** routers (`options.py`), `brokerages/registry.py`, adapters, projections/store/migration/trend, `held_option_market_data` (retain-prior error helper), tests.

## Target module map (6b)

| Module under `stock-app/app/brokerages/` | Owns |
|---|---|
| `activity_store.py` | Headers, lock, read/atomic-write, `ActivityValidationError`, schema constants |
| `activity_normalize.py` | Event/mark/position/Greek/beta normalization + transaction selection + shared parsers |
| `activity_sync.py` | `fetch_tastytrade`, Greek/beta fetch, `sync()`, trend observation handoff |
| `activity_manual.py` | Manual create/update/delete |
| `activity_repair.py` | `import_broker_events`, `remove_symbols` |

Facade [`options_activity.py`](../stock-app/app/options_activity.py): re-export public names and the private helpers already imported by adapters/tests (`_lock`, `_read_csv`, `_atomic_write`, `_contract_key`, `_option_terms`, `_safe_market_data_error`, …) so registry/routers/adapters do not churn.

## Frozen contracts

- CSV filenames under `SFP_*` / `config` path helpers; header lists; `schema_version`
- Sync and manual response keys, including zeroed retired `groups_*` counters and `group_id: null`
- Account transport via `services.tastytrade`; market data via `services.options_market` (no `utilities` import)
- Retain-prior Greek/beta policy on miss (mirror pattern in `held_option_market_data.py`; do not merge TT and Fidelity importers)
- Injected `provider=` for offline tests; network-blocked suites stay green

## Owner decisions (baked in)

- Keep `import_broker_events` / `remove_symbols` as tests-backed recovery APIs; **no** public router or CLI in this phase.
- Thin re-export facade first; migrate private imports inward only as needed later.
- Shared `_strategy_data_root` with wheel is out of scope for activity work.

## Import / call-site impact

- Production importers of `app.options_activity` unchanged.
- Architecture enforcement that AST-parses `options_activity.py` for account vs market-data imports must target `activity_sync.py` (where those imports live after extraction).
- Facade retains `from . import config` so `options_activity.config` keeps working in tests.

## Verification gates

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
git diff --check
python3 tools/scan_secrets.py
```

Especially: `test_options_activity.py`, brokerage adapter/API/architecture enforcement.

## Landed (6b)

- `brokerages/activity_{store,normalize,sync,manual,repair}.py`
- Thin [`options_activity.py`](../stock-app/app/options_activity.py) re-export facade
- Architecture enforcement targets `activity_sync.py`; adapter ownership scan excludes `activity_*`

## Explicit non-goals

- CSV/API/artifact/methodology changes
- Exposing repair helpers as HTTP/CLI
- Merging TT and Fidelity market-data importers
- Angular view-model work
- Removing beta/Greek fields (Phase 7)
