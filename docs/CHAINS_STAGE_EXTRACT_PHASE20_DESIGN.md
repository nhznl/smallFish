# Chains stage extract (Phase 20) design

**Status:** 20a complete  
**Date:** 2026-07-30  
**Parent:** [`CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md`](CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md) (Phase 6c first extract)

## Goal

Extract the remaining pipeline stages from [`chains.py`](../utilities/options/chains.py):
underlying pool / actual-expiry eligibility, strike selection, and per-symbol
quote enrichment — preserving artifact contracts, methodology, and CLI entry
`python -m utilities.options.chains`.

Phase 6c already split config/scope (`chains_config.py`) and publish
(`chains_publish.py`). This phase completes the stage decomposition planned in
the parent design.

## Target module map (20a)

| Module | Owns |
|---|---|
| `chains_quote.py` | Numeric guards, quote quality, contract identity, yield/spread helpers, shared quote/contract constants |
| `chains_eligibility.py` | `build_underlying_pool`, `rv_window_for_actual_dte`, `derive_actual_expiry_context`, skip/pair reason codes |
| `chains_strikes.py` | `nearest_expiry`, `option_moneyness`, `select_entry_strikes`, `select_roll_exit_strikes`, moneyness constants |
| `chains_enrich.py` | `apply_quote_observation`, `process_symbol_chains`, `enrich_tastytrade_quotes`, entry/role constants |
| `chains.py` | `PREMIUM_COLUMNS`, `run_chains`, `main`, yfinance bridge; re-exports for existing importers |

`chains_enrich.enrich_tastytrade_quotes` lazily imports `PREMIUM_COLUMNS` from
`chains.py` to avoid a module-load cycle while keeping one column-order owner.

## Frozen contracts

- Premium archive layout (`PREMIUM_SCHEMA_VERSION` 3, immutable runs, daily/views, `latest.json`)
- `PREMIUM_COLUMNS` order and CSV field names
- Strike/eligibility/quote-quality methodology — **move code only; no retuning**
- Injected fetchers; offline / network-blocked tests stay green
- CLI module path and FastAPI subprocess argv unchanged

## Verification

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
git diff --check
python3 tools/scan_secrets.py
python3 tools/check_docs.py
```

Especially: `test_chains.py`, `test_verify_premiums.py`.

## Explicit non-goals

- Methodology or schema changes
- Merging Python runtimes
- Angular or FastAPI router changes
- Beta/Greek trim
