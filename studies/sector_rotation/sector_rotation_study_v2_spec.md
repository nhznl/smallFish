# Legacy-nine sector rotation v2 — frozen full-period exploratory plan

**Plan frozen:** 2026-07-26, after observing the v1 pre-2020 primary and 2020+
sensitivity results, but before calculating the pooled 1998–2026 estimate,
matched full-period controls, or regime-difference interval.

**Study ID:** `legacy-nine-v2-full-period`

**Analysis class:** `EXPLORATORY_POST_OUTCOME`

**Status:** completed once on 2026-07-26. Exploratory; no pass/fail verdict.

## Outcome at a glance

The authoritative run at committed code `ed07424d` included all 108 disjoint
signal decisions and 3,376 candidate-pair events through 2026-07-24. The pooled
date-aggregate target-minus-source mean was **-0.2277% per 63 sessions**, with a
moving-block-bootstrap 95% interval of **-0.7320% to +0.2757%**. The interval
crosses zero and the point estimate is negative, providing no evidence of a
positive full-history association. The realized 80%-power minimum detectable
mean was 0.8072% per 63 sessions.

The full-period pair-event hit rate was 49.38%. Mean pair spread was -0.1790%
gross and -0.5790% after the frozen 40-basis-point pair cost. The pooled rule
ranked at only the 3.2nd percentile of 1,000 matched random-pair controls.

Plain momentum averaged -0.6007% (95% interval -1.7302% to +0.5025%). Rotation
minus momentum averaged +0.3729% (95% interval -0.7047% to +1.4598%), so the
rule did not reliably outperform that simple control.

The 2020+ date-aggregate mean was -1.2928%, compared with +0.0833% before 2020.
The exploratory `2020_PLUS - PRE_2020` difference was **-1.3762 percentage
points**, with a 95% interval of **-2.5906 to -0.1814 percentage points**. This
describes material historical instability but is not a fresh structural-break
test because both regime results were known when v2 was planned.

All analytical artifacts reproduced byte for byte, and every output and source
price hash matched the authoritative manifest.

## 1. Purpose and evidence boundary

V1 incorrectly excluded 2020–2026 from its primary even though no prior
ETF-focused forward study had observed that period. V2 answers the descriptive
question V1 should originally have asked: across every available disjoint
decision from 1998 through 2026, what was the historical forward association
between the fixed rotation rule and subsequent target-minus-source performance?

V2 is not a new confirmatory test. Its plan was written after observing v1's
pre-2020 and 2020+ estimates. Pooling those periods, comparing them, or applying
controls now cannot create a fresh validation claim. V2 may improve the
full-history estimate, expose instability, and quantify controls, but it cannot
lift the permanently descriptive gate for the live 11-sector page.

## 2. Frozen data, signal, and calendar

V2 inherits v1 without modification:

- benchmark SPY and legacy-nine universe XLB, XLE, XLF, XLI, XLK, XLP, XLU,
  XLV, and XLY;
- exact validated SPY-session alignment with no interpolation;
- 5-, 20-, and 63-session features over the trailing 127 sessions;
- nine-fund top/bottom-third thresholds (`leading_rank_max=3`,
  `lagging_rank_min=7`);
- target strengthening and improving in rank while source weakens and loses
  rank, with at least one confirming feature window;
- every qualifying ordered pair retained, with no top-N or best-window search;
- decisions beginning at common-calendar index 126 and advancing exactly 63
  sessions; and
- forward spread equal to target adjusted-close return minus source
  adjusted-close return over the following 63 sessions.

All scheduled decisions with complete forward outcomes are included. This is
expected to produce 108 non-overlapping signal decisions: 81 whose outcomes end
by 2019-12-31, one crossing the 2019/2020 boundary, and 26 beginning in 2020.
The input is frozen at the completed 2026-07-24 session; later cache rows are
ignored, and the runner fails closed unless it obtains exactly 108 decisions.

Pairs sharing a decision date are dependent. They are equal-weighted into one
date aggregate before every pooled or regime-level interval is calculated.

## 3. Frozen pooled estimate

The principal v2 output is the mean date-aggregate forward spread across all
108 decisions. Report mean, median, standard deviation, standard error,
normal-approximation 80%-power minimum detectable mean, and a two-sided 95%
moving-block-bootstrap interval using:

- block length four decisions;
- 10,000 draws; and
- deterministic seed 20260727.

There is no pass/fail rule and no primary hypothesis. Whether the interval
excludes zero is descriptive post-outcome evidence only.

## 4. Frozen regime analysis

Report the same date-level statistics separately for:

1. `PRE_2020`: outcomes ending on or before 2019-12-31;
2. `CROSS_BOUNDARY`: the single decision whose outcome crosses into 2020; and
3. `2020_PLUS`: decisions beginning on or after 2020-01-01.

Quantify `2020_PLUS mean - PRE_2020 mean` with an independent moving-block
bootstrap within each ordered regime, using the same block length, draws, seed,
and confidence level. This is a stability diagnostic, not a newly validated
structural-break test.

## 5. Frozen controls and costs

Controls use all signal dates and match the number of candidate pairs per date:

1. **Random ordered pairs:** seeds 0 through 999, sampled without replacement
   from all 72 ordered pairs. Report the pooled rotation mean's percentile.
2. **Plain momentum:** trailing-63-session top-three forward return minus
   bottom-three forward return on the same dates. Report its pooled interval and
   the paired rotation-minus-momentum interval.

Report pair-event hit rate and mean spread gross and after the same frozen
40-basis-point pair round-trip cost used by v1.

## 6. Multiple comparisons and omitted inference

V2 defines no pair-specific or trigger-window hypothesis tests. It will not
repeat v1's invalid event-level FDR approximation. Pair/window breakdowns, if
ever inspected, are descriptive only and require a separate date-clustered
method before any inferential fields can be emitted.

## 7. Artifacts and immutability

The separate runner `studies/sector_rotation/study_v2.py` writes once to:

`data/sector_rotation_study/legacy-nine-v2-full-period/runs/{run_id}/`

Artifacts:

- `decision_results.csv`;
- `candidate_events.csv`;
- `random_controls.csv`;
- `regime_results.csv`;
- `summary.json`; and
- `manifest.json` with source/output hashes, configuration, specification,
  commit, dirty state, arguments, and dependencies.

The first completed v2 run is authoritative for this exploratory plan.
Verification reruns reproduce the five analytical artifacts byte for byte in a
temporary directory. V1 artifacts and verdict are never read, overwritten, or
reinterpreted by the v2 runner.

## 8. Result language

Allowed: “Across the full historical legacy-nine sample, the fixed rule had an
estimated mean forward spread of X, with interval Y; the estimate differed by
regime.”

Not allowed: “validated,” “confirmed edge,” “out-of-sample success,” “live
signal,” or any statement that lifts the 11-sector product gate.

## Amendment log

- 2026-07-26: Original v2 exploratory plan frozen after observing both v1
  period results and before calculating the pooled/control/regime outputs.
- 2026-07-26: Pre-run amendment froze the input cutoff at 2026-07-24 and the
  expected decision count at 108. No pooled v2 output had been calculated.
- 2026-07-26: Recorded the one-shot exploratory result without changing the
  plan. No pass/fail verdict or product claim was created.
