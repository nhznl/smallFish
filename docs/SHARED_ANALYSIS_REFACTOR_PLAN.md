# Shared analysis refactor plan

Status: approved for implementation planning; no behavior change is authorized.

## Objective

Move reusable stock-analysis calculations out of the FastAPI application into
a top-level `analysis/` package that both Python runtimes can import. Preserve
all API response shapes, numeric conventions, scanner classifications, scoring,
and research evidence.

The migration is staged for review safety, but its completed state must contain:

- one implementation of each active calculation;
- no compatibility shims under `stock-app/app/`;
- no new `stock-app -> utilities` dependency;
- no filesystem, network, environment, FastAPI, pandas, or study dependencies
  in `analysis/`; and
- no changes to frozen study methodology, evidence, results, or verdicts.

## Architecture after the refactor

```text
models/            standard-library data and artifact contracts
    ^
    |
analysis/          deterministic, dependency-light financial calculations
    ^                                  ^
    |                                  |
stock-app/         utilities/ and studies/
API adapters       batch I/O and research orchestration
```

Allowed `analysis/` dependencies are the Python standard library, NumPy, and
`models/`. Both Python environments already pin NumPy. `analysis/` must accept
data through arguments and return values; it must not discover files, read
configuration, use the network, or consult the wall clock without an explicit
injectable argument.

`stock-app/app/dates.py` remains backend-owned because it defines API date and
timezone presentation. It is not shared research analysis.

## Target module ownership

The first refactor should improve ownership without redesigning algorithms.
Avoid combining relocation with a new scoring model or indicator convention.

| Current implementation | Final owner | Notes |
|---|---|---|
| `stock-app/app/trend_engine.py` numeric helpers | `analysis/numeric.py` | Preserve IEEE-754 single-precision and rounding behavior exactly. |
| `Daily`, trend result, indicator and trend functions | `analysis/trend.py` | Preserve SMA-seeded EMA and MACD warmup semantics. |
| `stock-app/app/ema_crossover.py` | `analysis/ema_crossover.py` | Preserve first-close EMA convention; keep `now` injectable. |
| `Weekly`, `GainLoss`, `Stock`, and `momentum-v3` scoring | `analysis/stock.py` | This is relocation only. A later composition redesign requires its own proposal. |
| `stock-app/app/dates.py` | unchanged | API serialization concern. |
| `stock-app/app/cache.py` | unchanged owner | It remains the adapter from files/configuration into analysis objects. |
| `stock-app/app/serializers.py` | unchanged owner | It remains the stable Angular JSON boundary. |

The names above are recommended, not an excuse for a broad redesign. If an
implementation detail requires a different filename, document it in the
handoff and keep the ownership rules intact.

## Existing indicator conventions

Do not consolidate functions solely because they share names. The repository
currently has distinct, intentional conventions:

- the scanner trend EMA is SMA-seeded;
- the technical-chart/EMA14-over-20 calculation is first-close-seeded; and
- `utilities/indicators/ta.py` contains pandas-facing research calculations,
  including pandas EWM behavior for MACD.

The refactor must retain explicit names, module boundaries, documentation, and
tests that prevent these conventions from being silently substituted for one
another. Moving or redesigning `utilities/indicators/ta.py` is out of scope.

## Frozen-study boundary

`studies/pre_earnings_momentum/momentum_v3_replay.py` is an intentionally frozen
causal replay required by the approved study specification. Do not edit, delete,
or redirect it in this refactor. It is a versioned research snapshot rather
than a second active product implementation.

Add or retain characterization coverage proving that the extracted live
`momentum-v3` behavior still matches the approved golden cases. Do not rerun a
spent holdout, regenerate pinned evidence, change materialized study artifacts,
or edit a frozen specification.

Future studies may use a separately approved, versioned analysis entry point,
but that policy is not retroactively applied to published studies here.

## Stage 0: baseline and inventory

Before editing:

1. Confirm the worktree state and preserve unrelated changes.
2. Inventory every import and public symbol from the three modules being moved.
3. Run the targeted stock-analysis tests to establish a baseline.
4. Record the baseline commands and results in the implementation handoff.

At minimum, inspect consumers in `cache.py`, `serializers.py`, and the stock-app
tests, plus references in documentation. Do not assume star exports preserve
the existing surface.

## Stage 1: extract implementation and add temporary shims

Create `analysis/` and move the implementations into their final modules.
Replace the three old FastAPI modules with import-only compatibility shims:

- `stock-app/app/trend_engine.py`;
- `stock-app/app/ema_crossover.py`; and
- `stock-app/app/stock_model.py`.

Requirements for this stage:

- A shim may only explicitly re-export names from `analysis/`; it may not
  contain calculations, constants with independent values, wrappers, or state.
- There must be only one implementation even during Stage 1.
- Preserve public symbol names and behavior long enough for old imports to pass.
- Add an architecture test for the `analysis/` dependency allowlist.
- Add a parity/characterization test that exercises the extracted implementation
  against existing fixed expectations.

Commit Stage 1 separately so its movement and compatibility surface can be
reviewed independently.

## Stage 2: migrate consumers and remove shims

Update all production and test imports to use `analysis.*` directly. Then delete
all three compatibility files. Update affected READMEs and architecture diagrams
in the same stage.

The final repository must satisfy all of the following:

```bash
test ! -e stock-app/app/trend_engine.py
test ! -e stock-app/app/ema_crossover.py
test ! -e stock-app/app/stock_model.py
! rg -n 'app\.(trend_engine|ema_crossover|stock_model)|from \.(trend_engine|ema_crossover|stock_model)' \
    stock-app utilities studies --glob '*.py'
```

Do not retain deprecated aliases in `stock-app/app/__init__.py`, dynamic module
registration, duplicated constants, copied algorithms, or undocumented import
fallbacks. Commit Stage 2 separately.

## Compatibility invariants

This work is a structural refactor. The following are fixed:

- all FastAPI paths and JSON field names;
- `SETUP_SCORE_VERSION == "momentum-v3"`;
- setup classification, score totals, component names, and reversal penalties;
- trend direction, strength, confidence, RSI, momentum, MACD, OBV, and warmups;
- weekly grouping and float32 rounding;
- YTD, midpoint, five-week, and five-day gain/loss conventions;
- EMA14-over-20 status, strict `$1` threshold, crossover age, market-close
  handling, and benchmark-session coverage;
- cache loading and malformed-row behavior;
- API date serialization; and
- published research artifacts and evidence.

Do not opportunistically fix suspicious semantics during the move. Record any
finding as a separate follow-up recommendation.

## Verification

Run targeted tests after Stage 1 and again after Stage 2. Run the full required
suites on the final tree:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
stock-app/.venv/bin/python -c "import analysis"
utilities/.venv/bin/python -c "import analysis"
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Also run the final no-shim/no-legacy-import checks from Stage 2. Tests must stay
offline. Do not build or load the Angular application unless implementation
changes cross into UI files or response behavior, which this plan does not
authorize.

## Commit and review expectations

Use two focused commits:

1. extract shared analysis with temporary explicit re-export shims;
2. migrate every consumer, delete shims, and update documentation.

Do not include unrelated worktree changes. Do not rewrite history merely to
hide the staged migration: the intermediate commit is useful review evidence,
while the final tree is the required shim-free state.

The implementation handoff must include:

1. final outcome and commit hashes;
2. old-to-new symbol/module mapping;
3. explicit confirmation that final shims and duplicate active implementations
   are absent;
4. behavior-preservation evidence and any parity tests added;
5. every verification command with pass/fail/count summary;
6. `git status --short`, `git diff --stat`, and final legacy-import search;
7. any unrelated pre-existing changes left untouched; and
8. risks or suspicious behavior discovered but deliberately not changed.

