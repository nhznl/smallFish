# Chains pipeline decomposition (Phase 6) design

**Status:** 6a–6c complete (further stage splits deferred)  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) item 6

## Goal

Begin splitting [`utilities/options/chains.py`](../utilities/options/chains.py) (~2134 lines) into pipeline stages behind frozen premium archive contracts. This phase extracts **config/scope + artifact publish only**; strike selection, eligibility, and `process_symbol` stay in `chains.py`.

## Inventory (current monolith)

| Stage | Symbols today |
|---|---|
| Config / defaults | `DEFAULT_*`, `CONFIG_PATH`, `chains_config`, `load_config` |
| Collection scope | `normalize_collection_scope` |
| Pool / eligibility / strikes | `build_underlying_pool`, expiry pair logic, `select_*_strikes`, `process_symbol_chains`, … |
| Quote enrich | `enrich_tastytrade_quotes`, `apply_quote_observation`, … |
| Orchestration | `run_chains`, `ChainsResult`, `main` |
| Publish | `write_chain_artifacts`, `_runtime_metadata`, verify + `latest.json` promotion |

CLI entry remains `python -m utilities.options.chains`. FastAPI only subprocesses via [`run_jobs`](../stock-app/app/routers/run_jobs.py).

## Target module map (6c first extract)

| Module under `utilities/options/` | Owns |
|---|---|
| `chains_config.py` | Config defaults used by normalization, `CONFIG_PATH`, `chains_config`, `normalize_collection_scope`, `load_config`, `_strategy_data_root` |
| `chains_publish.py` | `ChainsResult`, `write_chain_artifacts`, `_runtime_metadata` (verify + immutable run + daily views + `latest.json`) |

[`chains.py`](../utilities/options/chains.py) keeps `run_chains`, `main`, pool/eligibility/strikes/enrichment; imports the extracted modules and **re-exports** `chains_config`, `normalize_collection_scope`, `write_chain_artifacts`, `ChainsResult`, etc. so existing tests and callers of `utilities.options.chains` do not churn.

Later slices (not this phase): extract pool/eligibility/strikes and quote-enrich stages.

## Frozen contracts

- Premium archive layout under `data/premiums/` (`PREMIUM_SCHEMA_VERSION` 3, immutable `runs/{run_id}/`, compatibility daily/views, `latest.json`)
- CSV column set (`PREMIUM_COLUMNS`) and meta field names
- Strike/eligibility/quote-quality methodology: **move code only; do not retune**
- Injected fetchers; offline / network-blocked tests stay green
- No `stock-app` → `utilities` import; job argv surface unchanged unless a bug forces it

## Owner decisions (baked in)

- First extract = config/scope + publish only.
- Shared `_strategy_data_root` with wheel: out of scope for a joint refactor; chains may keep its local helper in `chains_config` for this extract.
- Do not change CLI module path or FastAPI subprocess wiring.

## Import / call-site impact

- Tests that import from `utilities.options.chains` keep working via re-exports.
- Direct imports of new modules are allowed but not required in 6c.
- Architecture enforcement that parses `chains.py` for Yahoo vs neutral market-data boundaries must still hold (discovery stays in `chains.py`; quote transport stays via `market_quotes` / `services.options_market`).

## Verification gates

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
# If argv / job wiring changes (not expected):
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests/test_run_jobs.py
git diff --check
python3 tools/scan_secrets.py
```

Especially: `test_chains.py`, `test_verify_premiums.py`.

## Landed (6c)

- [`chains_config.py`](../utilities/options/chains_config.py): defaults, `chains_config`, `normalize_collection_scope`, `load_config`, `_strategy_data_root`
- [`chains_publish.py`](../utilities/options/chains_publish.py): `ChainsResult`, `write_chain_artifacts`, `runtime_metadata`
- [`chains.py`](../utilities/options/chains.py) re-exports for existing importers; CLI entry unchanged

## Explicit non-goals

- Full stage split (strikes/eligibility/enrichment) in one change
- Methodology, schema, or archive layout changes
- New router/CLI surfaces
- Merging the two Python environments
- Angular view-model decomposition
- Removing GET job routes or beta/Greek fields (Phase 7)
