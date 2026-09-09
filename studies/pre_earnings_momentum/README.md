# Pre-earnings momentum strategy

This package contains smallFish's pre-earnings momentum scanner, its canonical
candidate engine, and the historical studies used to evaluate the strategy.
It is the current home for the durable conclusions from the July 2026 analytics
review and remediation work.

Part of the Research Studies catalog — see [`../README.md`](../README.md) for how
studies are published, verified, and frozen. Published at
`/studies/pre-earnings-momentum`. Its default Study 1 remains `FAILED` with
`CONFIRMATORY` evidence; the separately frozen Study 3 is also available there.
Study 4 is a separate exploratory Friday-only execution extension and does not
change either prior verdict.

The live UI scan requires a current upcoming-earnings cache; it reuses a cache
fetched within one day with sufficient coverage or conditionally refreshes it
from Finnhub. If refresh is required, `FINNHUB_API_KEY` must be configured and
the scan fails closed rather than publishing candidates from stale event data.
The published study record needs no credential. The separate multi-year
`earnings_history.csv` is maintained through `./commands.sh earnings-history`
and is never fetched automatically in the live request path.

## Current status

**The original strategy did not demonstrate an edge over SPY in its one-shot
holdout. Its failed result remains the default and is not superseded.**

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

Study 3 is a separately pre-registered post-earnings Risk-On method. In its
spent 2023–2025 holdout, the selected Risk-On portfolio grew from **$50,000 to
$109,465.01 (+118.93%)**, versus identically costed SPY at **$92,267.22
(+84.53%)**. It passed its frozen cumulative endpoint by **34.40 percentage
points** across 393 completed stock trades. The result is confirmatory for that
specific protocol, but the static current universe is survivorship-limited,
execution is simulated, and annual drawdowns were not better than SPY. The
holdout cannot be rerun or tuned; broader claims require a new prospective or
point-in-time-universe study.

Study 4 applies the same low-fee post-earnings selection, sizing, T+7, cash
staging, and Risk-On rules, but changes **execution timing**: Study 3 made
daily decisions with next-session fills, while Study 4 evaluated Monday through
Thursday and executed every stock and SPY change together at Friday's open.
Its independent 2010–2025 Risk-On chain returned **+921.43%** versus SPY
**+697.74%**; the all-regime baseline returned **+889.36%**. It is
retrospective exploratory context, not a new holdout or a broader validation
claim.

Study 5 changes the decision cadence again: it performs one
evaluation immediately before the week's final NYSE session opens, uses only
evidence through the prior session's close, and executes the resulting batch
at that same open. It has no weekday scans or sticky weekday exits. The causal
timing and complete protocol are frozen in
[`post_earnings_weekly_open_decision_spec.md`](post_earnings_weekly_open_decision_spec.md)
as a separate retrospective exploratory method.

The proposed live management and Tastytrade execution workflow is documented
in [`../../docs/STUDY4_LIVE_MANAGEMENT_DESIGN.md`](../../docs/STUDY4_LIVE_MANAGEMENT_DESIGN.md).
The operational evaluator is `./commands.sh study4-live-evaluate`. FastAPI
consumes its checksummed artifacts; it never imports this package. Frozen Study
4 specs and published numbers are unchanged.

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
Risk-On-or-Neutral arm. The development runner cannot access 2023–2025.

The separately guarded 2023–2025 one-shot holdout is frozen in
[`post_earnings_hold_low_fee_holdout_spec.md`](post_earnings_hold_low_fee_holdout_spec.md).
It starts new $50,000 baseline and Risk-On chains in 2023, carries each chain
through 2025, and never accepts a development checkpoint:

```bash
./commands.sh pre-earnings-post-event-low-fee-holdout \
  --variant risk-on --year 2023 --origin-year 2023 \
  --run-id HOLDOUT-risk-on-2023 --confirm-low-fee-holdout
```

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

### Friday-only execution replay

[`post_earnings_weekly_batch_spec.md`](post_earnings_weekly_batch_spec.md)
defines a separate $0.0008/share, equal-arm replay that evaluates Monday through
the final decision session of each week and executes all stock and SPY changes
at the following Friday open. If Friday is a market holiday, that week's final
available SPY session is the execution session. Stock exits are deliberately
deferred too, so the post-event maximum may be late. Weekday virtual P&L never
feeds real sizing or reported performance.

The owner authorized only the already-observed 2022–2025 window. It is
exploratory development evidence, **not** a new holdout; it cannot revise the
published 2023–2025 Study 3 result.

```bash
./commands.sh pre-earnings-post-event-weekly-batch \
  --variant baseline --year 2022 --origin-year 2022 \
  --run-id weekly-batch-baseline-2022-2025-COMMIT --confirm-weekly-batch-development-run
```

Every following year requires the immediately prior variant-specific
`state_checkpoint.json`. After the two continuous chains finish:

```bash
./commands.sh pre-earnings-post-event-weekly-batch-comparison \
  --artifact-root "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_weekly_batch" \
  --baseline-tag weekly-batch-baseline-2022-2025-COMMIT \
  --risk-on-tag weekly-batch-risk-on-2022-2025-COMMIT \
  --start-year 2022 --end-year 2025 \
  --output "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_weekly_batch/reports/2022-2025-COMMIT/weekly-batch-comparison.csv"
```

### Study 5: one pre-open weekly decision

[`post_earnings_weekly_open_decision_spec.md`](post_earnings_weekly_open_decision_spec.md)
defines the independent 2010-2025 baseline and Risk-On study. The
week's full decision uses only prior-session-close evidence immediately before
the final NYSE session opens, then executes at that open. The interval is
already observed, so any result is retrospective exploratory evidence with
`NO_VERDICT`; it cannot reopen or replace Study 3 or Study 4.

The guarded runner requires a clean committed worktree and explicit owner
confirmation. The implementation agent must not run it; historical execution
was delegated to a separate agent.

```bash
./commands.sh pre-earnings-post-event-weekly-open-decision \
  --variant baseline --year 2010 --origin-year 2010 \
  --run-id study5-baseline-2010-2025-COMMIT \
  --confirm-weekly-open-decision-exploratory-run
```

Each later year requires its variant's immediately prior
`state_checkpoint.json`. The shared daily-study runner now enforces the same
Study 5 confirmation flag, 2010 origin, 2010–2025 window, clean worktree, and
predecessor implementation-commit identity, so a direct `--config` invocation
cannot bypass the wrapper. After both continuous chains finish, validate and
compare them with:

```bash
./commands.sh pre-earnings-post-event-weekly-open-decision-comparison \
  --artifact-root "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_weekly_open_decision" \
  --baseline-tag study5-baseline-2010-2025-COMMIT \
  --risk-on-tag study5-risk-on-2010-2025-COMMIT \
  --start-year 2010 --end-year 2025 \
  --output "$SFP_DATA_DIR/backtest/pre_earnings_momentum/post_earnings_weekly_open_decision/reports/2010-2025-COMMIT/study5-comparison.csv"
```

The saved 2010–2025 exploratory artifacts remain `NO_VERDICT / EXPLORATORY`.
Each variant has 835 weekly decision snapshots: 834 execution weeks whose
fills fall inside 2010–2025, plus one 2025-12-31 cutoff that schedules the
2026-01-02 open. Those year-end checkpoints each retain five pending orders
for that next-year open; no 2026 fills enter reported performance. The shared
runner loads the following year's SPY calendar only so a 31 December cutoff can
schedule that pending batch. Changing that end-of-period boundary needs an
explicit owner decision; do not alter the preserved artifacts or silently
omit the snapshot.

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

### Study 6: weekly SPXL/SPY allocation

The [frozen Study 6 specification](post_earnings_regime_staging_spec.md) defines
Risk-On-only stock entries with SPXL for residual capital during Risk-On and
SPY otherwise, alongside a stock-free ETF switching comparison. Existing Study
5 artifacts are read-only historical context.

Implementation is isolated in `post_earnings_regime_staging_engine.py`, with
three pinned configs selected by the guarded `post_earnings_regime_staging.py`
runner. It writes ETF-aware annual checkpoints and artifacts without changing
the Study 3/4/5 schemas. The authorized 2010–2025 run is complete:
`post_earnings_regime_staging_comparison.py` validated all three checkpoint
chains, their shared input snapshots and passive-SPY ledger. The final 2025
cutoff that would execute in 2026 is excluded by the frozen execution-date
boundary. The result remains `NO_VERDICT / EXPLORATORY`.

#### Calendar-year returns

The following total returns use the final adjusted close before each calendar
year and the final adjusted close within that year. The years are the four in
which Study 6's stock-plus-SPXL treatment had a negative calendar return. SPY
itself was positive in 2011 and 2015.

| Year | SPY | VOO | TLT | GLD | USO | Best of these funds |
|---:|---:|---:|---:|---:|---:|---|
| 2011 | +1.89% | +1.90% | **+34.00%** | +9.57% | -2.28% | TLT |
| 2015 | +1.23% | **+1.33%** | -1.79% | -10.67% | -45.97% | VOO |
| 2018 | -4.57% | -4.50% | **-1.61%** | -1.94% | -19.57% | TLT |
| 2022 | -18.18% | -18.17% | -31.23% | -0.77% | **+28.97%** | USO |

#### Best performers in the broader comparison

The fixed comparison universe contained 26 funds covering Treasuries, broad
bonds, gold, commodities, currencies, defensive equity sectors, Treasury
bills, and inverse-equity ETFs. Every included fund existed before 2011.

| Year | First | Second | Third |
|---:|---|---|---|
| 2011 | **ZROZ +60.36%** | EDV +55.94% | TLT +34.00% |
| 2015 | **UUP +7.01%** | XLP +6.87% | XLV +6.83% |
| 2018 | **RWM +11.57%** | UUP +7.05% | XLV +6.28% |
| 2022 | **PSQ +36.40%** | SDS +30.69% | USO +28.97% |

Selecting each year's winner retrospectively would introduce perfect hindsight.
These comparisons describe the selected years; they do not define a causal
defensive allocation rule.

#### What those winners represent

- **ZROZ:** 25+ year zero-coupon U.S. Treasuries. Its extreme duration produced
  exceptional gains when long-term rates fell in 2011.
- **UUP:** bullish U.S. dollar exposure.
- **RWM:** daily inverse Russell 2000 exposure.
- **PSQ:** daily inverse Nasdaq-100 exposure.
- **SDS:** daily leveraged inverse S&P 500 exposure.
- **XLP and XLV:** consumer-staples and healthcare equity sectors.

RWM, PSQ, and SDS target daily inverse returns. Their longer-period results can
differ materially from a simple inverse multiple because they reset daily.

#### SPXS returns

SPXS targets -300% of the S&P 500's return for one day. Its multi-day result is
path-dependent and should not be inferred by multiplying SPY's calendar return
by -3.

| Year | SPY | Naive -3x SPY | Actual SPXS | Broader-comparison winner |
|---:|---:|---:|---:|---:|
| 2011 | +1.90% | -5.69% | **-32.66%** | ZROZ +60.36% |
| 2015 | +1.23% | -3.70% | **-17.86%** | UUP +7.01% |
| 2018 | -4.57% | +13.71% | **+3.44%** | RWM +11.57% |
| 2022 | -18.18% | +54.53% | **+36.14%** | PSQ +36.40% |

SPXS gained during 2018 and 2022, but lost heavily in the positive, volatile
markets of 2011 and 2015. It nearly matched the broader winner in 2022, while
falling well short of the naive -3x calculation in every displayed year.
Calendar-year SPXS returns do not determine its result over Study 6's exact
weekly Risk-Off intervals; that policy requires a separately frozen replay.

### Study 7: weekly GLD Risk-Off staging

The [frozen Study 7 specification](post_earnings_defensive_regime_staging_spec.md)
keeps Study 6's regime classifier, stock rules, weekly clock, and costs, and
asks whether replacing Risk-Off SPY with GLD improves the return/drawdown
tradeoff. Implementation is isolated from Study 6. Historical 2010–2025
execution is unauthorized until the owner reviews the implementation commit.

```bash
./commands.sh pre-earnings-defensive-regime-staging \
  --variant stocks-spxl-gld-treatment --year 2010 --origin-year 2010 \
  --pinned-implementation-commit REVIEWED_COMMIT \
  --run-id study7-stocks-spxl-gld-2010-2025-COMMIT \
  --confirm-weekly-defensive-regime-staging-exploratory-run
```

The other variant names are `passive-spy`, `stocks-spy-control`,
`stocks-spxl-spy-control`, and `etf-only-spxl-gld`. After all five continuous
chains finish, validate them with:

```bash
./commands.sh pre-earnings-defensive-regime-staging-comparison \
  --artifact-root "$SFP_DATA_DIR/backtest/pre_earnings_momentum/weekly_defensive_regime_staging" \
  --passive-spy-tag study7-passive-spy-2010-2025-COMMIT \
  --stocks-spy-control-tag study7-stocks-spy-2010-2025-COMMIT \
  --stocks-spxl-spy-control-tag study7-stocks-spxl-spy-2010-2025-COMMIT \
  --stocks-spxl-gld-treatment-tag study7-stocks-spxl-gld-2010-2025-COMMIT \
  --etf-only-spxl-gld-tag study7-etf-only-spxl-gld-2010-2025-COMMIT \
  --start-year 2010 --end-year 2025 \
  --output-dir "$SFP_DATA_DIR/backtest/pre_earnings_momentum/weekly_defensive_regime_staging/reports/2010-2025-COMMIT/study7-comparison"
```

Study 7 remains `NO_VERDICT / EXPLORATORY` regardless of the historical result.
Do not add it to the published catalog without a separate owner decision.

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
| `post_earnings_weekly_batch.py` | Guarded 2022–2025 Friday-only execution replay |
| `post_earnings_weekly_batch_comparison.py` | Joins the two Friday-only annual series with their common SPY benchmark |
| `config/daily_redeployment.yaml` | Accepted $500 daily-scan parameters for the daily-redeployment study |
| `config/daily_redeployment_cash_staging.yaml` | Separate equal-only development configuration with post-scan cash staging |
| `cash_staging_study_spec.md` | Binding methodology for the independent cash-staging development study |
| `post_earnings_hold_study_spec.md` | Binding frozen T+7 post-event and market-regime methodology |
| `config/post_earnings_hold_*.yaml` | Separate baseline, Risk-On, and Risk-On-or-Neutral study contracts |
| `post_earnings_hold_low_fee_study_spec.md` | Frozen-design per-share-fee development methodology |
| `post_earnings_weekly_batch_spec.md` | Exploratory Friday-only execution methodology |
| `post_earnings_weekly_open_decision.py` | Guarded Study 5 baseline/Risk-On annual runner |
| `post_earnings_weekly_open_decision_comparison.py` | Validates and compares the Study 5 annual chains |
| `post_earnings_weekly_open_decision_spec.md` | Frozen Study 5 single weekly pre-open decision methodology |
| `config/post_earnings_weekly_open_decision_*.yaml` | Frozen baseline and Risk-On Study 5 contracts |
| `post_earnings_regime_staging.py` | Guarded Study 6 annual runner; separate historical authorization required |
| `post_earnings_regime_staging_engine.py` | ETF-aware Study 6 state, fills, passive benchmark and checkpoint contract |
| `post_earnings_regime_staging_report.py` | Atomic Study 6 annual artifact writer |
| `post_earnings_regime_staging_comparison.py` | Validates and compares complete A/B/C chains and optional Study 5 drift context |
| `post_earnings_regime_staging_spec.md` | Frozen Study 6 SPXL/SPY methodology and reporting contract |
| `config/post_earnings_regime_staging_*.yaml` | Frozen A/B/C Study 6 contracts |
| `post_earnings_defensive_regime_staging.py` | Guarded Study 7 annual runner; separate historical authorization required |
| `post_earnings_defensive_regime_staging_engine.py` | Dedicated Study 7 config schema, ETF-aware state, fills, calendar validation, and checkpoint contract |
| `post_earnings_defensive_regime_staging_report.py` | Atomic Study 7 annual artifact writer |
| `post_earnings_defensive_regime_staging_comparison.py` | Validates and compares complete A–E chains |
| `post_earnings_defensive_regime_staging_spec.md` | Frozen Study 7 GLD Risk-Off methodology and reporting contract |
| `config/post_earnings_defensive_regime_staging_*.yaml` | Frozen A–E Study 7 contracts |
| `config/post_earnings_weekly_batch_*.yaml` | Frozen baseline and Risk-On Friday-only execution contracts |
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
