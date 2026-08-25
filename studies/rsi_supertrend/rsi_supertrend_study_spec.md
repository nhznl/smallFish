# RSI/SuperTrend Pine replication — design and implementation handoff

**Design opened:** 2026-08-23

**Study ID:** `rsi-supertrend-pine-v1`

**Protocol status:** `FROZEN — HOLDOUT NOT YET RUN`

**Protocol frozen:** 2026-08-23

**Protocol amended:** 2026-08-25, before any 2022–2025 strategy result, to add
the owner-approved shared-TA implementation sensitivity.

**Work status:** Pine execution, both indicator providers, and paired
sensitivity outcome artifacts are implemented; independent review of this
stage is required before holdout authorization.

**Source:** supplied Pine Script v6

This is the frozen, self-contained handoff for the agent that will implement
and run the study. Owner approval and the commit containing this document form
the dated preregistration before any 2022–2025 strategy result is observed. Do
not change its methodology or outcome rules after that commit.

## 1. Owner decisions already made

1. The supplied executable Pine code is the complete, authoritative strategy
   definition. Implement its order logic exactly; comments and labels do not
   override executable behavior.
2. Development/parity covers 1999-01-01 through 2021-12-31.
3. The primary population is a fixed 14-ETF cohort: SPY, the nine long-history
   Select Sector SPDRs, QQQ, DIA, IWM, and MDY.
4. The current smallFish stock universe is a separate exploratory historical
   cohort and must be labeled survivorship-biased.
5. Indicator implementation, paired outcome construction, holdout execution,
   and publication are separate reviewed stages. Completed artifacts return for
   independent verification before any app publication.
6. The Pine implementation remains the sole primary result. A prespecified
   `shared_ta` variant will measure whether smallFish's established indicator
   conventions meaningfully change behavior or outcomes.

## 2. Research questions and evidence boundaries

### Primary question

How did the supplied Pine strategy behave when independently applied to the
fixed 14-ETF primary cohort?

This is the only cohort eligible for the study's primary verdict.

### Exploratory stock question

How did the same code behave historically on the current live smallFish stock
universe?

This cohort is permanently `EXPLORATORY` for this study. It applies today's
surviving/current membership historically and therefore has survivorship,
current-membership, classification, and availability bias. It cannot establish
performance for the historical investable stock population or support a
general stock-edge claim.

The study tests historical code behavior. It does not produce a forecast or
financial advice.

## 3. Authoritative Pine behavior

`specialBuy` is the Pine script's one-bar Boolean entry signal. With the fixed
inputs, it becomes true on the second occasion that RSI(10) crosses above its
SMA(10) while RSI is below 50. RSI moving above 50 resets the count before a
signal can form, and a triggered signal immediately resets the count to zero.

| Topic | Required executable behavior |
|---|---|
| RSI entry signal | The second RSI-over-RSI-SMA crossover below 50 |
| SuperTrend at entry | No entry filter; `specialBuy` alone creates the long order |
| SuperTrend role | Exit only, on `ta.change(stDirection) > 0` |
| Divergence | Visual-only, disabled by default, never used by an order |
| Repeated entries | Pine default `pyramiding=1`; do not add while already long |
| Transaction costs | Zero commission and zero slippage |
| Order timing | Calculate at close; fill market orders at the next bar's open |

Required consequences:

- The first qualifying crossover has no maximum age. It remains counted until
  RSI exceeds 50 or a second qualifying crossover occurs.
- RSI exactly equal to 50 neither resets nor increments the counter.
- The two crossovers need not be pivot lows, have similar depths, or have a
  minimum/maximum separation.
- A long can be entered while SuperTrend is already bearish. Because the exit
  requires a new `-1` to `+1` direction change, current bearish state alone does
  not immediately close it.
- `strategy.close` does nothing when no matching open trade exists.
- The divergence pivots and their five-bar right-side confirmation must not
  influence orders.
- A position open at the study cutoff remains open and is marked at the final
  close for equity reporting. Do not invent a liquidation absent from the Pine
  code.

## 4. Exact strategy mechanics to implement

Use completed daily bars. For every symbol independently:

1. Calculate RSI(10) from close using the literal Pine `ta.rma` gain/loss
   formula.
2. Calculate SMA(10) of RSI.
3. `bullCross` is true when RSI is above the signal now and was at or below it
   on the preceding bar.
4. Reset persistent `crossCount` to zero when RSI is greater than 50.
5. When `bullCross` is true and RSI is below 50, increment `crossCount`.
6. `specialBuy` is true when that increment makes `crossCount == 2`; reset the
   count immediately afterward.
7. Calculate TradingView-compatible SuperTrend using ATR(10), factor 2.5, and
   the built-in's recursive upper/lower-band behavior.
8. On `specialBuy`, submit the Pine long entry for 100% of that chart's current
   simulated equity. Fill at the next session's open.
9. Do not add while long. Indicator and counter state continue updating.
10. When SuperTrend direction changes from `-1` to `+1`, submit
    `strategy.close`. Fill at the next session's open.

Initial capital is $10,000 per independently simulated chart. The exact-code
result uses zero commission and zero slippage because the source declares
neither. State this as a replication assumption, not achievable execution.

The implementation must use a study-local Pine-compatible ATR. TradingView's
`ta.atr` is based on `ta.tr(true)`, whose first bar uses high-low. Do not change
smallFish's established shared ATR initialization to make this study match
Pine.

### Prespecified implementation sensitivity

Run the same strategy and order emulator with two indicator providers:

- `pine` — the primary implementation specified above;
- `shared_ta` — direct calls to `utilities.indicators.ta.compute_rsi`,
  `compute_sma`, and `compute_atr`.

The shared-TA variant must use the primary parameters: RSI(10), SMA(10) of that
RSI, ATR(10), trigger 50, cross target 2, and SuperTrend factor 2.5. It must not
substitute smallFish's usual RSI(14), price SMA(20/50), or ATR(14). Because
smallFish has no shared SuperTrend function, both providers use the same
TradingView-compatible recursive SuperTrend band and direction logic; only the
supplied RSI, SMA, and ATR series differ.

Everything downstream of indicator calculation is shared byte-for-byte:
`specialBuy` state, SuperTrend flip rule, next-open fills, whole-share sizing,
zero costs, sleeve accounting, cohort aggregation, and bootstrap inference.
Do not build a second emulator.

The paired comparison covers the primary ETFs and the separately labeled stock
cohort in development. When holdout is eventually authorized, both providers
must run in the same authoritative one-shot command and claim; never open the
holdout once for Pine and again for shared TA. The Pine result remains solely
eligible for the primary verdict. Shared-TA outputs are labeled
`IMPLEMENTATION_SENSITIVITY`; stock outputs retain the additional
`EXPLORATORY` and survivorship-bias labels.

Required comparison evidence:

- defined-mask and maximum absolute differences for RSI, RSI-SMA, ATR, and
  SuperTrend value;
- mismatched `specialBuy`, SuperTrend direction, and exit-flip dates;
- mismatched entry and exit fill dates and prices;
- per-symbol and cohort return, exposure, and drawdown deltas;
- whether the shared-TA bootstrap verdict category would differ, reported only
  as a secondary diagnostic.

Any primary-symbol fill mismatch is a behavioral implementation difference. A
different bootstrap verdict category is inferentially material, but neither can
replace, rescue, or reverse the Pine primary verdict.

## 5. Cohorts

### A. Primary ETF cohort — verdict-bearing

`SPY, XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY, QQQ, DIA, IWM, MDY`

These 14 ticker identities are fixed. Each is a separate Pine-style chart or
$10,000 sleeve. Aggregate results must be equal-weight across sleeves; never
pretend that all symbols simultaneously receive 100% of one shared portfolio.

Any missing, corrupt, or incomplete primary history fails the primary run
closed rather than dropping that instrument.

The four non-sector additions have distinct, established US equity-index
mandates. Their rationale was fixed before their strategy results:

- QQQ: Nasdaq-100 large-cap growth/technology-heavy exposure;
- DIA: Dow 30 large-cap blue-chip exposure;
- IWM: Russell 2000 small-cap exposure;
- MDY: S&P MidCap 400 exposure.

They were not chosen by strategy outcome. Do not add leveraged, inverse,
commodity, bond, thematic, or later familiar winners after results are seen.

### B. Current-stock cohort — exploratory and survivorship-biased

Resolve exactly once at the authoritative run as:

`universe.csv rows where type=STOCK` minus `retired_symbols.csv`.

Archive the sorted resolved list and SHA-256 of both source registries. Exclude
a stock only for a documented validation or coverage failure. IPOs and shorter
histories begin only after their indicators become defined; do not backfill
synthetic history.

Report this cohort separately. It has no pass/fail verdict and cannot be pooled
with the primary ETFs to inflate the primary sample size.

## 6. Data, windows, and the one-shot boundary

- Source: validated smallFish adjusted daily OHLC cache under `SFP_DATA_DIR`.
- Precision: the cache's adjustment vintage and two-decimal storage are
  authoritative. TradingView may differ because of provider, adjustment,
  session, or precision settings.
- Development/parity: **1999-01-01 through 2021-12-31**.
- One-shot holdout: **2022-01-01 through 2025-12-31**.
- Exclude 2026 because it is incomplete.

Indicators and `crossCount` receive causal pre-window history. Each evaluation
window begins flat and accepts entries only from signals inside that window.

The development window is practice: the implementer may use it for calculation
parity, execution debugging, and predeclared design decisions. The holdout is
the exam: after the frozen implementation reveals 2022–2025, those dates are
spent. A byte-identical verification is allowed; a changed strategy rerun is
exploratory and cannot become a second validation attempt.

This is a historical, procedurally sealed holdout, not a genuinely prospective
future test. Result language must retain that limitation.

## 7. Primary endpoint and inference

Primary endpoint:

> The primary cohort's equal-weight daily Pine-strategy return minus the
> equal-weight daily buy-and-hold return of the same 14 instruments during
> 2022–2025.

Frozen inference:

- Aggregate 14 independently marked sleeves at equal weight.
- Cash earns zero.
- Use a 63-session calendar moving-block bootstrap, 10,000 draws, fixed seed
  `20260823`, and a two-sided 95% confidence interval for mean daily excess.
- Require complete primary-cohort data and at least 756 benchmark sessions.
- `PASSED`: interval lower bound above zero.
- `FAILED`: interval upper bound below zero.
- `INCONCLUSIVE`: interval includes zero.
- Total return, drawdown, exposure, completed-trade return, win rate, duration,
  per-symbol outcomes, and SPY-only outcome are secondary diagnostics and
  cannot override the primary verdict.

Exact-code zero-cost output is primary because the source declares no
commission or slippage. No transaction-cost sensitivity is part of this frozen
protocol. Any later cost analysis is post-hoc exploratory and cannot rescue or
reverse the primary verdict.

## 8. Implementation requirements for the handoff agent

Maintain the `studies/rsi_supertrend/` runtime package without changing frozen
methodology absent another explicit pre-holdout owner amendment. Minimum
components:

- frozen YAML config matching every parameter and cohort above;
- study-local Pine RMA/RSI/ATR/SuperTrend helpers;
- direct shared-TA RSI/SMA/ATR provider plus one common SuperTrend recurrence;
- single-symbol order emulator with Pine default order timing and pyramiding;
- independent-sleeve daily equity curves;
- primary moving-block inference plus descriptive ETF/stock outputs;
- strict price validation and explicit exclusions;
- creation-only run directories with source, config, spec, universe, and output
  hashes;
- synthetic tests for indicator seeds, two-cross state, RSI=50, stale first
  cross, bearish-at-entry behavior, ignored repeated entries, next-open fills,
  same-bar entry/exit edge cases, cutoff marking, and missing data;
- a holdout guard requiring `protocol_status=FROZEN`, an explicit
  `--confirm-holdout`, a clean committed worktree, and no prior authoritative
  holdout directory.

Before relying on local calculations, compare fixed development fixtures with
a TradingView export using the same symbol, daily timeframe, price adjustment,
session, and input settings. Record any irreducible rounding/provider drift.

The FastAPI runtime must not import study code. After independent result
verification, add a materialized `data/studies/<id>/study.json` and catalog
entry through the established publisher. Do not add a live scan unless it is a
separately approved product feature.

Required verification before handoff:

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

## 9. Required run artifacts

- `instrument_summary.csv` — signal counts, closed/open trades, exposure,
  strategy return, buy-and-hold return, and data coverage per symbol;
- `daily_equity.csv` — each sleeve plus equal-weight cohort and benchmark;
- `trades.csv` — signal, next-open entry, direction state, exit signal/fill,
  return, duration, and exit reason;
- `exclusions.csv` — every rejected symbol and exact reason;
- `resolved_universe.json` — exact cohort membership and registry hashes;
- `summary.json` — primary endpoint, interval, verdict, secondary diagnostics,
  and evidence labels;
- `manifest.json` — source commit, dirty state, command, dependencies, frozen
  config/spec hashes, price/universe hashes, and output hashes.

Do not commit raw price data or stock-level position artifacts. The catalog may
publish aggregate statistics only.

## 10. Implementation, review, and run gates

Stage 1 is implementation only. The implementation agent may use the
development window for tests and parity, but must not execute the 2022–2025
holdout. The agent stops after committing the implementation and returns it for
independent review.

For the Stage 1 review, return:

1. frozen specification commit;
2. implementation commit;
3. proposed holdout command, without executing it;
4. development parity evidence;
5. test/check output;
6. any exclusions or data-quality warnings.

The verifier independently checks cohort resolution, causal timing, Pine
parity, order emulation, inference, artifact construction, holdout guards, and
tests. Only after the owner accepts that review may Stage 2 execute the frozen
holdout command once.

After Stage 2, the run agent stops and returns the exact command, authoritative
creation-only run path, manifest, artifact hashes, verdict, and warnings. It
must not retune, rerun a changed variant, soften the verdict, or publish to the
app. The verifier then checks price-input hashes, artifact hashes, recomputation,
primary inference, verdict language, and catalog projection. Only after that
verification may a separate publication change expose the aggregate result in
`/studies`.

## 11. Research record

- 2026-08-23: Draft opened before any smallFish RSI/SuperTrend outcome was
  calculated. Recorded code precedence, primary ETFs, exploratory current
  stocks, and the holdout concept.
- 2026-08-23 (2026-08-24 UTC): During an implementation misunderstanding, a
  temporary local development-only smoke run was executed for SPY plus the nine
  primary ETFs. It loaded all ten and printed descriptive development results
  covering 1999–2021, including 390 completed trades. No implementation or run
  artifact is retained in the repository, and no 2022–2025 holdout command was
  run. Because development outcomes were observed, they may be used only as
  development evidence and must never be relabeled as holdout evidence.
- 2026-08-23: Owner clarified that this task is documentation-only. Removed the
  runner, indicator helpers, tests, config, and command wiring; retained only
  this design/handoff and related documentation links.
- 2026-08-23: Added QQQ, DIA, IWM, and MDY to the verdict-bearing primary cohort
  by owner decision before any strategy result for those four funds was
  calculated. The earlier development smoke output covered only the original
  SPY-plus-nine set. The missing QQQ, DIA, and MDY files were backfilled. A
  read-only audit then confirmed strict OHLCV validity and exact SPY-session
  coverage throughout 2022–2025 for all 14 ETFs, plus continuous development
  coverage after QQQ's and IWM's first available dates. No holdout strategy
  result was calculated, and the one-off backfill script was removed.
- 2026-08-23: Owner approved the methodology, primary endpoint, inference, and
  staged implementation-review-run workflow. The protocol was frozen before
  any 2022–2025 strategy result was observed.
- 2026-08-25: Before any 2022–2025 strategy result, the owner amended the
  protocol to add a prespecified `shared_ta` implementation sensitivity. Pine
  remains primary. The variant uses identical parameters and execution, runs in
  the same eventual authoritative holdout, and cannot change the primary
  verdict. Indicator-provider implementation preceded outcome comparison.
- 2026-08-25: Paired Pine/shared-TA outcome comparison was implemented and
  covered with synthetic tests. No real development cohort was run, and no
  2022–2025 strategy result, authoritative claim, or TradingView parity report
  was created.
