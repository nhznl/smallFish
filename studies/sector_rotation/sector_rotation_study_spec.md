# Legacy-nine sector rotation — frozen historical study specification

**Protocol frozen:** 2026-07-26, before any pre-2020 sector-ETF price outcomes
were added to the repository cache or scored by this study.

**Study ID:** `legacy-nine-v1`

**Status:** completed once on 2026-07-26. The frozen primary **FAILED**.

**Methodology correction recorded after completion:** the protocol incorrectly
called 2020–2026 “spent/development” because descriptive sector snapshots had
been viewed. No ETF-focused forward study had been performed, and viewing
contemporaneous descriptive signals did not reveal their subsequent historical
outcomes. The full 2020–2026 forward period was therefore unspent when this
protocol was frozen and could have been included in the primary. The mistaken
cutoff unnecessarily reduced the frozen primary from 108 available decisions
to 81: 26 were labeled 2020+, and one decision crossed the artificial
2019/2020 boundary. Once the authoritative run calculated the separately
labeled 2020+ sensitivity, those outcomes became observed; they cannot now be
promoted into or used to redefine the primary. The original result and
artifacts remain immutable, with this correction attached.

## Outcome at a glance

The authoritative run at committed code `f02052f2` covered 81 disjoint
pre-2020 signal decisions and 2,611 candidate-pair events. Mean date-aggregate
63-session target-minus-source spread was **+0.0833%**, with a moving-block
bootstrap 95% CI of **-0.4524% to +0.6362%**. The interval crosses zero, so the
single frozen primary endpoint failed.

The realized aggregate standard deviation was 2.6670%, giving an 80%-power
minimum detectable mean of 0.8297% per 63 sessions. The point estimate was much
smaller. This is no evidence of a useful predictive association, while the
interval still permits small positive or negative effects.

Controls and fixed diagnostics do not rescue the primary: the real mean ranked
at the 74.3rd percentile of 1,000 random-pair controls; plain momentum averaged
-0.0498% (95% CI -1.2387% to +1.1621%); rotation minus momentum averaged
+0.1332% (95% CI -1.0384% to +1.3150%); and candidate-pair directional hit rate
was 50.33%. Pair-event mean was +0.1219% gross and -0.2781% after the frozen
40-basis-point pair cost.

The 2020+ sensitivity was -1.2928% (95% CI -2.4046% to -0.2565%, 26
decisions). The period was unspent at protocol freeze and became observed when
this sensitivity ran. Because it was not part of the frozen primary, it remains
adverse descriptive evidence rather than a second primary test.

**Secondary-inference limitation discovered after the run:** pair/window FDR
rows used normal approximations over pair events. Same-date pairs share sectors
and are not independent, so their `p_value`, `q_value`, and `reject_fdr` fields
are invalid for inference. In particular, the nominal 5-session trigger flag
must not be treated as a finding. Those archived rows are retained rather than
silently rewritten, and no secondary result can alter the failed date-level
primary.

**Product boundary:** the live Sectors page uses all 11 Select Sector SPDRs and
is permanently descriptive. This study cannot lift that product gate. It asks
a narrower research question about the nine funds with histories beginning in
1998 and cannot generalize to XLC, XLRE, or today's full 11-sector taxonomy.

Any post-result change creates a new exploratory study ID. It may not overwrite
or reinterpret this protocol's primary result.

## 1. Why the pre-earnings documents are precedents, not specifications

`studies/pre_earnings_momentum/backtest_spec.md` and
`learnings/understanding_backtest_statistics.md` supply reusable research
discipline: freeze one primary hypothesis, calculate power before testing,
model dependence in inference, compare with matched controls, retain immutable
artifacts, and never let secondary diagnostics rescue a failed primary.

Their concrete choices do not transfer. This study has no earnings events,
stock-selection portfolio, daily-SPY excess endpoint, ticker clustering, entry
fills, or cash allocation. Its observation unit, control construction, and
bootstrap are defined below for sector-pair relative returns.

## 2. Question and claim scope

Question: when the existing rotation rule identifies a weakening source sector
and strengthening target sector, does the target subsequently outperform the
source over the next 63 exchange sessions?

The study CAN estimate a historical association for the actual adjusted-price
paths of XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, and XLY through 2019.

The study CANNOT establish measured fund flow, validate a live trading edge,
cover XLC or XLRE, remove historical taxonomy/constituent changes, or validate
the current 11-sector Sectors page. The frozen pre-2020 evaluation is
procedurally sealed in this repository but uses public historical data and is
not a truly prospective holdout. The 2019 cutoff was an unnecessary design
restriction, not a consequence of prior ETF forward-outcome research.

## 3. Frozen inputs and data contract

- Benchmark calendar: SPY.
- Legacy-nine universe, alphabetically fixed: XLB, XLE, XLF, XLI, XLK, XLP,
  XLU, XLV, XLY.
- Source: the repository's yfinance adjusted OHLCV cache, one consistent
  adjustment vintage per symbol, validated by `read_prices_validated`.
- Requested history begins 1998-01-01. The common study calendar begins on the
  latest first session among SPY and all nine ETFs. From that date through the
  evaluation end, every fund must have every SPY session; missing sessions,
  conflicting duplicates, invalid OHLCV, or partial provider coverage fail the
  study closed. No interpolation or silently shortened window is allowed.
- Primary evaluation: decisions whose feature window and complete forward
  63-session outcome both end on or before 2019-12-31.
- The frozen protocol labels the already cached 2020-01-01 onward period
  spent/development-only and permits it only as a separately labeled
  sensitivity result. That rationale was mistaken: no historical ETF forward
  outcomes had been studied. The label is retained solely to describe what the
  immutable runner did; the period became observed only when that sensitivity
  was calculated.
- Price-cache hashes, exact configuration, code commit, dependencies, and the
  generated artifacts are recorded in the run manifest.

## 4. Frozen decision calendar and signal

After the common calendar begins, the first decision is the session at index
126, providing exactly 127 sessions for the current and prior 63-session
features. Later decisions advance by exactly 63 SPY sessions. Forward windows
therefore do not overlap: each outcome is adjusted-close return from decision
session `t` through session `t + 63`.

At each decision, run the existing descriptive logic on the trailing 127
sessions with these frozen study settings:

- feature windows: 5, 20, and 63 sessions;
- top/bottom thirds for nine funds: `leading_rank_max=3`,
  `lagging_rank_min=7`;
- target must be strengthening and improving in rank while source is weakening
  and losing rank;
- at least one of the three windows must confirm;
- volume remains a diagnostic and is not a signal gate;
- retain all qualifying ordered pairs (maximum 72), with no best-pair,
  best-window, or top-N selection.

For candidate `source -> target`, define forward relative spread as:

`target adjusted-close return(t, t+63) - source adjusted-close return(t, t+63)`.

Pairs on the same decision date are dependent. Equal-weight their spreads into
one decision-date aggregate. Dates with no candidates are reported as coverage
but are not observations in the conditional primary endpoint.

## 5. Primary endpoint and pass rule

**Single primary hypothesis:** the mean decision-date aggregate forward
relative spread is greater than zero in the pre-2020 evaluation.

Inference uses a moving-block bootstrap over ordered signal-decision
aggregates: block length four decisions (approximately one year), 10,000
draws, deterministic seed 20260726. The study passes only if:

1. at least 20 signal decisions exist;
2. the observed mean is positive; and
3. the two-sided 95% bootstrap confidence interval's lower bound is above zero.

Otherwise the primary fails. When fewer than 20 signal decisions exist, the
failure is specifically labeled underpowered/inconclusive rather than evidence
that the association is absent. The result is recorded once regardless of
direction.

## 6. A-priori power and detectable effect

Approximately 80 disjoint decision windows are available after the 127-session
warm-up and before 2020, before accounting for no-signal dates. For a two-sided
5% test with 80% power, the normal-approximation minimum detectable mean is
`(1.96 + 0.84) * sigma / sqrt(n)`:

| Signal decisions | Aggregate sigma | Detectable mean per 63 sessions |
|---:|---:|---:|
| 80 | 4% | 1.25% |
| 80 | 6% | 1.88% |
| 80 | 8% | 2.51% |
| 40 | 6% | 2.66% |
| 20 | 6% | 3.76% |

This study can resolve only a large quarterly relative edge unless averaging
pairs makes aggregate volatility unusually low. The runner must report actual
signal count, aggregate standard deviation, standard error, and the same
normal-approximation detectable mean using observed sigma. A failed or
zero-crossing result with a wide interval is not evidence of no modest effect.
No power result may be used to change the frozen endpoint after outcomes load.

## 7. Frozen controls

Controls use the identical dates, 63-session forward returns, and candidate
count per date.

1. **Random ordered-pair control:** for seeds 0 through 999, sample without
   replacement the same number of ordered pairs from all 72 possible pairs on
   each signal date. Report the real mean's percentile in this distribution.
2. **Plain momentum control:** on each signal date, rank the nine funds by
   trailing 63-session return; equal-weight the forward returns of the top
   three minus the bottom three. Report its mean and confidence interval and
   the paired date-level difference from the rotation aggregate.

Controls are diagnostics. Neither can rescue a failed primary endpoint.

## 8. Secondary and exploratory outputs

Always report signal coverage, positive-spread hit rate, median spread,
distribution quantiles, a 20-basis-point round-trip cost per long or short leg
(40 basis points per pair), and the separately labeled 2020+ sensitivity. The
runner's frozen “spent” label is historically inaccurate, as recorded above.

Pair-specific results, confirmation-count strata, individual trigger-window
strata, regimes, and alternative horizons are secondary or exploratory. Apply
Benjamini-Hochberg false-discovery-rate control at `q=0.05` separately within
each reported family. No member of those families changes the primary verdict.
No alternative feature, horizon, rank threshold, decision phase, universe, or
aggregation may be searched under `legacy-nine-v1`.

## 9. Structural limitations

- XLRE did not trade until 2015 and XLC until 2018, so both are excluded rather
  than synthetically backfilled.
- The legacy ETFs are real investable histories, but GICS changes redistributed
  real estate in 2015 and communication-services constituents in 2018. The
  economic meaning of the nine funds is not compositionally constant.
- Adjusted ETF price is a leadership proxy, not creations/redemptions or net
  subscription flow.
- Pair spreads share sectors within a date; the date aggregate, not each pair,
  is the primary inference unit.
- The older history is public and the rule was motivated after inspecting
  contemporaneous descriptive sector snapshots. Those snapshots did not expose
  the historical forward outcomes and did not spend 2020–2026. The study is
  nevertheless retrospective and weaker than a prospective study.

## 10. Immutable execution and artifacts

The frozen runner is `studies/sector_rotation/study_v1.py`, invoked by
`./commands.sh sector-rotation-study`. Its creation-only run directory is
`data/sector_rotation_study/legacy-nine-v1/runs/{run_id}/` and contains:

- `decision_results.csv` — one row per scheduled decision;
- `candidate_events.csv` — pair-level evidence and forward outcomes;
- `random_controls.csv` — one row per fixed random seed;
- `secondary_results.csv` — corrected secondary families;
- `summary.json` — the primary verdict, power, controls, and limitations;
- `manifest.json` — hashes for all source prices and outputs, arguments,
  configuration, commit, dirty state, dependencies, and timestamps.

The first completed primary run is authoritative. Verification reruns must
write to a temporary directory and reproduce the authoritative analytical
tables byte for byte; they do not create a new verdict. Runtime code must reject
configuration drift from the values frozen in
`studies/sector_rotation/config/sector_rotation_study.yaml`.

## Amendment log

- 2026-07-26: Original `legacy-nine-v1` protocol frozen. No pre-2020 sector ETF
  history had been added to the local cache and no study outcome had been run.
- 2026-07-26: Recorded the one-shot result without changing the protocol. The
  primary failed. Also recorded the post-run secondary-inference limitation;
  no retry or replacement endpoint was created.
- 2026-07-26: Corrected the false claim that 2020–2026 was already spent at
  freeze time. No ETF-focused forward study had occurred. The cutoff remains
  part of the immutable original protocol, and the 2020+ outcomes—now observed
  by its sensitivity run—were not retroactively promoted to the primary.
