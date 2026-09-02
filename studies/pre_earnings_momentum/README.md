# Pre-earnings momentum strategy

This package contains smallFish's pre-earnings momentum scanner, its canonical
candidate engine, and the historical studies used to evaluate the strategy.
It is the current home for the durable conclusions from the July 2026 analytics
review and remediation work.

Part of the Research Studies catalog — see [`../README.md`](../README.md) for how
studies are published, verified, and frozen. Published at
`/studies/pre-earnings-momentum` with verdict `FAILED` and evidence level
`EXPLORATORY`.

The live UI scan requires a current upcoming-earnings cache; it reuses a cache
fetched within one day with sufficient coverage or conditionally refreshes it
from Finnhub. If refresh is required, `FINNHUB_API_KEY` must be configured and
the scan fails closed rather than publishing candidates from stale event data.
The published study record needs no credential. The separate multi-year
`earnings_history.csv` is maintained through `./commands.sh earnings-history`
and is never fetched automatically in the live request path.

## Current status

**The research workstream is closed. The strategy did not demonstrate an edge
over SPY in its one-shot holdout.**

The frozen Study 1 portfolio returned **+22.42%** from 2025-04-04 through
2026-06-26, while SPY returned **+49.17%**. The strategy had lower volatility
and drawdown and beat 85 of 100 random eligible controls, but those secondary
results do not overturn the failed primary endpoint.

Study 2 swept otherwise idle cash into SPY and returned **+53.16%** over the
same dates versus SPY's **+49.17%**. It is exploratory only: the design was
chosen after Study 1 exposed the deployment problem, and the date range was
already spent. It is not validation evidence.

Do not present either study as proof of a validated strategy edge. Any future
validation requires a dated pre-registration and genuinely new prospective or
point-in-time data.

## Strategy intent

The live scan looks for liquid stocks with an upcoming earnings catalyst and
constructive technical conditions, with the intent of exiting before the
earnings announcement. It is research tooling rather than trade advice.

The normal command paths are:

```bash
./commands.sh scan
./commands.sh backtest
./commands.sh event-backtest
```

The explicit `earnings` strategy selector is also accepted. Reports and study
artifacts are written to the strategy's namespaced data directories.

A separate daily-redeployment study (`pre-earnings-daily-redeployment-v1`) is
implemented as development tooling. Discover it with:

```bash
./commands.sh pre-earnings-daily-study --help
```

Every historical run requires explicit owner authorization. The command fails
closed for a standalone `--year 2021` origin unless `--confirm-2021-pilot` is
present. This does not change the published `FAILED` predecessor study. An
authorized continuous sequence names `--origin-year`; its first year cannot
accept prior state, and every following year must pass the immediately prior
`state_checkpoint.json` with `--state-in PATH`. This prevents cash, positions,
pending orders, pins, SPY, benchmark, and the zero-cost shadow from silently
resetting. The authorized 2010–2022 development sequence uses `--origin-year
2010`; its 2021 continuation is distinct from the standalone pilot artifacts.

Continuation runs accept only an explicitly frozen study configuration:
`config/daily_redeployment.yaml` for the original two-arm daily/$500 sequence,
or `config/daily_redeployment_cash_staging.yaml` for the separate equal-only
cash-staging development study. Other sensitivity configurations fail closed
even when their effective values happen to match a selected configuration. The
cash-staging study reserves only the fully specified next-session stock-entry
orders after each close, then sweeps the remaining whole-share-eligible cash to
SPY. It exists to measure the avoided overnight SPY round trips; it does not
alter the original study or its evidence. Its currently authorized initial
window is 2010–2016, after which the owner reviews results before deciding on
any later years. In `daily_equity.csv`, `summary.json`, and `report.md`, `strategy_return`,
`benchmark_return`, and `excess_return` are cumulative since the original
$50,000 strategy origin. They are not calendar-year returns in a continuation
year.

Historical execution prints one flushed `PROGRESS` line after every completed
SPY decision session and a final `YEAR_COMPLETE` line with session count,
elapsed seconds, and output directory. Redirect or `tee` each annual command to
a distinct log file for durable monitoring without changing the artifacts.

### Frozen 10-bps post-earnings T+7 study

The separate [`post_earnings_hold_study_spec.md`](post_earnings_hold_study_spec.md)
defines an equal-allocation post-event development study with three independent
variants: unrestricted entries, Risk-On-only entries, and Risk-On-or-Neutral
entries. All three preserve the approved cash-staging rule. They replace T-1
with a floor-protected hold that exits no later than the **open of the seventh
SPY session strictly after** the realized or visibly labelled fallback event
date.

Implementation, synthetic verification, and the authorized 2010–2022
development runs are complete and frozen. The command refuses every historical
invocation unless the owner has separately authorized it and the caller
supplies `--confirm-historical-run`.
The shared daily-study runner enforces the same guard if a post-event config is
passed directly, so the wrapper cannot be bypassed through the legacy command:

```bash
./commands.sh pre-earnings-post-event-study --help
./commands.sh pre-earnings-post-event-study \
  --variant baseline --year 2010 --origin-year 2010 \
  --run-id SERIES-baseline-2010 --confirm-historical-run
```

The other variant names are `risk-on` and `risk-on-neutral`. Continuation years
must supply the immediately prior variant-specific checkpoint, for example:

```bash
./commands.sh pre-earnings-post-event-study \
  --variant baseline --year 2011 --origin-year 2010 \
  --state-in "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_hold/baseline/2010/SERIES-baseline-2010/state_checkpoint.json" \
  --run-id SERIES-baseline-2011 --confirm-historical-run
```

Use a distinct durable `tee` log for every variant-year. After all three
sequences have been validated with `daily_redeployment_series_report`, join
their annual equal-arm rows with:

```bash
utilities/.venv/bin/python -m \
  studies.pre_earnings_momentum.post_earnings_hold_comparison \
  --artifact-root "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_hold" \
  --baseline-tag BASELINE_TAG --risk-on-tag RISK_ON_TAG \
  --risk-on-neutral-tag RISK_ON_NEUTRAL_TAG \
  --start-year 2010 --end-year 2016 --output PATH/post_event_comparison.csv
```

The comparison tool fails if the variants do not cover identical years, do not
contain only the equal arm, or disagree on the passive SPY benchmark.

### Frozen-design per-share-fee rerun

The separate
[`post_earnings_hold_low_fee_study_spec.md`](post_earnings_hold_low_fee_study_spec.md)
keeps the frozen post-event rules and changes only the transaction-cost model
to `$0.0008 × filled shares` on every filled stock and SPY side. It has its own
study IDs and artifact root, starts independent $50,000 baseline and Risk-On
chains in 2010, continues them through 2022, and does not include the
Risk-On-or-Neutral arm. The 2023–2025 period remains untouched.

Each invocation is guarded independently of the predecessor command:

```bash
./commands.sh pre-earnings-post-event-low-fee-study --help
./commands.sh pre-earnings-post-event-low-fee-study \
  --variant baseline --year 2010 --origin-year 2010 \
  --run-id SERIES-baseline-2010 --confirm-low-fee-development-run
```

Continuation years require the immediately prior low-fee checkpoint. The
two-series comparison command is:

```bash
utilities/.venv/bin/python -m \
  studies.pre_earnings_momentum.post_earnings_low_fee_comparison \
  --artifact-root "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_hold_low_fee" \
  --baseline-tag BASELINE_TAG --risk-on-tag RISK_ON_TAG \
  --start-year 2010 --end-year 2022 --output PATH/low_fee_comparison.csv
```

After an authorized continuous sequence finishes, validate its output hashes,
checkpoint chain, frozen commit/config, accounting constraints, sector caps,
whole-share fills, uniform costs, and zero-cost order identity while producing
calendar-year comparison rows with:

```bash
utilities/.venv/bin/python -m \
  studies.pre_earnings_momentum.daily_redeployment_series_report \
  --artifact-root data/backtest/pre_earnings_momentum/daily_redeployment \
  --series-tag SERIES_TAG --start-year START --end-year END \
  --output PATH/annual_summary.csv
```

`Equity Growth`, `SPY Growth`, `Excess Growth`, drawdown, and volatility fields
are numeric decimal returns (for example, `0.10` means 10%). `No Of
Transactions` counts every filled stock or SPY order side; cancelled orders are
excluded. `Completed Stock Trades` remains separate. Regime comments are
mechanically classified from that calendar year's local SPY return, maximum
drawdown, and annualized daily volatility. The report writes validation and
reproducibility sidecars beside the CSV.

In `decisions.csv`, `sector_open_plus_pending_count` and
`sector_open_plus_pending_counts` record sector occupancy at the decision.
Selected entries and scheduled exits expose `order_id`, which is the explicit
join key to the execution outcome or cancellation reason in `orders.csv`.

The owner-frozen rule set uses daily candidate scans and a $500 maximum entry
price. The earlier price-cap sensitivities use
`config/daily_redeployment_price_500.yaml` and
`config/daily_redeployment_price_1000.yaml`. They change only `price_max` from
the original $300 run and do not authorize a later historical year.

The owner-authorized churn sensitivities use
`config/daily_redeployment_monday_thursday.yaml` and
`config/daily_redeployment_monday.yaml`. They restrict new-candidate scans to
holiday-adjusted weekly slots while retaining daily held-position exits. The
2021 replays are complete under run IDs `pilot-2021-mon-thu-158bf3e` and
`pilot-2021-mon-158bf3e`; they remain development sensitivities and do not
authorize any later year.

## Package map

| Path | Responsibility |
|---|---|
| `scan.py` | Live scan orchestration and freshness/exclusion reporting |
| `candidate_engine.py` | Canonical gates, diagnostics, ordering, and sector-cap path shared with replay |
| `scoring.py` | Technical diagnostics and bounded score components |
| `event_forecast.py` | Causal naive-anniversary earnings-date forecast used by Study 1 |
| `backtest.py` | Frozen portfolio study runner |
| `event_backtest.py` | Event-study runner using completed decision bars |
| `daily_redeployment.py` | Guarded CLI and annual-checkpoint restore for the development daily-redeployment study |
| `daily_redeployment_series_report.py` | Fail-closed checkpoint-chain validation and annual comparison CSV |
| `post_earnings_hold.py` | Guarded variant-selecting runner for the frozen 10-bps post-event study |
| `post_earnings_hold_comparison.py` | Joins three validated equal-arm annual series with their common SPY benchmark |
| `post_earnings_low_fee.py` | Guarded baseline/Risk-On runner for the independent per-share-fee study |
| `post_earnings_low_fee_comparison.py` | Joins the two validated low-fee annual series with their common SPY benchmark |
| `config/daily_redeployment.yaml` | Accepted $500 daily-scan parameters for the daily-redeployment study |
| `config/daily_redeployment_cash_staging.yaml` | Separate equal-only development configuration with post-scan cash staging |
| `cash_staging_study_spec.md` | Binding methodology for the independent cash-staging development study |
| `post_earnings_hold_study_spec.md` | Binding frozen T+7 post-event and market-regime methodology |
| `config/post_earnings_hold_*.yaml` | Separate baseline, Risk-On, and Risk-On-or-Neutral study contracts |
| `post_earnings_hold_low_fee_study_spec.md` | Frozen-design per-share-fee development methodology |
| `config/post_earnings_hold_low_fee_*.yaml` | Separate baseline and Risk-On low-fee study contracts |
| `config/daily_redeployment_price_500.yaml` | 2021 development sensitivity with a $500 entry-price ceiling |
| `config/daily_redeployment_price_1000.yaml` | 2021 development sensitivity with a $1,000 entry-price ceiling |
| `config/daily_redeployment_monday_thursday.yaml` | 2021 $500 sensitivity with holiday-adjusted Monday/Thursday entry scans |
| `config/daily_redeployment_monday.yaml` | 2021 $500 sensitivity with a holiday-adjusted Monday entry scan |
| `config/scan.yaml` | Live behavioral strategy contract and candidate selection |
| `config/backtest.yaml` | Frozen Study 1 execution, portfolio, split, and inference settings |
| `backtest_spec.md` | Binding Study 1 protocol, amendments, and results |
| `backtest_spec_2.md` | Binding exploratory SPY cash-sweep study record |
| `EXPLAINER.md` | Plain-language explanation of the design and outcome |

Shared price, universe, event, indicator, artifact-manifest, and calendar
services remain at the `utilities/` level. The strategy package does not own
those platform services.

## Current candidate contract

The behavioral source of truth is `config/scan.yaml`. Its current frozen
selection contract is:

- price from $10 through $300;
- 20-session average volume of at least 4 million shares;
- 20-session average dollar volume of at least $10 million;
- an upcoming earnings event 2–5 weeks away;
- no more than three exchange sessions of price staleness;
- the earnings-date consistency gate when its history is available;
- all hard-gate passers ordered by `days_to_event` descending;
- score bands bypassed for selection, while scores remain diagnostics;
- at most 10 reported candidates per sector;
- Unknown market regime treated with the Risk-Off factor and throttle.

No diagnostic score can rescue a failed hard gate. The portfolio replay uses
the same canonical candidate engine, deterministic ordering, and sector-cap
behavior. Its causal anniversary forecast stands in for unavailable historical
earnings-estimate vintages; it is not exact parity with the live Finnhub event
feed.

## Durable analytics contracts

The July 2026 review found both computation defects and research-design defects.
The durable corrections are now enforced in code and tests:

- Price readers reject wrong-year rows, conflicting duplicates, non-finite or
  non-positive values, and impossible high/low/close relationships. A hard
  defect quarantines the complete symbol rather than silently dropping a row.
- Provider responses that omit cached dates or years cannot rewrite a guarded
  price history. Price adjustment rewrites and their slope recomputations are
  atomic and auditable.
- Scan and backtest features use completed bars at or before the decision time.
  An entry never uses a completed entry-day bar to trade at that day's open.
- The technical replay loads the contiguous requested year range plus warm-up;
  a missing required cache year fails the run.
- Volume spike compares the observed session with the **prior** 20-session
  baseline. The shared market-regime slope uses five completed sessions.
- Downside extension is bounded: a falling knife cannot earn maximum
  pullback/room credit merely because it is far below a moving average or
  Bollinger band.
- Missing or insufficient SPY context fails closed instead of becoming a
  favorable market regime.
- RSI, EMA/MACD warm-up, OBV direction, YTD anchoring, weekly volume width, and
  related backend calculations use the corrected shared conventions protected
  by regression tests.
- Scan, backtest, and event-backtest artifacts carry reproducibility metadata,
  including arguments, configuration, input hashes, Git revision, and dependency
  versions.
- User-facing results distinguish diagnostics and descriptive observations from
  validated performance claims.

Shared models, active configuration, and [`../../docs/DATA.md`](../../docs/DATA.md)
own the platform-wide data contracts. This README records how the strategy
consumes them; it does not replace them.

## Remediation disposition

The original audit and remediation-plan documents were removed after this
summary was established. Their detailed pre-fix evidence and rejected design
alternatives remain available in Git history. The findings that still matter
have the following disposition:

| Original problem | Current disposition |
|---|---|
| Intermediate backtest years silently omitted | Fixed; contiguous coverage is required |
| Entry-day completed features used for same-open entry | Fixed; decisions use the prior completed bar |
| Live and backtest candidate logic drifted | Canonical candidate engine shared; event-source parity remains limited by unavailable historical vintages |
| Current universe/events used as if point-in-time | Not manufactured away; survivorship and event-vintage limits are explicit |
| Stale data and unknown regime could look favorable | Fixed; freshness gate and fail-closed regime |
| Falling knives could receive maximum extension credit | Fixed with two-sided bounds |
| Indicator conventions differed across paths | Fixed and regression-tested |
| Old artifacts could not support an edge claim | Superseded by frozen Study 1; its holdout failed |
| Reusing the holdout could create a false validation claim | Closed; the 2025-04-04..2026-06-26 window is permanently spent |

## Study 1: frozen historical strategy study

The binding details are in `backtest_spec.md`; `EXPLAINER.md` gives the
plain-language rationale.

The frozen portfolio used:

- $100,000 initial equity;
- $4,000 base nominal per position, scaled by market regime;
- at most 25 open positions and 3 per sector;
- longest-event-first selection with least-represented-sector allocation;
- next-session limit-on-open at decision close plus 3%, with no retry;
- predicted T-1 exit, no protective stop, and a 70-session safety cap;
- 10 basis points of cost per side;
- block-bootstrap daily excess diagnostics and 100 random eligible controls.

The development result was encouraging but selection-tainted. The untouched
holdout failed because the portfolio was only about 47% invested on average
during a strong SPY rally. Individual trades had positive matched-SPY excess on
average, but the cash exposure gap dominated the portfolio comparison. That is
a diagnosis, not permission to resize and rerun the spent holdout.

## Study 2: exploratory SPY cash sweep

`backtest_spec_2.md` records the mechanical sweep design and results. It places
idle cash in SPY while preserving the Study 1 stock sleeve. Its replay slightly
beat SPY over the spent window, but its daily excess confidence interval crossed
zero and the study was designed after observing Study 1. It carries no holdout
or validation weight.

## Known limitations

- Historical universe membership is not effective-dated, so historical results
  are limited to the surviving current universe.
- Historical earnings data contains realized dates, not the forecast revisions
  known to traders at each past decision date.
- The Study 1 anniversary forecaster is causal but is only a stand-in for live
  event estimates.
- Daily OHLC simulation cannot establish intraday fill quality.
- Overlapping events and repeated ticker observations require portfolio-level
  and dependence-aware inference; naive trade counts are not independent proof.
- The spent holdout cannot be reused for a new validation claim.

These limitations are boundaries on interpretation, not open implementation
tasks. The stock-strategy workstream remains closed unless explicitly reopened.

## Verification

Run the utilities suite from the repository root:

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
```

The highest-value regression coverage includes strict price validation,
completed-bar timing, candidate-engine parity, freshness and market-regime
gates, deterministic ordering, score monotonicity, manifests, and frozen study
behavior.

## Documentation authority

When documents differ, use this order:

1. code, shared models, and [`../../docs/DATA.md`](../../docs/DATA.md) for
   executable behavior and platform data contracts;
2. `config/scan.yaml` and `config/backtest.yaml` for active/frozen configuration;
3. `backtest_spec.md` and `backtest_spec_2.md` for immutable study protocols and
   results;
4. this README for current package status and architecture;
5. `EXPLAINER.md` for non-binding plain-language rationale.
