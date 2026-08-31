# Daily pre-earnings redeployment — implementation handoff

**Audience:** External implementation agent

**Study ID:** `pre-earnings-daily-redeployment-v1`

**Status:** READY FOR IMPLEMENTATION. Design implementation and offline tests are
authorized. **Do not run the 2021 pilot or any historical year.** The owner will
authorize the first 2021 run separately after independent code review.

**Binding design:**
[`daily_redeployment_study_spec.md`](daily_redeployment_study_spec.md)

This handoff is the implementation and progress-tracking companion to the
binding design. When they disagree, stop and ask the owner; do not silently
choose a rule. The study specification controls methodology. Repository code
and committed configuration control existing behavior.

## 1. Objective

Implement the approved daily pre-earnings portfolio simulator under
`studies/pre_earnings_momentum/`, with deterministic offline tests and
auditable report generation.

The implementation must support two independent $50,000 arms:

1. primary equal allocation; and
2. secondary Momentum Scanner `setupScore`-proportional allocation.

It must reproduce the approved Momentum Scanner `momentum-v3` entry signal,
use causal historical earnings forecasts, evaluate held positions daily, trade
only on the specified triggers, sweep residual capital into whole SPY shares,
and produce reconciled daily/order/trade/year-end artifacts.

The implementation is complete only when all acceptance criteria in this
handoff and the binding design are met. Producing plausible returns is not an
acceptance criterion.

## 2. Authority and prohibitions

### Authorized

- New implementation, configuration, tests, and runner documentation needed
  for this study.
- A guarded `commands.sh` entry point.
- Synthetic and fixture-backed offline tests.
- Tiny synthetic smoke runs that cannot touch real cached market data.
- Updating this handoff's progress log with implementation evidence.

### Not authorized

- Running 2021 or any other historical year.
- Invoking the runner against `SFP_DATA_DIR` market history, even as a smoke
  test.
- Fetching earnings, prices, universe data, or any live-provider data.
- Publishing the new study in `definition.json` or the Research Studies
  catalog.
- Editing or regenerating predecessor evidence or materialized study artifacts.
- Changing existing scan, backtest, API, or UI behavior.
- Retuning thresholds or adding strategy variants.
- Committing, staging, pushing, or opening a pull request unless the owner asks.

The command must fail closed for `--year 2021` unless an explicit pilot
confirmation flag is present. Implement and test that guard, but **do not use
the confirmation flag during this task**.

## 3. Read before editing

Read these completely:

1. repository `AGENTS.md`;
2. [`daily_redeployment_study_spec.md`](daily_redeployment_study_spec.md);
3. [`README.md`](README.md);
4. [`backtest_spec.md`](backtest_spec.md) and
   [`backtest_spec_2.md`](backtest_spec_2.md);
5. [`candidate_engine.py`](candidate_engine.py),
   [`event_forecast.py`](event_forecast.py), and [`backtest.py`](backtest.py);
6. [`scoring.py`](scoring.py), to understand what this study must **not** use
   as its entry score;
7. `stock-app/app/stock_model.py`, `stock-app/app/trend_engine.py`,
   `stock-app/app/ema_crossover.py`, and `stock-app/app/cache.py`, as the
   canonical `momentum-v3` behavior to characterize without importing; and
8. `utilities/price_reader.py`, `utilities/indicators/`,
   `utilities/manifest.py`, `utilities/universe.py`, and relevant tests.

Before editing, run `git status --short`. Preserve every pre-existing change.
Do not fold unrelated work into this implementation.

## 4. Settled strategy rules

Do not reopen these decisions:

- Initial equity is $50,000 per arm.
- Entries require every inherited price/liquidity/freshness/event-consistency
  gate, an event 2–5 weeks away, setup exactly `BULLISH_CONTINUATION`, and
  penalized `setupScore > 50`.
- Preliminary bearish-reversal warnings remain eligible if the penalized score
  is still greater than 50.
- Candidate order is entry `setupScore` descending, then ticker ascending.
- Equal allocation is primary; score-proportional allocation is secondary.
- Whole shares only for stocks and SPY.
- Stock entry targets are at least $1,000 and at most $5,000.
- At most three simultaneous open-plus-pending positions per sector.
- Surviving stock positions are never routinely resized.
- Origin allocation is actionable. Later candidate deployment occurs only when
  an exit is scheduled or free cash plus sellable whole SPY shares can fund a
  $1,000 target after estimated costs.
- Entry uses the prior close and a close +3% limit-on-open guard.
- The only exit signals are:
  - underlying Momentum Scanner trend direction becomes `DOWN`/bearish;
  - capital-scaled close decline, linearly 20% at $1,000 actual entry principal
    to 10% at $5,000; and
  - planned T-1.
- `BULLISH_REVERSAL`, sideways, `WATCH`, and setup-score deterioration do not
  independently cause an exit.
- Setup score is an entry-selection and allocation input only.
- Non-T-1 risk exits pin the symbol for 30 calendar days; pure or simultaneous
  T-1 exits do not pin.
- Stock and SPY transaction costs are both 10 bps per side.
- The zero-cost diagnostic is a shadow ledger of identical executed orders,
  fills, and shares. It never feeds money back into decisions.
- Whole-share residue remains visible cash.
- Positions and state carry across December 31; annual checkpoints do not
  liquidate.

## 5. Architecture boundaries

The new implementation belongs in `studies/pre_earnings_momentum/` and runs
with `utilities/.venv/bin/python`.

Hard boundaries:

- Do not import `stock-app/` or FastAPI application modules from the study.
- Do not make `stock-app/` import study or utilities runtime modules.
- Do not move Momentum Scanner behavior into `models/`; `models/` owns
  standard-library-only data contracts, not application analytics.
- Do not put this logic in `services/`; services own raw provider transport.
- Any future API/UI integration must consume materialized artifacts and is out
  of scope now.
- Tests must not open a network socket.

The study needs a frozen local replay of `momentum-v3`. Use synthetic golden
fixtures and characterization tests to demonstrate parity with the canonical
backend behavior. Do not create a runtime dependency merely to avoid a small
amount of versioned study-local code.

## 6. Recommended implementation shape

Names may change if a clearer structure emerges, but keep responsibilities
separated and explain any deviation in the progress log.

```text
studies/pre_earnings_momentum/
├── daily_redeployment_study_spec.md       binding design
├── DAILY_REDEPLOYMENT_IMPLEMENTATION_HANDOFF.md
├── daily_redeployment.py                  CLI/orchestration only
├── daily_redeployment_engine.py           portfolio state machine/accounting
├── momentum_v3_replay.py                  frozen causal scanner calculation
├── daily_redeployment_report.py           artifacts and reconciliation
└── config/
    └── daily_redeployment.yaml            effective approved parameters

utilities/tests/
├── test_daily_redeployment_momentum.py
├── test_daily_redeployment_allocations.py
├── test_daily_redeployment_engine.py
├── test_daily_redeployment_reports.py
└── fixtures/
    └── momentum_v3_golden.json             synthetic expected results
```

Prefer small immutable dataclasses for orders, positions, pins, daily marks,
and portfolio state. Keep pure decision/allocation functions separate from I/O
so boundary cases are testable without files or real data.

The effective YAML must contain every adjustable study parameter, including
study ID, setup-score version, starting equity, gates, allocation floors/caps,
sector cap, entry limit buffer, drawdown endpoints, pin duration, and costs.
The code must validate the configuration and reject unknown or inconsistent
values. Do not reuse or edit frozen predecessor YAML.

## 7. Phased implementation plan

Complete phases in order. Keep later phases pending until the current phase's
exit criteria pass.

### Phase 0 — baseline and contract map

- [ ] Record initial `git status --short` in the progress log.
- [ ] Map each design section to proposed code and tests.
- [ ] Identify reusable causal helpers versus predecessor behavior that must
      remain untouched.
- [ ] Confirm no target path overlaps unrelated worktree changes.
- [ ] Run focused existing scanner/backtest tests before edits.

Exit criteria:

- Exact file plan recorded.
- Baseline tests and pre-existing failures recorded.
- No methodology ambiguity silently assumed.

### Phase 1 — frozen `momentum-v3` replay

- [ ] Implement causal OHLCV-to-trend/setup/score replay using bars through the
      supplied as-of session only.
- [ ] Include scanner context: shared reference date, freshness, and one-month
      SPY-relative strength.
- [ ] Preserve exact setup naming, reversal semantics, preliminary warning,
      penalty, score components, rounding, and version.
- [ ] Add synthetic golden cases for bullish continuation, preliminary bearish
      warning, confirmed bullish reversal, bearish continuation, sideways,
      stale data, missing SPY, and insufficient history.
- [ ] Prove `setupScore == sum(setupScoreComponents)` where evaluated.
- [ ] Prove no future bar changes an earlier as-of result.

Exit criteria:

- Golden parity tests pass offline.
- No production import across the runtime boundary.
- Every result identifies `momentum-v3`.

### Phase 2 — candidates and causal event context

- [ ] Reuse the validated price, universe, sector, SPY calendar, and causal
      anniversary-event services without changing predecessor behavior.
- [ ] Implement every hard gate from design section 6.
- [ ] Implement stable score/ticker ordering and ten-candidate report cap per
      sector.
- [ ] Exclude open, pending, pinned, and sector-full candidates.
- [ ] Add exact event-window and date-consistency boundary tests.
- [ ] Prove realized future events never affect candidate selection.

Exit criteria:

- Candidate decisions include every pass/fail reason.
- Exact 2–5 week and strict `setupScore > 50` boundaries pass.
- Candidate output is deterministic.

### Phase 3 — whole-share allocators

- [ ] Implement a shared deterministic minimum-lot and affordability layer.
- [ ] Implement equal allocation for new candidates only.
- [ ] Implement score-proportional water filling for new candidates only.
- [ ] Enforce $1,000 decision-time target, $5,000 limit-price reservation cap,
      entry-cost reservation, and three open-plus-pending positions per sector.
- [ ] Drop the lowest-scoring candidate when all minimum lots cannot be funded.
- [ ] Send unallocatable dollars to SPY/cash rather than resizing survivors.
- [ ] Cover price granularity, ties, unknown sector, rejected limit orders, and
      overnight affordability gaps.

Exit criteria:

- Cash can never become negative.
- Every share quantity is an integer.
- Allocation is deterministic and reconciles to reservations.
- Existing positions never change shares without an exit.

### Phase 4 — daily state machine and exits

- [ ] Implement origin allocation and conditional later candidate deployment.
- [ ] Evaluate held positions after every completed session.
- [ ] Implement bearish raw-direction exit only.
- [ ] Implement capital-scaled allowed drawdown from actual entry principal:

```text
clamp(20% - ((principal - $1,000) / $4,000) * 10%, 10%, 20%)
```

- [ ] Fix the drawdown percentage at entry; never recompute it from market
      value.
- [ ] Implement causal T-1 scheduling and pessimistic early-report handling.
- [ ] Record simultaneous triggers and give T-1 no-pin precedence.
- [ ] Implement 30-calendar-day arm-specific pins.
- [ ] Implement next-open order sequencing and same-session SPY close sweep.
- [ ] Carry open state through annual checkpoints.

Exit criteria:

- Tests cover $1,000, $2,000, $3,000, $4,000, $5,000, and clamped principals.
- A high `BULLISH_REVERSAL` score cannot trigger an exit by itself.
- Missing data cannot fabricate a risk exit.
- Every cash/share transition reconciles.

### Phase 5 — accounting, artifacts, and reports

- [ ] Implement independent equal and proportional ledgers.
- [ ] Apply 10 bps per side uniformly to stock and SPY transactions.
- [ ] Implement a zero-cost shadow ledger using identical executions without
      decision feedback.
- [ ] Materialize all six required artifact groups from design section 15.
- [ ] Reconcile daily equity against cash, open stocks, and SPY.
- [ ] Reconcile realized P/L and costs against completed orders/trades.
- [ ] Record unavailable/stale values as unavailable, never zero.
- [ ] Write atomically into a new run directory; never overwrite an existing
      run.
- [ ] Emit a complete reproducibility manifest and output hashes.

Recommended evidence root:

```text
$SFP_DATA_DIR/backtest/pre_earnings_momentum/daily_redeployment/<year>/<run-id>/
```

Do not create real output under this path during implementation. Test the writer
only against temporary directories and synthetic data.

Exit criteria:

- Artifact schemas match the design.
- A synthetic run reconciles byte for byte on repetition.
- The zero-cost shadow has identical orders/fills/shares to its cost-bearing
  parent and cannot affect it.

### Phase 6 — guarded command and documentation

- [ ] Add a command that uses the utilities runtime, for example:

```text
./commands.sh pre-earnings-daily-study --year YEAR
```

- [ ] Require an explicit `--confirm-2021-pilot` guard for 2021 and fail closed
      without it.
- [ ] Do not execute the command with the confirmation flag.
- [ ] Add command/tooling tests for routing, missing confirmation, invalid
      years, invalid config, and output collision.
- [ ] Update only the minimal module/root documentation needed to discover the
      command and understand that no historical run is yet authorized.
- [ ] Do not add a catalog variation or UI/API surface.

Exit criteria:

- Guard failure is tested without touching real data.
- Help text clearly labels the study as unrun development tooling.
- Existing commands remain compatible.

### Phase 7 — final verification and handback

- [ ] Run focused new tests.
- [ ] Run the full utilities suite.
- [ ] If `commands.sh` or tools changed, run
      `utilities/tests/test_setup_tooling.py` and a safe guard/help invocation.
- [ ] Run documentation, secret, and whitespace checks.
- [ ] Inspect the final diff for methodology drift, unrelated changes, secrets,
      developer paths, and generated real data.
- [ ] Update the progress log and prepare the completion report in section 11.

Exit criteria:

- All required checks pass or every pre-existing failure is evidenced.
- No 2021 or historical output exists.
- Completion report is sufficient for an independent reviewer to audit without
  reconstructing the implementation conversation.

## 8. Verification matrix

At minimum, run:

```bash
utilities/.venv/bin/python -m pytest -q \
  utilities/tests/test_daily_redeployment_momentum.py \
  utilities/tests/test_daily_redeployment_allocations.py \
  utilities/tests/test_daily_redeployment_engine.py \
  utilities/tests/test_daily_redeployment_reports.py

utilities/.venv/bin/python -m pytest -q utilities/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

If `commands.sh` changes:

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests/test_setup_tooling.py
```

If shared `models/` change unexpectedly, stop and justify the architecture
before proceeding; both Python suites would then be required. The expected
implementation does not need such a change.

Tests and implementation must use temporary directories and synthetic market
data. Do not point tests at the developer's real `data/` tree.

## 9. Reviewer rejection checklist

The independent reviewer should reject the implementation if any item is true:

- It imports `stock-app` from studies or changes production scanner behavior.
- It uses the pre-earnings `score_total` instead of Momentum Scanner
  `momentum-v3` `setupScore`.
- It treats `BULLISH_REVERSAL` as a bearish-trend exit.
- It exits because setup score deteriorated after entry.
- It uses final realized earnings dates for historical selection.
- It uses an entry-session close to decide an entry at that session's open.
- It sizes fractional shares or hides residual cash.
- It allows more than three open-plus-pending positions per sector.
- It reallocates surviving positions merely because new candidates appear.
- It recalculates the allowed drawdown from changing market value.
- It applies different cost rates to stocks and SPY.
- The zero-cost diagnostic changes orders or future decisions.
- It pins a pure/simultaneous T-1 exit.
- It liquidates at December 31.
- It silently overwrites an artifact directory.
- It runs 2021 or any historical market-data simulation before owner approval.
- It edits predecessor specs, configs, evidence, verdicts, or publication.

## 10. Progress log

Append entries; do not rewrite older evidence.

| Date/time | Phase | Status | Evidence / decisions | Next step |
|---|---|---|---|---|
| 2026-08-30 | Handoff | Complete | Owner approved external implementation handoff; 2021 run remains unauthorized. | External agent begins Phase 0. |
| 2026-08-30 15:27 PT | Phase 0 | In progress | Initial `git status --short`: only untracked `studies/pre_earnings_momentum/DAILY_REDEPLOYMENT_IMPLEMENTATION_HANDOFF.md` and `daily_redeployment_study_spec.md` on `main`. No other worktree changes. Baseline predecessor tests: `utilities/.venv/bin/python -m pytest -q utilities/tests/test_event_forecast.py utilities/tests/test_candidate_engine.py utilities/tests/test_backtest.py utilities/tests/test_event_backtest.py utilities/tests/test_scan_gates.py utilities/tests/test_scan_date_consistency.py` → 61 passed in 1.40s. | Record contract map and begin Phase 1. |
| 2026-08-30 16:10 PT | Phase 0 | Complete | File plan: `momentum_v3_replay.py`, `daily_redeployment_engine.py`, `daily_redeployment_report.py`, `daily_redeployment.py`, `config/daily_redeployment.yaml`, four new test modules + `utilities/tests/fixtures/momentum_v3_golden.json`. Reuse `event_forecast.py` (import only), `price_reader.read_prices_validated`, `universe.live_universe_symbols`/`get_sector`, `manifest.write_manifest`. Do not edit predecessor specs/YAML/runners/`definition.json`. T-1 fills at the open of the last SPY session before `P_entry` per the binding daily spec, not predecessor close fills. | Phase 1 momentum replay. |
| 2026-08-30 16:20 PT | Phase 1 | Complete | Frozen `momentum-v3` replay ported without `stock-app` imports. Golden fixtures characterized from canonical `Stock.build` on synthetic weekday series. `utilities/.venv/bin/python -m pytest -q utilities/tests/test_daily_redeployment_momentum.py` → 5 passed. Component sums match scores; future bars do not change earlier as-of results. | Phase 2 candidates through Phase 6 command path. |
| 2026-08-30 16:55 PT | Phases 2–6 | Complete | Candidates, allocators, daily engine, artifacts, guarded CLI, and docs implemented against synthetic data only. Focused suite 30 passed before command wiring; 2021 remains fail-closed and unrun. | Phase 7 full verification. |
| 2026-08-30 17:15 PT | Phase 7 | Complete | Focused tests 31 passed. Full `utilities/tests` 589 passed. `test_setup_tooling.py` 49 passed. `check_docs.py` PASS. `scan_secrets.py` PASS. `git diff --check` clean. `./commands.sh pre-earnings-daily-study --help` exit 0; `--year 2021` without confirm exit 2. No historical run, no `data/backtest/.../daily_redeployment` output, no commit/stage/push. | Independent review of working-tree changes. |
| 2026-08-31 | Review remediation | Complete | Corrected no-candidate SPY fallback, same-open exit redeployment, heterogeneous-price minimum lots, current-close shadow marks, annual state/shadow/benchmark/pending carry, cutoff-safe T-1, complete review metrics, and input-hash manifests. Added adversarial synthetic coverage. Focused tests 42 passed; full `utilities/tests` 600 passed; `test_setup_tooling.py` 49 passed; docs, secret scan, and `git diff --check` passed; help exit 0 and unconfirmed 2021 exit 2. No historical run or real market-data access. | Owner review; 2021 remains unauthorized. |
| 2026-08-31 | Independent-review fixes | Complete | Fixed P2-1 by resolving actual-open affordability from lowest rank upward while reserving cash for every higher-ranked order. Added decision sector occupancy plus `order_id` execution join (P3-1), cross-year holding-session counting (P3-2), and missing-open sector-cap protection (P3-3). For P3-3, pending-exit positions continue counting until their sale fills, the permitted stricter-cap approach. Added checkpoint/year guards and positive bearish-exit coverage (P3-4). Focused tests 48 passed; full `utilities/tests` 606 passed; `test_setup_tooling.py` 49 passed; docs, secret scan, `git diff --check`, help exit 0, and unconfirmed 2021 exit 2 all passed. No historical run, real-data access, commit, stage, or push. | Independent re-review; 2021 remains unauthorized. |
| 2026-08-31 | 2021 pilot | Complete | Owner authorized the one-time baseline run after independent approval. Frozen commit `699fd3bec1621c1fa4f792f848fe1d377ed415b2`; run `pilot-2021-699fd3b` completed once with the $300 price cap. Artifact hashes, 504 daily arm rows, 848 orders, 276 trades, cash, costs, sector caps, and zero-cost ledgers reconciled. | Review baseline and authorized price-cap sensitivities; later years remain unauthorized. |
| 2026-08-31 | Price-cap sensitivity | In progress | Owner authorized full 2021 replays at $500 and $1,000 maximum decision-close entry prices for both equal and proportional arms. The original $300 artifact remains unchanged as the baseline. | Freeze variant configs, then run each sensitivity once under a distinct run ID. |
| 2026-08-31 | Price-cap sensitivity | Complete | Frozen commit `fa824aeb507430864725be6345075f282f7f6eda`. Runs `pilot-2021-price500-fa824ae` and `pilot-2021-price1000-fa824ae` each completed once and passed artifact-hash, manifest, 504-mark, non-negative-cash, sector-cap, whole-share, and uniform-cost validation. Equal/proportional ending equity: $70,555.61/$70,660.62 at both caps, versus $69,552.09/$69,661.41 at the $300 baseline. The $500 and $1,000 portfolio artifacts are byte-identical; no stock above $500 passed all gates into selection. One newly admitted ROKU trade displaced a later losing LUMN trade in both arms, so the improvement is path-dependent. | Owner reviews the 2021 comparison and turnover before accepting a price cap; later years remain unauthorized. |
| 2026-08-31 | Entry-cadence sensitivity | In progress | Owner accepts the $500 entry-price ceiling and authorizes 2021 Monday/Thursday and Monday-only entry-scan replays for both arms. Daily held-position exits remain unchanged. Weekly slots are holiday-adjusted; off-cadence exit proceeds sweep into SPY until the next scan. | Implement and verify the schedule gate, freeze it, then run each cadence once. |
| 2026-08-31 | Entry-cadence sensitivity | Complete | Frozen commit `158bf3e2405cac76559b0ec141c5c8babdb28c63`; runs `pilot-2021-mon-thu-158bf3e` and `pilot-2021-mon-158bf3e` each completed once. Both passed output-hash, manifest, 504-mark, non-negative-cash, sector-cap, whole-share, uniform-cost, zero-cost-order, and scheduled-entry-date validation. In the primary equal arm, daily/Mon-Thu/Monday combined turnover was 36.043x/35.654x/32.230x, net return was 41.111%/31.488%/31.065%, and excess return was 10.652%/1.029%/0.606%. Mon-Thu lowered stock turnover but raised SPY turnover enough to leave combined turnover nearly unchanged. Monday-only lowered combined turnover 10.6% but also shifted average exposure from stocks to SPY and surrendered most baseline excess return. The proportional arm showed the same conclusion. | Owner reviews the completed 2021 development sensitivities; no later year is authorized. |
| 2026-08-31 | Rule-set selection | Complete | Owner freezes the active `daily_redeployment.yaml` values `price_max: 500.0` and `entry_scan_schedule: daily`. Daily held-position exits remain unchanged. Weekly entry cadences and the $300/$1,000 price caps are retained only as labeled 2021 development sensitivities. No historical replay was performed for this documentation-only decision. | Use the frozen daily/$500 rule set for any separately authorized next phase. |

Use statuses `Not started`, `In progress`, `Blocked`, and `Complete`. Include
test counts, exact failure summaries, and any approved deviation from this
handoff.

## 11. Required completion report

Return all of the following to the owner and reviewer:

1. Concise outcome and whether implementation is review-ready.
2. Changed-file list grouped by production, configuration, tests, and docs.
3. Design-section-to-code mapping.
4. Explanation of how `momentum-v3` parity was established.
5. Explanation of causal decision/entry/exit timing.
6. Allocation and accounting invariants.
7. Artifact schemas and a synthetic reconciliation example.
8. Exact commands and results for every verification check.
9. `git status --short` and diff summary.
10. Confirmation that no historical run, real artifact, commit, stage, push, or
    publication occurred.
11. Known limitations, unresolved questions, or reviewer attention points.

Do not report the task complete while a required check or design behavior is
missing. Do not substitute a generated 2021 result for implementation evidence.

## 12. Independent review workflow

After implementation, return the completed section-11 report and leave the
working-tree changes available for review. The original design agent's role is
review-only:

1. inspect the diff and effective configuration against the binding design;
2. run or independently verify the relevant checks;
3. report defects with file/line evidence and severity;
4. approve the implementation for owner consideration or send requested fixes
   back to the implementation agent; and
5. never run the 2021 pilot without the owner's separate authorization.

Do not ask the reviewer to finish incomplete implementation. If review finds a
defect, the external implementation agent owns the corrective edit and updated
verification unless the owner changes that division of responsibility.

## 13. Copy-ready kickoff prompt

```text
Work in the smallFish repository and implement the approved study described by:

- AGENTS.md
- studies/pre_earnings_momentum/daily_redeployment_study_spec.md
- studies/pre_earnings_momentum/DAILY_REDEPLOYMENT_IMPLEMENTATION_HANDOFF.md

Read those files completely before editing. The handoff is phased and includes
acceptance criteria, a rejection checklist, progress tracking, and the required
completion report. Follow it faithfully and update its append-only progress log
as you work.

Scope: implement and offline-test pre-earnings-daily-redeployment-v1 under
studies/pre_earnings_momentum. Preserve the published failed predecessor study,
existing scan/backtest behavior, API/UI contracts, and runtime boundaries.

Critical prohibitions:

- Do not run the 2021 pilot or any historical year.
- Do not access live providers or real brokerage/personal data.
- Do not import stock-app modules into studies.
- Do not edit predecessor evidence, frozen specs/config, definition.json, or
  published artifacts.
- Do not commit, stage, push, publish, or create a PR.

Use synthetic temporary data for all tests. Implement the guarded 2021 command
path but never invoke its confirmation flag. Run the verification matrix and
return the complete section-11 handback for independent review. If the binding
design and handoff conflict or a methodology decision is missing, stop and ask
instead of guessing.
```
