# Options-risk subsystem retirement design

**Status:** approved and implemented (2026-07-29)  
**Date:** 2026-07-29  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 3  

## Implementation record

Owner approved deletion with:

1. Delete the dead subsystem after moving coverage.
2. New home: `stock-app/app/brokerages/call_coverage.py`.
3. Capability option **(A):** reword `retirement-risk` to market-data enrichment;
   keep the id.
4. No known out-of-tree importers.

Done in-tree:

- `apply_call_coverage` (+ `_coverage_number`) live under `brokerages/call_coverage.py`.
- Deleted `app/options_market.py`, `app/options_risk.py`, `config/options_risk.yaml`,
  and the dashboard-only test modules.
- Coverage tests live in `stock-app/tests/test_call_coverage.py`.
- `retirement-risk` capability label/reason describe enrichment readiness.
- Docs no longer list `options_risk.yaml` or the legacy modules as live architecture.

The sections below are the pre-implementation audit and plan, retained for
history.

---

## 1. Goal

Retire the dead portfolio-risk dashboard code path while preserving the one
live behaviour that still lives inside it: short-call share coverage
classification used when materializing held-option market data.

Do **not** touch `services.options_market` (the live provider-neutral quotes /
Greeks / beta transport). Names collide; ownership does not.

## 2. Inventory

| Artifact | Role today | Disposition after approval |
|---|---|---|
| `stock-app/app/options_market.py` (~339 lines) | Legacy risk-dashboard market-input wiring (`build_market_inputs`, premium/Greek loaders, RV fallback, computed beta). **No production caller.** | Delete |
| `stock-app/app/options_risk.py` (~604 lines) | Black-Scholes / beta / band / `build_risk_snapshot` **plus** live `apply_call_coverage` (+ `_coverage_number`) | Keep coverage only; delete the rest |
| `stock-app/config/options_risk.yaml` | Cash limits, delta bands, vol/beta staleness for the retired dashboard | Delete with the subsystem |
| `stock-app/tests/test_options_market.py` | Tests only `app.options_market` | Delete with the module |
| `stock-app/tests/test_options_risk.py` | Dashboard math **and** call-coverage cases | Split: keep coverage tests; delete dashboard tests |
| `GET /capabilities` → `retirement-risk` | Still describes “risk figures” / fallbacks for a UI that no longer exists | Reword or remove (decision §7) |

### Live production consumer of coverage

```
brokerages/importers/held_option_market_data.py
  _option_rows()
    → apply_call_coverage(rows, _share_pool(ledger))
    → used by sync_betas / sync_greeks selection
```

Coverage mutates in-memory rows (`trade_type`, `coverage`, `covered_contracts`).
Those fields are asserted by importer tests. They are **not** written into the
beta/Greek CSV schemas and are **not** part of the public brokerage JSON
envelope’s `coverage` block (that block means retained-history completeness).

No FastAPI router imports `app.options_market` or `app.options_risk`. No Angular
code references the retired risk dashboard.

### Name collision (keep)

| Module | Keep? |
|---|---|
| `services/options_market/` | **Yes** — production quotes/Greeks/beta |
| `services/tests/test_options_market.py` | **Yes** |
| `utilities/options/market_quotes.py` | **Yes** — delegates to `services.options_market` |

## 3. External / artifact compatibility audit

| Surface | Finding |
|---|---|
| Public HTTP | No route serves `build_risk_snapshot` or `build_market_inputs`. Brokerage routes use common projections only. |
| CSV artifacts | Greek/beta CSVs are written by `held_option_market_data` / `options_activity` via `services.options_market`. Headers do not depend on `options_risk.yaml`. |
| Angular | No imports of retired risk types. Portfolio-risk CSS leftovers in `styles.scss` are unused chrome (cleanup optional, not blocking). |
| In-repo callers of `app.options_market` | Tests only (`test_options_market.py`; one private helper import in `test_importer_held_option_market_data.py` for `_load_tasty_greeks`). |
| In-repo callers of `app.options_risk` | Production: `held_option_market_data` → `apply_call_coverage`. Tests: `test_options_risk.py`, `test_options_market.py`. |
| Config | `strategy_config_yaml()` already removed in Phase 2. Nothing else resolves `options_risk.yaml`. |
| Unknown externals | Scripts outside this repository that imported `app.options_risk` / `app.options_market` or read `options_risk.yaml` would break. No such callers were found in-tree. |

**Conclusion:** In-repository evidence supports deletion of the dashboard
subsystem after moving coverage. External breakage is possible only for
out-of-tree imports of those modules or the YAML file.

## 4. Proposed implementation (after approval)

### Step A — Characterize and move coverage

1. Extract `apply_call_coverage`, `_coverage_number`, and the few string
   constants they need (`SHORT_CALL`, `COVERED_CALL`, `OPEN`, `COVERED`,
   `PARTIALLY_COVERED`, `UNCOVERED`, `CONTRACT_MULTIPLIER`) into a focused
   owner module, preferred location:

   `stock-app/app/brokerages/call_coverage.py`

   Rationale: the only production caller is already under `brokerages/importers/`;
   coverage is brokerage accounting, not market transport and not dashboard risk.

2. Keep the function signature and in-place mutation semantics **byte-for-byte**
   identical (same pool keying, floor-to-contracts rule, strike/expiry sort).

3. Move the existing call-coverage tests from `test_options_risk.py` into
   `stock-app/tests/test_call_coverage.py` (or an importer test module) without
   changing assertions.

4. Point `held_option_market_data` at the new module. Confirm
   `test_importer_held_option_market_data.py` still passes unchanged.

### Step B — Delete the dead subsystem

1. Delete `stock-app/app/options_market.py`.
2. Delete the remainder of `stock-app/app/options_risk.py` (or the whole file
   once coverage has moved).
3. Delete `stock-app/config/options_risk.yaml`.
4. Delete `stock-app/tests/test_options_market.py`.
5. Delete dashboard-only cases from `test_options_risk.py` (or delete the file
   after coverage tests have moved).
6. Replace the `test_importer_held_option_market_data` use of
   `app.options_market._load_tasty_greeks` with a local fixture reader or an
   importer-owned helper — do not keep the legacy module for one private test
   import.

### Step C — Capability and docs

1. Apply the §7 decision for `retirement-risk` on `GET /capabilities`.
2. Update `stock-app/README.md`, `docs/CONFIGURATION.md`, and any remaining
   references so `options_risk.yaml` / risk-dashboard modules are gone, not
   merely labelled legacy.
3. Optional follow-up (not required for this phase): remove unused
   portfolio-risk CSS in `stock-app-ui/src/styles.scss`.

### Step D — Verification gate

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
# services tests in both envs if any import path changes
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

No Angular build is required unless Step C changes UI-facing capability copy
that the client displays (today it does not render `retirement-risk`).

## 5. Explicit non-goals

- Do not change brokerage CSV schemas, Symbol Ledger math, or public JSON field
  names.
- Do not remove beta/Greek **fetching** or materialized artifacts.
- Do not merge Python runtimes or import `utilities/` from FastAPI.
- Do not revive Trade Groups or a portfolio-risk dashboard.
- Do not delete `services.options_market`.

## 6. Risk and rollback

| Risk | Mitigation |
|---|---|
| Coverage classification drifts | Move tests with the function; compare outputs on the existing importer fixtures before/after |
| Out-of-tree importer of `app.options_risk` | Document breaking change in commit message; modules are not a published package API |
| Capability clients rely on `retirement-risk` id | §7 decision; prefer reword-in-place over removing the id unless approved |

Rollback is a revert of the retirement commit; no data migration is involved.

## 7. Owner decisions required

Approve or amend before implementation:

1. **Proceed with deletion** of `app.options_market`, dead `app.options_risk`
   code, `config/options_risk.yaml`, and their dashboard-only tests after the
   coverage move? (Recommended: **yes**, given the in-repo audit.)

2. **New home for `apply_call_coverage`:**  
   `brokerages/call_coverage.py` (recommended) vs keep a slimmed `options_risk.py`
   that only exports coverage?

3. **`retirement-risk` capability:**  
   - **(A)** Reword to describe market-data enrichment readiness (Greeks/beta for
     retirement options), not “risk figures”; keep id `retirement-risk` for
     compatibility, or  
   - **(B)** Remove the capability entirely and update `test_capabilities.py`, or  
   - **(C)** Leave unchanged for a later pass.

4. **Confirm no known out-of-tree scripts** import `app.options_market`,
   `app.options_risk`, or read `stock-app/config/options_risk.yaml`.

## 8. Suggested commit shape (post-approval)

One focused commit (or two: move+characterize, then delete):

1. `refactor: move call coverage out of the retired risk module`
2. `chore: delete the unused options-risk dashboard subsystem`

Do not mix this with Phase 4 contract work or Angular cleanups.
