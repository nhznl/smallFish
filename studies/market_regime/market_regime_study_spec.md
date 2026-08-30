# Market regime stock study — frozen research protocol

**Study ID:** `market-regime-baseline-v1`

**Protocol status:** `FROZEN — HOLDOUT APPROVED, NOT YET OPENED`

**Design opened:** 2026-08-29

This document is the frozen design for a market-regime research framework. It is
not evidence that regimes predict returns or improve a stock portfolio. The
baseline exists to test those claims and is
allowed to fail.

No code calculated, visualized, or summarized a 2021–2025 outcome while this
protocol was `DRAFT`. On 2026-08-29, the owner approved freezing the original
expanding two-state K-means selection, without a confirmation or minimum-state
duration filter. The 2021–2025 window may now be opened once from the clean
commit containing this protocol. The 2026 partial year is reserved for
live/incomplete monitoring and is never part of the historical holdout.

## 1. Question and priorities

Primary research question:

> Does a causal, economically interpretable market-regime classification add
> out-of-sample risk information beyond buy-and-hold SPY and a lagged SPY
> 200-day moving-average rule?

The ordered evaluation priorities are:

1. catastrophic drawdown and tail-loss behavior;
2. risk-adjusted return;
3. stock-exposure position sizing;
4. distinguishing ordinary volatility from crash risk;
5. absolute return.

The study is stock-only. It tests broad-market stock exposure using SPY and
does not include options, option pricing, assignment, or option-strategy
outcomes.

## 2. Repository boundary

The research implementation belongs in `studies/market_regime/` and runs in
the utilities/studies Python environment. It may use `utilities.price_reader`
and `utilities.manifest`. It must not import FastAPI code.

Generated inputs and results live below `SFP_DATA_DIR/market_regime/`. If the
application later consumes a regime output, the study will materialize a
versioned, provider-neutral JSON artifact and FastAPI will read that artifact;
`stock-app/` will never import `studies/` or `utilities/`.

The pre-earnings scan's existing `Risk-On` / `Neutral` / `Risk-Off` throttle is
a separate compatibility surface. This study must not change its fields,
thresholds, sizing factors, or historical behavior.

## 3. Milestone-one architecture

```text
studies/market_regime/
├── config/baseline.yaml       frozen protocol parameters
├── data.py                    injected sources, SPY validation, calendar join
├── features.py                causal daily features
├── models.py                  RegimeModel and fixed rule baseline
├── walk_forward.py            split tags and expanding annual folds
├── statistics.py              forward outcomes, persistence, transitions
├── backtest.py                execution, costs, equity curves, metrics
├── visualization.py           dependency-free SVG timeline
├── experiment.py              fail-closed CLI and artifact orchestration
└── README.md                  commands, outputs, and evidence boundaries
```

## 4. Data contract and provenance

### SPY

Use the repository's existing validated, auto-adjusted daily OHLCV cache. The
current checkout has continuous validated SPY rows from 1998-01-02 onward. The
experiment records a SHA-256 for every source year file it reads.

### VIX

Use Cboe's official `VIX_History.csv`, which contains daily DATE, OPEN, HIGH,
LOW, and CLOSE values from 1990 onward. Fetching is explicit and injected so
tests never use the network. The exact downloaded bytes are retained with a
hash and retrieval timestamp.

### Join rule

SPY sessions are authoritative. VIX is left-joined by date; VIX-only dates are
ignored and a missing VIX value is never forward-filled. A row that lacks a
required input is `UNAVAILABLE`, not silently assigned a benign regime.

### Cash return

Uninvested exposure earns a three-month Treasury-bill proxy from the Federal
Reserve H.15 `DTB3` daily discount-rate series. The raw source is retained and
hashed. A rate becomes available only after a one-SPY-session lag. Convert the
91-day bank-discount quote to an implied calendar-day holding return and accrue
the actual calendar days to the next SPY open. This is a reproducible cash
proxy, not a claim that every brokerage account earns that exact rate.

The current source audit found one SPY session without VIX (1999-12-31) and 33
VIX-only dates between the first and last local SPY sessions. This discrepancy
must remain visible in the data-quality report.

### Daily dataset columns

Raw columns:

- `date`, `spy_open`, `spy_high`, `spy_low`, `spy_close`, `spy_volume`, `vix`

Causal features, calculated with rows available on or before date T:

- simple SPY return: 1, 20, and 50 sessions;
- annualized realized volatility: 20 and 60 sessions, from log returns with
  sample standard deviation and `sqrt(252)`;
- SPY SMA: 50 and 200 sessions;
- SPY distance from SMA 50 and SMA 200: `close / SMA - 1`;
- VIX close.

No backward fill, centered window, whole-sample normalization, or
future-derived label is allowed.

## 5. Fixed baseline classifier

The baseline intentionally uses no fitted parameters.

- `positive_trend`: SPY close >= SMA200 and SMA50 >= SMA200.
- `negative_trend`: SPY close < SMA200 and SMA50 < SMA200.
- `elevated_volatility`: VIX >= 25 or RV20 >= 25% annualized.

The five composite states are:

| State | Rule | Interpretation |
|---|---|---|
| `BULL_LOW_VOL` | positive trend, not elevated | established positive trend without the fixed stress flag |
| `BULL_HIGH_VOL` | positive trend, elevated | positive long trend with unusually high absolute volatility |
| `BEAR_LOW_VOL` | negative trend, not elevated | established negative trend without acute volatility |
| `BEAR_HIGH_VOL` | negative trend, elevated | established negative trend with stress volatility |
| `NEUTRAL_TRANSITION` | neither established trend | mixed/transition trend evidence, regardless of volatility |

Rows without all required inputs are `UNAVAILABLE`.

These thresholds are not claimed to be optimal. Robustness checks around SMA
lengths 180/190/200/210/220 and stress thresholds 20/22.5/25/27.5/30 belong to
validation analysis and every attempted variation must be logged. No variation
may be selected by holdout CAGR.

## 6. Windows and walk-forward discipline

- warm-up/data start: 1998-01-02;
- research/model-selection data: 1999-01-01 through 2020-12-31;
- initial training: 1999-01-01 through 2004-12-31;
- annual expanding walk-forward predictions: 2005-01-01 through 2020-12-31;
- sealed historical holdout: 2021-01-01 through 2025-12-31;
- live/incomplete monitoring: 2026-01-01 onward.

Each annual fold trains through the prior calendar year and predicts the next
calendar year. The 2005 model trains on 1999–2004; the 2020 model trains on
1999–2019. Every annual prediction is out-of-sample relative to its fitted
model, while the combined 1999–2020 period remains a model-development
resource. The already-inspected 2015–2020 baseline result remains valid for
that fixed rule version, but it is not pristine validation for later model
selection. Only 2021–2025 is the final untouched holdout.

## 7. Forward regime statistics

For each state and 1, 5, 10, 21, and 63-session horizon, measure SPY behavior
from the classification close at T forward:

- count, mean, median, standard deviation, positive-return probability;
- downside deviation and mean/std ratio;
- 5% historical VaR and expected shortfall;
- probability of a return <= -5%;
- maximum/mean adverse excursion and favorable excursion from adjusted OHLC;
- mean and worst within-window drawdown.

These overlapping forward outcomes are descriptive. Their naive observations
are not independent and no daily Sharpe claim may be made from them.

Persistence outputs include run-count, mean/median/min/max duration,
probability the current run survives another 1/5/10 sessions, and a row-normalized
transition matrix. `UNAVAILABLE` breaks a run and is excluded from transition
probabilities.

## 8. Predeclared strategy comparison

Signals are formed after the close on T and become positions at the open on
T+1. Returns are open-to-next-open while a position is held. This prevents a
same-close fill using a close-derived signal.

Compare:

1. SPY buy and hold;
2. SPY close above SMA200, otherwise cash, with the same one-session lag;
3. one unoptimized regime-sizing policy:
   - `BULL_LOW_VOL`: 1.00;
   - `BULL_HIGH_VOL`: 0.75;
   - `NEUTRAL_TRANSITION`: 0.50;
   - `BEAR_LOW_VOL`: 0.25;
   - `BEAR_HIGH_VOL`: 0.00;
   - `UNAVAILABLE`: 0.00.

The mapping is a test subject, not trading guidance. The primary friction is 5
basis points per unit of one-way turnover; gross, 1 bp, and 10 bp sensitivities
will also be reported without choosing whichever looks best.

Metrics use compounded equity and include CAGR, annualized volatility, Sharpe,
Sortino, maximum drawdown, Calmar, average
drawdown, time underwater, best/worst month and year, daily hit rate, exposure,
and turnover. Maximum drawdown is exactly:

`equity / equity.cummax() - 1`

## 9. Leakage and integrity tests

Required automated tests:

- changing rows after cutoff T leaves all features and regimes through T
  byte-for-byte unchanged;
- a regime at T never changes when forward returns are mutated;
- rolling return, realized volatility, SMA, and distance formulas;
- SPY-calendar join, missing VIX, duplicate-date, and invalid-value behavior;
- annual walk-forward split boundaries have no train/predict overlap;
- a signal at close T cannot affect the return before open T+1;
- compounded equity and running-maximum drawdown math;
- transitions, durations, and persistence probabilities;
- holdout execution fails while protocol status is not `FROZEN`.

## 10. Bias register

| Risk | Milestone-one control |
|---|---|
| Lookahead in features | trailing-only formulas plus future-mutation tests |
| Lookahead in execution | next-open activation |
| Whole-sample scaling | no fitted scaler in baseline; later scalers fit per fold |
| Data revisions | raw hashes and retrieval timestamps; no revised macro data yet |
| Adjustment-vintage drift | hash every SPY cache input and use the validated reader |
| VIX methodology break | disclose the 2014 inclusion of weekly SPX options and inspect period sensitivity |
| Threshold snooping | fixed primary thresholds; log every robustness variation |
| Repeated holdout use | fail-closed sealed window and clean-commit confirmation gate |
| Famous-episode tuning | visual inspection is diagnostic only; no parameter may be chosen to color an episode “correctly” |
| Overlapping outcomes | descriptive label and no false independence claim |
| Trading-cost omission | turnover and predeclared cost sensitivities |
| Survivorship-biased breadth | breadth excluded until point-in-time constituents exist |

## 11. Model-comparison phase

- Cboe VIX term-structure history: useful, but requires futures settlement and
  roll conventions that must be specified separately.
- Treasury yields/yield curve: FRED is a possible source, but release timing,
  holidays, and revisions require explicit availability lags.
- Credit spreads: potentially useful, but the public FRED ICE high-yield spread
  series is now history-limited; do not make it a baseline dependency.
- Breadth: excluded until point-in-time S&P 500 constituents and delisting
  handling are available.
- VVIX, put/call ratios, MOVE, correlation, and dispersion: each needs a source,
  stable history, and incremental-information test before inclusion.
- K-means/GMM/HMM: later studies compare 2/3/4 states using fold-local scaling,
  deterministic seeds, state interpretation from training-sample properties,
  and no numeric-ID Bull/Bear labels.
- The primary comparison includes the fixed rule baseline, volatility
  targeting, K-means, Gaussian mixtures, and Gaussian HMMs with 2/3/4 states.
- Every fitted model uses fold-local scaling. State meaning is determined from
  training-sample trend and volatility properties, never numeric state IDs or
  forward returns.
- HMM prediction uses causal filtered probabilities. Viterbi or smoothed
  full-sequence states are prohibited because they can use later observations.
- Expanding-window results are primary; a ten-year rolling window is a
  prespecified sensitivity.
- The primary selection cost is 5 bps per unit of one-way turnover. A model is
  eligible only if its average exposure is at least 25%, its CAGR is no more
  than one percentage point below the 200-day-SMA benchmark, its Calmar is
  higher, and its maximum drawdown is no worse. Rank eligible candidates by
  Calmar, then Sortino, then maximum drawdown. If none qualifies, retain the
  200-day-SMA benchmark rather than forcing a sophisticated winner.
- Rule thresholds and rolling training windows are robustness evidence, not
  additional winner candidates.

## 12. Stop conditions

1. Implement and verify annual 2005–2020 walk-forward model comparison.
2. Return the attempted-variation ledger and research findings for review.
3. Freeze the complete simple/clustering/HMM comparison protocol before any
   model sees 2021–2025.
4. Open the holdout once, publish the result whether favorable or unfavorable,
   and never retune under this study ID.

## 13. Frozen candidate and holdout execution

The only holdout-eligible research candidate is `kmeans_2`:

- K-means with two states and the four features listed in section 11;
- expanding training beginning 1999-01-01;
- annual refitting with each year's prediction based only on earlier dates;
- fold-local standardization and the fixed deterministic seeds in configuration;
- training-property risk ranking and exposures 1.0/0.0;
- no confirmation, persistence, or minimum-duration filter;
- primary evaluation at 5 bps one-way turnover, with 0/1/10 bps descriptive
  sensitivities.

For the first 2021 open, the position is seeded by the 2020-12-31 close signal
from the 2020 research fold trained through 2019. Thereafter each newly fitted
annual model's first signal becomes active at the following session's open.

The final verdict applies the already-used eligibility checks against SMA200:
average exposure at least 25%, CAGR no more than one percentage point below
SMA200, Calmar above SMA200, and maximum drawdown no worse than SMA200. All
checks must pass. Buy-and-hold, SMA200, 10% volatility targeting, and the fixed
rule are reported as benchmarks. No other fitted candidate is evaluated on the
holdout, and there is no reselection using holdout outcomes.

The frozen selection artifact SHA-256 is
`f37f80b80630e10de1560f6ad3dd28b9b18a0f6dfe588ebb36477a571c607336`.
