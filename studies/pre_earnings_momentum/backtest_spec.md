# Pre-earnings momentum — historical strategy study (frozen specification)

**Original protocol frozen:** 2026-07-18 (before any result was observed)

**Final holdout configuration re-frozen:** 2026-07-18 (after development
selection and the 3% limit-on-open verification; before any holdout run)

**Study class:** upgraded E3 (realized-history event study with a causal
point-in-time date *forecaster*), as summarized in `README.md`.

**Amendments:** any change after first results requires a dated entry in the
Amendment log at the bottom, and invalidates any holdout run made before it.

## Outcome at a glance — one-shot holdout completed

**Result: primary endpoint failed.** Over the final holdout window
(decisions 2025-04-04..2026-06-26), the frozen no-sweep portfolio returned
**+22.42%** versus **SPY +49.17%** (**−26.76 percentage points**), across 281
completed trades. The reported mean daily excess versus SPY was **−0.0647%**
(CI95 **−0.1337% to +0.0041%**). This is the one-shot holdout result; its date
range is spent and cannot be used for any future validation claim. Study 2's
separate SPY-sweep replay is documented in `backtest_spec_2.md` and is
exploratory only.

## 1. Claim scope

This study CAN estimate: whether the deployable pipeline (gates → deterministic
event-lead ordering → sector-diversified weekly allocation → capped
limit-on-open entry → T-1-style exit),
driven by a *naive but causal* earnings-date forecaster, earned excess return
over 2021–2026 **on the surviving current universe**.

This study CANNOT claim: performance on delisted names (survivorship — the
universe, sector, and cap-tier maps are today's), performance of the live
Finnhub-estimate entry rule (the forecaster is a conservative stand-in), or
intraday execution quality. Result language must carry these limits.

## 2. Inputs (immutable at run time)

- Prices: `data/{2020..2026}/{SYM}.txt`, strict contract via
  `read_prices_validated`; quarantines recorded in the manifest. SPY was
  rewritten 2026-07-18 in a single adjustment vintage covering 2020→present
  (audit `OK` with `--require-full-year-coverage`).
- Events: `data/earnings_history.csv` (Yahoo realized dates, full live
  universe, fetched 2026-07-18, `--limit-events 40`, window 2018-01-01..
  2026-12-31) plus its `.meta.json` manifest. The file is treated as frozen
  for this study.
- Universe: `universe.csv − retired_symbols.csv` as of run date; sector map
  from the registry (documented classification limitation).
- Configuration: `scan.yaml` and `backtest.yaml` as amended by the final
  holdout freeze below: $10–$300 price range, 2–5 week event window,
  longest-event-first selection with score bands bypassed, regime throttle,
  least-represented-sector allocation, $4,000 fixed base nominal, 25-position
  cap, decision-close +3% limit-on-open entry with no retry/replacement, no
  protective stop, and reduced-size Risk-Off/Unknown entries allowed.

## 3. Decision calendar

Weekly decisions at each Friday (last benchmark session of the week, from the
cached SPY calendar). All features come from completed bars `date <= D`;
entry orders use only the completed decision-session close and are evaluated
at the next benchmark session's opening auction.

## 4. Point-in-time earnings-date forecaster (frozen)

Let `H(D)` be the symbol's realized earnings dates strictly before decision
date `D` (past events are public knowledge; using them at `D` is causal).

- **Prediction:** `P(D) = min{ E + 364 days : E ∈ H(D), E + 364 days > D }`.
  364 (52 weeks) preserves the weekday, matching the same-week-of-quarter
  cadence most US reporters follow.
- **Consistency errors:** for each realized event `R_k` with a 4-back
  predecessor, `err_k = | R_k − (R_{k−4} + 364d) |` in calendar days.
- **Consistency gate:** at decision `D`, using only errors computable from
  `H(D)`: require at least 2 defined `err` values among the most recent 4,
  and `max(err) ≤ 7` days. Symbols failing the gate have no predicted event
  and cannot pass the engine's `require_upcoming_event` gate.
- Semi-annual or irregular reporters fail the gate by construction
  (conservative).

Predicted events are fed to the canonical `build_candidates` engine as the
events frame (`event_type = "earnings-predicted"`). The frozen configuration
applies the event window and hard gates exactly as live, retains computed
scores as diagnostics, bypasses score-band filtering, and orders gate-passers
by `days_to_event` descending.

## 5. Entry rule (one observation per ticker-event)

A ticker-event is scheduled at the **first** weekly decision where the ticker
appears in the engine's final report. The ticker must have a bar exactly on the
decision session; otherwise the order is skipped rather than using a stale
close. Set `entry_limit = decision_close × 1.03` and submit a limit-on-open for
the next benchmark session. If the ticker lacks a bar on that entry session,
skip it. If `entry_open <= entry_limit`, fill at the opening price plus entry
cost. If `entry_open > entry_limit`, cancel the order immediately: do not
retry, do not substitute another candidate, do not consume cash, and do not
lock the ticker. Unused cash remains available at the next weekly decision.

Only a filled ticker becomes locked. While it is open, and after it exits, the
same ticker is locked out until `D > P_entry` (the predicted event at entry has
passed). This yields at most one filled primary observation per ticker-event
(option U1).

## 6. Exit policy (final holdout freeze)

- **Planned exit:** close of the last session strictly before `P_entry`
  (predicted T-1).
- **Protective stop:** none. Development tests showed that the original
  2.5×ATR stop harvested ordinary multi-week volatility and materially reduced
  return. There is also no profit target.
- **Forecast-miss handling (realized date `R`):**
  - If `R` occurs on/before the planned exit while holding: forced exit at
    the close of the first session strictly after `R` (position eats the
    post-report gap — deliberately pessimistic). If that lands before entry,
    exit at entry-day close.
  - If `R` is after the planned exit: exit at the planned session's close
    (harmless early exit).
- **Safety valve:** if neither exit is reachable (missing data/delisting),
  exit at the last available close and flag `exit_reason=DATA_END`; hard cap
  70 trading days.
- Costs: 10 bps per side (20 bps round trip), applied to every exit path.

## 7. Portfolio simulation

Initial equity $100,000; cash earns 0; no leverage. Max 25 concurrent
positions; at most 3 open positions per (current-registry) sector. Fixed base
position nominal is $4,000 multiplied by the engine's regime size factor:
$4,000 Risk-On, $2,400 Neutral, and $1,200 Risk-Off/Unknown. Risk-Off/Unknown
entries are allowed at that reduced size; orders below the $500 dust floor are
skipped. Cash and entry costs may reduce a final order.

Gate-passers are ordered by `days_to_event` descending (longest event lead
first). Portfolio allocation repeatedly chooses the highest-ranked candidate
from the currently least-represented sector, counting open and pending
positions and respecting the three-per-sector hard cap. A candidate with no
free slot or a full sector is skipped (no queueing). Daily close marks produce
the equity curve.

## 8. Benchmarks

1. **SPY buy-and-hold** over each split window (portfolio-level comparison).
2. **Matched-dates SPY excess** per trade: SPY return over the identical
   entry→exit sessions, subtracted from the trade return (event-study view).
3. **Random-eligible control:** identical gates, calendar, slots, sizing, and
   exit policy, but candidate rank replaced by a uniform random shuffle within
   each decision date; seeds 0..99; compare the real portfolio against the
   seed distribution.

## 9. Splits and discipline

- **Development:** decisions 2021-07-02 .. 2024-12-27.
- **Embargo:** no new decisions 2024-12-28 .. 2025-03-31 (open positions may
  close; > max horizon, spans the Q1 reporting cycle).
- **Holdout:** decisions 2025-04-04 .. 2026-06-26. Run **once**, only after
  every development-phase choice is frozen via a dated amendment. Report the
  outcome whatever it is.

## 10. Endpoints and inference

- **Primary (single hypothesis):** holdout portfolio mean daily excess return
  vs SPY > 0, with a stationary block bootstrap (mean block 21 sessions,
  10,000 draws) 95% CI excluding 0, and positive terminal excess.
- **Important secondary risk profile (nice-to-have, always report):** portfolio
  maximum drawdown, annualized close-to-close volatility (`sqrt(252)`), and
  total-return / absolute-maximum-drawdown ratio, each compared with SPY over
  the identical sessions. Desirable directions are shallower drawdown, lower
  volatility, and a higher return-to-drawdown ratio. These metrics describe a
  potentially valuable lower-risk outcome but do not replace or rescue a
  failed primary excess-return hypothesis.
- **Diagnostics (exploratory, labeled as such):** dev-period lead-shape
  curve; per-event matched-SPY run-up with ticker-clustered bootstrap; regime
  and cap-tier splits; forecaster hit/miss incidence and cost of misses; gate
  pass rates; random-control percentile. No diagnostic supports an edge claim.
- Scores, including `score_event`, remain report diagnostics but do not filter
  or order the frozen holdout candidate set. They cannot support an edge claim.

## 11. Result language

A positive holdout must be reported as: "positive excess return in a
survivorship-limited simulation with a naive causal date forecaster" — not as
a validated live edge. A negative holdout retires the numeric performance
claims until the strategy is revised (new pre-registration required).

---

## Development results — recorded 2026-07-18

The original protocol shell was frozen before these results were observed.
Every later development-driven change is recorded in the amendment log, and
the final parameter choices are now reflected in sections 1–11 plus the
explicit final freeze below. The holdout remains **untouched**.

### Runs

| Run | Config | Artifacts |
|---|---|---|
| Original primary | original protocol exactly (score ranking + 2.5×ATR stop) | `data/backtest/pre_earnings_momentum/strategy_study/development/` |
| Variant 1 | `--atr-stop-mult 0` (no stop), ranking kept | `.../strategy_study/development_nostop/` |

Both: 183 weekly decisions (2021-07-02 .. 2024-12-27), 1,603-ticker panel,
~1,261 forecast-gated predicted events per decision, ~21 candidates per
weekly report, 100-seed random controls, all manifests written.

Each artifacts directory contains:

- `trades.csv` — one row per simulated trade: decision/entry/exit dates and
  prices, `exit_reason` (T1_PLANNED / STOP / STOP_GAP / EARLY_REPORT / ...),
  `ret_gross`/`ret_net`, `spy_matched_excess` (trade return minus SPY over
  the identical sessions), predicted vs realized event dates, scores, regime,
  sector, position nominal.
- `equity.csv` — daily portfolio equity marks (the curve compared with SPY).
- `controls.csv` — per random seed: terminal equity and trade count (the
  distribution the real portfolio is ranked against).
- `summary.json` — headline metrics as printed by the run.
- `trades.csv.meta.json` — reproducibility manifest (commit, config, args,
  hashes, quarantines).

### Headline outcomes ($100k initial; SPY buy-and-hold: +35.9%)

| Configuration | Terminal | Return | Percentile vs own random controls |
|---|---:|---:|---:|
| Original protocol (ranking + 2.5×ATR stop) | $105,911 | +5.9% | 3rd |
| No stop (ranking kept) | $119,261 | +19.3% | 2nd |
| Random-pick controls, no stop (median) | $144,276 | +44.3% | — |
| Random-pick controls, with stop (median) | $135,229 | +35.2% | — |

Mean daily excess vs SPY was negative with CI spanning zero in both real
runs. Average invested fraction ≈ 56% (regime throttle).

**Reading the control columns.** The random controls draw from the full
gate-passing pool each week (the score bands are switched off, so the pool is
every gated name, not the ~21 the real report keeps), shuffle it, apply the
sector cap, and take that week's real report size — identical
entry/exit/slot/sizing machinery with rank replaced by chance. The two control
rows are the *median* terminal equity across 100 seeds; the percentile column
is how many of the 100 the real run beat (3 and 2 respectively). Controls also
trade *more* than the real run (median 353 vs 319 with the stop; 267 vs 229
without): only the weekly candidate budget is matched, never the total trade
count, and the real run's lead-time-favoring `event` score makes it hold names
~9 weeks (dev `days_to_event` median ≈ 60 of ~63) and recycle its 10 slots
more slowly than uniform random picks do.

### Findings

1. **The category carries the value.** Gates + forecastable earnings event
   3–9 weeks out + hold to predicted T-1, with **no scoring**, beat SPY by
   ~8 points at the control median — through the 2022 bear, at ~56–65%
   deployment. Per-trade (no-stop): 54% win rate, median matched-SPY excess
   +0.12%.
2. **The score ranking is inverse in this window.** Real portfolios sit at
   the 2nd–3rd percentile of their own random controls in both variants;
   matched-SPY excess by `score_total` tercile is monotonically backwards
   (no-stop: low +2.63%, mid −2.08%, high −1.86%). The 95-point composite
   selected systematically worse stocks than chance from the same pool.
   This confirms and strengthens the original review's suspicion that the
   technical composite was mildly inverse to forward returns.
3. **The 2.5×ATR stop is destructive at this horizon.** It harvested 53% of
   primary-run trades at mean −7.5% after ~13 sessions; trades reaching
   planned T-1 averaged +9.0% (+5.2% matched-SPY excess). Removing the stop
   alone was worth +13.4 points of terminal return (counterfactual run, not
   just conditioning on outcomes). The stop band was a median 6.7% of entry
   price (IQR 5.1–8.8%; 82% of trades tighter than 10%), so a fixed-percentage
   stop is dominated by the same finding — the problem is a tight downside stop
   at a multi-week pre-earnings horizon, not the ATR scaling. The recommendation
   is *no stop*, not a reshaped one.
4. **The point-in-time forecaster works.** Early-report surprise rate 3.4%
   (primary) / 7.0% (no-stop, longer holds); surprised trades were not the
   losers (+11% mean in the primary run). The consistency gate passed ~75%
   of covered tickers per decision.

### Interpretation and status

The deployable strategy as currently constituted **failed its development
window**; the failure decomposes cleanly into the ranking (dominant) and the
stop (large), while the underlying event-window category shows genuine
promise. Development-phase evidence spent so far: the primary run, one exit
variant, and the control distributions. The next configuration to be tested
should be the candidate holdout config, not further exploratory variants.

**Superseded pending decision (historical):** final frozen configuration for the single
holdout run. Recommended: gates + event window with deterministic
days-to-event-descending selection (no score ranking), T-1 exit with no
stop, throttle/sector-cap/gates unchanged. Inverting the score ranking is
explicitly rejected as overfitting.

## Score-free candidate exploration — recorded 2026-07-18

Development-only runs of the recommended score-free config and a regime
entry-gate variant. New runner overrides (dev-only, holdout refuses):
`--select-by {score,days_to_event,days_to_event_short}` and
`--block-risk-off-entries`. Artifacts under `.../strategy_study/development_devA`
and `.../development_devB`. Holdout still **untouched**.

- **A** — days-to-event selection (longest lead first), score bands OFF, no
  stop, size-only regime (`--select-by days_to_event --atr-stop-mult 0`).
- **B** — A + take no new entries while regime is Risk-Off/Unknown
  (`--block-risk-off-entries`); open positions still exit normally.

| Config | Terminal | Return | Ctrl median | Pctile vs own controls | Trades | Wks sat out |
|---|---:|---:|---:|---:|---:|---:|
| PRIMARY (score + 2.5×ATR stop) | $105,911 | +5.9% | $135,229 | 3rd | 319 | — |
| score, no stop | $119,261 | +19.3% | $144,276 | 2nd | 229 | — |
| **A: days-to-event (desc), no bands, no stop** | **$145,085** | **+45.1%** | $146,920 | **49th** | 223 | 0 |
| **B: A + sit out Risk-Off/Unknown** | **$123,140** | **+23.1%** | $155,893 | **1st** | 195 | 56 |

SPY buy-and-hold over the dev window: +35.9% ($135,938).

**Findings.**

1. **The category beats SPY, robustly.** A finished +45.1% and its *control
   median* is +46.9% — the whole "gates + event 3–9wk + hold to T-1, no stop"
   family clears SPY regardless of within-pool ranking.
2. **Days-to-event ordering is neutral — and not inverse.** A sits at the 49th
   percentile of its own controls (mean daily excess +0.00007, CI95
   [−0.00045, +0.00061], spans 0). Unlike the 95-pt score (2nd–3rd pct,
   *inverse*), lead-time ordering neither helps nor hurts vs random from the
   gated pool. It is a deterministic, defensible selection, not alpha.
3. **The regime entry-gate helped random selection but B itself cratered.**
   B's control median rose to $155,893 (> A's $146,920) — random picks benefit
   from skipping risk-off entries. Yet B landed at the **1st percentile**.
   Sanity check (2026-07-18): B ≈ A through 2022 (the bear it sat out; A−B gap
   only $1.7k at 2022-end) then diverges in 2023–24 (gap $8.9k → $21.9k). A's
   Risk-Off entries had −1.14% matched-SPY excess (the weak trades), but A's
   Risk-On entries earned +1.27% vs **B's Risk-On −0.03%** — same rule, same
   regime, a *different and worse name set* produced by the altered entry
   timeline (path dependence). B's crater is a window-specific, path-dependent
   artifact, **not** evidence that regime-gating entries is bad.
4. **Net:** the gated pre-earnings-event category is the edge; neither the
   score, the days-to-event ordering, nor the regime entry-gate adds robust
   alpha on top of it.

**Superseded interim recommendation (pre-freeze):** freeze **A** as the holdout config
(beats SPY, neutral-not-inverse, no fragile interactions); **reject B** (its
result is too path-dependent to stake the one-shot holdout on). The
regime-entry-gate idea is not dead but would need its own dev design + fresh
holdout (retry-trap discipline).

### Short-lead ordering check — recorded 2026-07-18

The development-only short-lead variants completed after the interim
recommendation above was written. They use the same score-free pool and
no-stop exit as A/B, but order candidates by `days_to_event` ascending
(shortest lead first):

- **A-short** — shortest-lead-first selection, score bands OFF, no stop,
  size-only regime (`--select-by days_to_event_short --atr-stop-mult 0`).
- **B-short** — A-short + no new entries while the regime is Risk-Off/Unknown
  (`--block-risk-off-entries`).

| Config | Terminal | Return | Ctrl median | Pctile vs own controls | Trades | Wks sat out |
|---|---:|---:|---:|---:|---:|---:|
| **A-short: days-to-event (asc), no bands, no stop** | **$154,960** | **+55.0%** | $148,096 | **62nd** | 370 | 0 |
| **B-short: A-short + sit out Risk-Off/Unknown** | **$188,984** | **+89.0%** | $151,511 | **94th** | 299 | 56 |

SPY buy-and-hold over the same development window remained +35.9%
($135,938). A-short mean daily excess vs SPY was +0.000118 with bootstrap
CI95 [−0.000435, +0.000688]; B-short was +0.000333 with CI95
[−0.000238, +0.000923]. Both intervals span zero. Forecast-miss incidence was
8.65% for A-short and 7.02% for B-short.

Artifacts are under `.../strategy_study/development_devA_short` and
`.../development_devB_short`. Fresh reruns under the corresponding
`*_rerun` directories reproduced `trades.csv`, `equity.csv`, `controls.csv`,
and `summary.json` byte-for-byte. The focused candidate-engine suite passed
(5 tests).

These are development observations only. No final holdout configuration is
frozen by this record, interpretation remains pending, and the holdout remains
untouched.

**Post-run implementation finding (2026-07-18).** The simulator sized each
pending next-open order against the same unreserved cash balance. When several
orders were scheduled by one decision, their combined nominal plus entry costs
could therefore exceed cash, contrary to section 7's no-leverage requirement.
Reconstruction found minimum cash of −$1,816 in A-short and −$3,641 in B-short.
The implementation now reserves pending nominal plus entry cost as each order
is scheduled. All portfolio results recorded above are pre-fix development
observations and must be rerun before they are used for a freeze decision. The
holdout remains untouched.

**Allocation experiment plan (historical; 2026-07-18).** Portfolio allocation is now
configurable as `portfolio.allocation_order`: `ranked` preserves the historical
report-order behavior, while `least_represented_sector` repeatedly takes the
highest-ranked candidate from the currently least-represented sector, counting
open and pending positions and retaining the hard three-per-sector cap. Missing
sector classifications share an `Unknown` allocation bucket. The development
runner exposes the dev-only `--allocation-order` override; the holdout refuses
it. The development comparison is recorded below; no mode is selected for
freeze.

**Price-range experiment plan (historical; 2026-07-18).** The shared scan configuration
now admits prices from $10 through $300 instead of the original $7 through
$150 range. Buckets are correspondingly defined as $10–$50, $50–$100,
$100–$150, and $150–$300. The development grid below uses this range; it is not
frozen for holdout.

**Portfolio-size change plan (historical; 2026-07-18).** Starting capital remains
$100,000. The pending configuration replaces percentage-of-equity sizing with
a fixed $4,000 base nominal per stock and increases the concurrent-position
limit from 10 to 25. The existing regime size factor still applies, producing
$4,000 Risk-On, $2,400 Neutral, and $1,200 Risk-Off/Unknown targets; cash and
entry-cost constraints may reduce the final order, and no entry nominal may
exceed $4,000. A position's marked value may subsequently rise above $4,000
through price appreciation. The development grid below uses this sizing; it is
not frozen for holdout.

## Post-fix development grid — recorded 2026-07-18

Eight requested configurations crossed earnings ordering (nearest/short versus
longest/long), portfolio allocation (ranked versus least-represented sector),
and Risk-Off entry handling (reduced-size entries allowed versus blocked).
Every run used the corrected cash reservation, $10–$300 price gate, fixed
$4,000 regime-scaled entry nominal, 25 total positions, three-per-sector cap,
no protective stop, and 100 random-control seeds. All ran the 183 development
decisions; the holdout remained untouched.

| Tag | Terminal | Return | Ctrl median | Ctrl pctile | Trades | Blocked wks | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| `short_ranked_riskoff` | $131,099 | +31.1% | $129,184 | 61st | 653 | 0 | −14.0% |
| `long_ranked_riskoff` | $127,777 | +27.8% | $130,669 | 37th | 513 | 0 | −14.5% |
| `short_least_rep_order_riskoff` | $128,820 | +28.8% | $129,201 | 49th | 649 | 0 | −14.5% |
| `long_least_rep_order_riskoff` | $127,614 | +27.6% | $128,615 | 44th | 521 | 0 | −15.5% |
| `short_ranked_riskoff_blocked` | $133,476 | +33.5% | $127,630 | 82nd | 543 | 56 | −14.4% |
| `long_ranked_riskoff_blocked` | $121,524 | +21.5% | $126,964 | 19th | 453 | 56 | −15.5% |
| `short_least_rep_order_riskoff_blocked` | **$134,988** | **+35.0%** | $127,471 | **88th** | 544 | 56 | **−13.2%** |
| `long_least_rep_order_riskoff_blocked` | $122,919 | +22.9% | $126,926 | 25th | 458 | 56 | −16.4% |

SPY buy-and-hold over the development window ended at $135,938 (+35.9%). The
best grid member, `short_least_rep_order_riskoff_blocked`, finished about $950
(0.95 percentage point) below SPY. Mean daily excess versus SPY was negative in
all eight runs and every stationary-bootstrap CI95 spanned zero.

Its risk profile was materially better: maximum drawdown was −13.2% versus
SPY's −24.5%, annualized volatility was 10.9% versus 18.3%, and total-return /
absolute-max-drawdown was 2.65 versus 1.47. These are important secondary
development diagnostics under the criterion above, not evidence that the
primary endpoint passed.

Development-only readings:

1. Nearest-event ordering beat longest-event ordering in every matched
   allocation/blocking pair. The advantage was modest with Risk-Off entries
   allowed (+1.2k to +3.3k terminal) and much larger with them blocked
   (+12.0k).
2. Risk-Off blocking interacted strongly with event ordering: it improved both
   nearest-event portfolios (+2.4k ranked; +6.2k least-represented) but reduced
   both longest-event portfolios (−6.3k and −4.7k).
3. Least-represented-sector allocation had a comparatively small and
   context-dependent effect: it trailed ranked allocation when Risk-Off entries
   were allowed, but improved both blocked portfolios by roughly $1.4k–$1.5k.
4. The strongest within-pool result was nearest-event + least-represented +
   Risk-Off blocked at the 88th percentile of its random controls. This is a
   development diagnostic, not a holdout edge claim.

Artifacts are under `.../strategy_study/development_<tag>/`. Manifest checks
confirmed the requested arguments and shared configuration for every run.
Reconstructed cash never fell below zero beyond sub-cent floating-point
residue. No configuration is frozen by this grid.

**Event-window sensitivity plan (historical; 2026-07-18).** A development-only
runner override can vary `event_min_weeks` / `event_max_weeks` without mutating
shared YAML and group artifacts under a named `strategy_study` subdirectory.
The requested experiment holds the maximum lead at five weeks, varies the
minimum from one through four weeks, and repeats the eight post-fix
ordering/allocation/Risk-Off configurations in each window. This 32-run grid is
exploratory multiple-testing evidence; its best member cannot be selected as if
it were a single pre-specified test. Results follow; holdout remains untouched.

## Event-window sensitivity results — recorded 2026-07-18

All 32 development runs completed with no stop and 100 random-control seeds.
Artifacts are grouped under `strategy_study/min_{1,2,3,4}_weeks/`, with one
`development_<tag>` directory per requested configuration. Every cell below is
`total return / control percentile / maximum drawdown`.

Codes: `S` = nearest-event ordering, `L` = longest-event ordering, `R` = ranked
allocation, `D` = least-represented-sector allocation, and `B` = Risk-Off entry
blocking. Absence of `B` means reduced-size Risk-Off entries were allowed.

| Config | 1–5 weeks | 2–5 weeks | 3–5 weeks | 4–5 weeks |
|---|---:|---:|---:|---:|
| SR | +37.1% / 46th / −7.3% | +36.3% / 48th / −7.8% | +38.2% / 56th / −7.2% | +28.3% / 7th / −8.4% |
| LR | +37.1% / 44th / −8.7% | +39.0% / 66th / −7.3% | +41.4% / 85th / −7.3% | +32.9% / 36th / −7.7% |
| SD | +36.5% / 26th / −7.2% | +37.8% / 50th / −7.9% | +36.7% / 45th / −8.6% | +35.0% / 39th / −7.7% |
| LD | **+42.6% / 76th / −7.8%** | **+42.1% / 88th / −7.0%** | +40.5% / 71st / −7.2% | **+37.8% / 77th / −6.9%** |
| SRB | +31.4% / 29th / −7.5% | +29.3% / 8th / −6.8% | +42.4% / 66th / −5.7% | +29.7% / 26th / −9.3% |
| LRB | +36.6% / 68th / −7.8% | +38.9% / 82nd / −7.3% | +41.3% / 55th / −7.0% | +29.1% / 19th / −10.0% |
| SDB | +30.6% / 16th / −7.5% | +30.6% / 13th / −6.8% | **+43.7% / 70th / −5.7%** | +31.1% / 21st / −8.9% |
| LDB | +40.2% / 83rd / −8.1% | +40.3% / 87th / −7.6% | +43.0% / 62nd / −7.2% | +31.4% / 21st / −10.7% |

SPY returned +35.9% with −24.5% maximum drawdown and 18.3% annualized
volatility over the identical sessions. Across this grid, portfolio volatility
ranged from 6.7% to 8.9%; every maximum drawdown was substantially shallower
than SPY's. No stationary-bootstrap daily-excess CI95 excluded zero (only one
run had a positive point estimate, effectively zero), so none passed the
primary endpoint on development data.

Exploratory readings:

1. **LD is the robust configuration in this grid.** Longest-event ordering +
   least-represented allocation + reduced-size Risk-Off entries returned 42.6%,
   42.1%, 40.5%, and 37.8% as the lower bound moved from one to four weeks. It
   beat SPY in every window, ranked at the 71st–88th control percentiles, and
   held drawdown to 6.9%–7.8%.
2. **The single maximum is fragile.** SDB at 3–5 weeks returned 43.7% with 5.7%
   drawdown, but the same configuration returned only 30.6%, 30.6%, and 31.1%
   in the adjacent sensitivity windows. It should not be selected merely for
   being the maximum of 32 development variants.
3. **The 4–5 week-only window loses breadth.** Mean weekly report size fell
   from 40.9 names at 1–5 weeks to 13.2 at 4–5 weeks, and most configurations
   deteriorated. The three-to-four-week lead region appears important, but this
   is a development observation rather than a causal attribution.
4. Risk-Off blocking remains interaction-heavy: it helped several 3–5 week
   portfolios but was generally harmful in the narrow 4–5 week window. The
   unblocked LD result is notably more stable across window definitions.

All manifests captured the requested event bounds, selection order, allocation
mode, no-stop exit, Risk-Off setting, $4,000 sizing, and 25-position cap. All
required artifacts were present. No configuration is frozen by this grid.

**Development winner before position-size sensitivity (recorded 2026-07-18):**
the 2–5
week **LD** configuration — longest-event-first selection,
least-represented-sector allocation, reduced-size Risk-Off/Unknown entries
allowed, and no protective stop. It returned 42.1%, ranked at the 88th
percentile of its controls, had 7.0% maximum drawdown and 7.9% annualized
volatility, and produced a 6.04 return-to-drawdown ratio. It is preferred over
the corresponding blocked configuration because LD had higher return, shallower
drawdown, lower volatility, and materially better stability across adjacent
event windows. This designation was provisional until the position-size
sensitivity test below; the holdout remained untouched.

**Position-size sensitivity experiment (2026-07-18).** Repeat the full 32-run
event-window grid at (a) $5,000 base nominal with 20 concurrent positions and
(b) $2,500 base nominal with 40 concurrent positions. Both preserve $100,000
maximum initial Risk-On entry capacity. `min_position_nominal` is reduced from
$1,000 to $500 for both grids so the $2,500 configuration's $750 Risk-Off
target remains eligible rather than silently duplicating Risk-Off blocking.
Development-only position-size/slot overrides and nested output grouping are
used so the two grids can run concurrently without mutating their shared base
configuration. This is further exploratory multiple-testing evidence. At the
time the experiment was designed, the current winner remained provisional and
the holdout remained untouched. Results follow.

## Position-size sensitivity results — recorded 2026-07-18

All 64 requested development runs completed: 32 at $5,000 base nominal / 20
positions and 32 at $2,500 / 40 positions. Every run used no stop, a $500
minimum position nominal, and 100 random controls. All manifests matched the
requested sizing, event window, event ordering, allocation order, and Risk-Off
entry setting; all 320 required artifacts were present and their recorded trade
hashes verified. No holdout run or artifact was created.

Each cell below is `total return / control percentile / maximum drawdown`. The
configuration codes have the same meanings as in the event-window grid above.

### $5,000 nominal / 20 positions

| Config | 1–5 weeks | 2–5 weeks | 3–5 weeks | 4–5 weeks |
|---|---:|---:|---:|---:|
| SR | +41.4% / 40 / −8.9% | +40.6% / 49 / −9.4% | +40.4% / 42 / −8.6% | +38.0% / 35 / −9.8% |
| LR | +45.3% / 65 / −10.1% | +42.9% / 62 / −8.6% | +48.3% / 84 / −8.0% | **+43.6% / 72 / −6.9%** |
| SD | +42.8% / 58 / −8.5% | **+46.3% / 78 / −9.4%** | +35.1% / 26 / −10.3% | +33.0% / 16 / −9.8% |
| LD | **+45.9% / 74 / −9.4%** | +44.8% / 76 / −8.4% | +47.3% / 96 / −7.9% | +39.4% / 59 / −8.2% |
| SRB | +35.9% / 24 / −9.4% | +33.5% / 12 / −8.5% | +45.8% / 45 / −6.8% | +34.3% / 12 / −10.8% |
| LRB | +45.9% / 88 / −8.9% | +44.0% / 75 / −8.3% | +47.4% / 62 / −7.9% | +37.2% / 29 / −10.5% |
| SDB | +37.2% / 33 / −9.3% | +37.4% / 38 / −8.4% | +43.0% / 43 / −6.8% | +33.1% / 10 / −10.2% |
| LDB | +42.9% / 72 / −11.0% | +44.9% / 87 / −7.6% | **+48.5% / 79 / −7.2%** | +38.8% / 47 / −8.8% |

### $2,500 nominal / 40 positions

| Config | 1–5 weeks | 2–5 weeks | 3–5 weeks | 4–5 weeks |
|---|---:|---:|---:|---:|
| SR | +21.2% / 6 / −5.0% | +24.9% / 52 / −5.3% | +19.8% / 3 / −4.2% | +19.9% / 16 / −5.5% |
| LR | **+24.8% / 23 / −5.5%** | **+26.0% / 74 / −4.9%** | +23.5% / 54 / −5.5% | +19.8% / 11 / −5.3% |
| SD | +21.2% / 6 / −5.0% | +24.9% / 52 / −5.3% | +19.8% / 3 / −4.2% | **+19.9% / 16 / −5.5%** |
| LD | +24.8% / 23 / −5.5% | +26.0% / 74 / −4.9% | +23.5% / 54 / −5.5% | +19.8% / 11 / −5.3% |
| SRB | +19.3% / 2 / −5.1% | +20.1% / 15 / −4.5% | +22.7% / 11 / −4.3% | +19.1% / 15 / −7.3% |
| LRB | +22.7% / 29 / −6.2% | +22.8% / 53 / −6.0% | **+24.0% / 34 / −5.9%** | +18.5% / 7 / −7.4% |
| SDB | +19.3% / 2 / −5.1% | +20.1% / 15 / −4.5% | +22.7% / 11 / −4.3% | +19.1% / 15 / −7.3% |
| LDB | +22.7% / 29 / −6.2% | +22.8% / 53 / −6.0% | +24.0% / 34 / −5.9% | +18.5% / 7 / −7.4% |

The prior 2–5 week LD winner compares across sizing as follows:

| Base nominal / cap | Return | Ctrl pctile | Max DD | Ann. vol | Return/DD | Trades |
|---|---:|---:|---:|---:|---:|---:|
| $2,500 / 40 | +26.00% | 74 | −4.91% | 6.11% | 5.30 | 804 |
| **$4,000 / 25** | **+42.14%** | **88** | **−6.98%** | **7.91%** | **6.04** | 691 |
| $5,000 / 20 | +44.75% | 76 | −8.41% | 9.10% | 5.32 | 595 |

Exploratory readings:

1. **$5,000 / 20 produced the strongest absolute returns.** Across the four
   windows, LR was especially robust: 45.3%, 42.9%, 48.3%, and 43.6% (45.1%
   mean; 42.9% minimum). The highest individual cell was 3–5 week LDB at
   48.5%, but selecting that maximum from 64 new tests would be inappropriate.
2. **$4,000 / 25 remains the best risk-adjusted setting for the existing 2–5
   week LD winner.** Moving to $5,000 added 2.61 percentage points of return,
   but maximum drawdown increased by 1.43 points, volatility rose by 1.19
   points, return-to-drawdown fell from 6.04 to 5.32, and control percentile
   fell from 88 to 76. The position-size test therefore does not automatically
   displace the recorded provisional winner.
3. **$2,500 / 40 did not translate the nominal 40-slot capacity into actual
   deployment.** For 2–5 week LD it averaged 15.4 open positions, reached at
   most 33, and averaged only about $28,600 of entry nominal open. The $4,000
   and $5,000 versions averaged about $39,200 and $42,600 respectively and
   reached their 25- and 20-position caps. The three-per-sector cap, candidate
   availability, and ticker/event locks prevented the smaller orders from
   being offset by enough additional positions. Consequently every $2,500
   result lagged SPY's 35.9% return, despite shallower drawdowns.
4. At $2,500 / 40, ranked and least-represented allocation produced the same
   rounded portfolio outcomes in every matched pair, although their trade
   files were not byte-identical. With the total slot cap never binding, the
   allocation-order choice had little portfolio-level effect in this grid.
5. No stationary-bootstrap daily-excess CI95 excluded zero in either new grid.
   The risk results remain secondary development diagnostics and cannot rescue
   failure of the primary excess-return endpoint.

Artifacts are under
`strategy_study/position_{5000,2500}/min_{1,2,3,4}_weeks/development_<tag>/`.
The explicit sizing/configuration decision and final freeze follow. The
holdout remains untouched.

## Final holdout configuration freeze — recorded 2026-07-18

Development selection is concluded. The following configuration is now the
authoritative, frozen setup for the one-shot holdout. It is written into
`config/scan.yaml` and `config/backtest.yaml`; the holdout must run without any
development-only CLI override.

| Component | Frozen value |
|---|---|
| Eligible price | $10–$300 |
| Predicted-event window | 2–5 weeks |
| Candidate selection | `days_to_event` descending (longest event first); score bands off |
| Portfolio allocation | `least_represented_sector` |
| Starting equity | $100,000 |
| Base position nominal | $4,000, multiplied by the regime size factor |
| Position limits | 25 total; 3 per sector; $500 minimum nominal |
| Risk-Off/Unknown entries | Allowed at reduced size; not blocked |
| Entry execution | Next-session limit-on-open at decision close +3%; exact decision/entry bars required; no retry or replacement |
| Exit | Predicted T-1 close; no protective stop; 70-session safety cap |
| Costs | 10 bps per side |

This is the **2–5 week LD, $4,000 / 25-position configuration with a 3%
limit-on-open**. Its final development verification returned +43.28%, ranked
at the 93rd percentile of its random controls, with −6.62% maximum drawdown,
7.69% annualized volatility, and a 6.53 return-to-drawdown ratio. SPY returned
+35.94%, with −24.50% drawdown and 18.30% volatility over the same sessions.

### Decision rationale and retained inferences

1. **LD was the most stable event/allocation/Risk-Off combination at the
   selected sizing.** Across the 1–5, 2–5, 3–5, and 4–5 week sensitivity
   windows it returned 42.57%, 42.14%, 40.54%, and 37.82%, beat SPY in every
   window, and limited drawdown to 6.92%–7.80%. The 2–5 window combined the
   highest control percentile (88) with a shallower drawdown than 1–5 and 3–5,
   while retaining enough breadth that the 4–5 window lost.
2. **Reduced-size Risk-Off entries remain allowed.** The corresponding 2–5
   week LDB portfolio returned 40.3% with 7.6% drawdown, versus LD's 42.1%
   with 7.0% drawdown. LD also had lower volatility and was materially more
   stable across adjacent event-window definitions. Risk-Off blocking showed
   strong path-dependent interactions elsewhere and was not chosen.
3. **Least-represented-sector allocation is retained for diversification.** It
   prevents a ranked candidate list clustered by sector from consuming the
   available slots before other industries are considered, while preserving
   rank within each sector and the hard three-per-sector cap.
4. **$4,000 / 25 was preferred on the like-for-like position-size grid.** Under
   the earlier unconditional next-open execution shared by that grid, the
   2–5 week $5,000 / 20 LD version earned more (+44.75%), but had worse maximum
   drawdown (−8.41%), volatility (9.10%), return-to-drawdown (5.32), and
   control percentile (76) than $4,000 / 25 LD. The $5,000 variants have not
   been rerun with the later 3% limit-on-open rule, so they are retained as
   historical sizing evidence rather than a current apples-to-apples result.
5. **Deferred research note — $5,000 / 20 merits future reconsideration, not a
   second frozen choice.** At that sizing, LR (longest event first + ranked
   allocation, Risk-Off entries allowed) was the most robust absolute-return
   configuration across event windows: 45.3%, 42.9%, 48.3%, and 43.6%. This is
   interesting evidence that the allocation interaction changes with larger
   positions/fewer slots, but its weaker risk/control evidence did not change
   the sizing decision. Revisit it, including the new entry rule, only in a
   separately designed future study or with genuinely fresh prospective
   data—not by reacting to the one-shot holdout.
6. **$2,500 / 40 is not selected.** The strategy could not use the nominal
   capacity: 2–5 week LD averaged 15.4 open positions and never reached 40,
   leaving much more cash idle. Every $2,500 result lagged SPY despite its
   shallower drawdown.
7. **The development evidence is promising but not statistically conclusive.**
   No tested daily-excess bootstrap CI95 excluded zero. The selected portfolio
   beat SPY in terminal return and had a much better risk profile, but the
   claim of repeatable excess return remains unproven until the untouched
   holdout is run. Secondary drawdown, volatility, and return-to-drawdown
   measures remain important, but cannot rescue a failed primary endpoint.

At the moment of this freeze, no holdout command had been run and no holdout
artifact existed.

### Entry execution amendment verification

After the sizing/configuration decision but before holdout, the entry policy
was amended from an unconditional next-session market-on-open fill to the
causal 3% limit-on-open rule above. The selected configuration was first rerun
on development as `development_ld_loo3`; no alternative buffer was searched.

| Metric | Previous next-open fill | 3% limit-on-open |
|---|---:|---:|
| Return | +42.14% | **+43.28%** |
| Control percentile | 88 | **93** |
| Trades | 691 | 687 |
| Opening-limit rejections | — | 10 |
| Maximum drawdown | −6.98% | **−6.62%** |
| Annualized volatility | 7.91% | **7.69%** |
| Return / drawdown | 6.04 | **6.53** |

The rerun had 183 decisions and 100 controls (control median $137,880). Every
one of the 687 fills satisfied `entry_price <= decision_close × 1.03`; the
largest accepted opening gap was 2.97%. No candidate lacked an exact decision
or entry-session bar, and rejected orders did not create replacements. The
daily-excess CI95 remained inconclusive at [−0.000478, +0.000504], so this
execution improvement does not establish statistical significance. The
development result supports retaining and re-freezing the simple 3% rule; it
does not authorize further buffer optimization.

### Event-window sensitivity with the 3% entry rule

After the selected 2–5 verification, the same frozen LD configuration and 3%
limit rule were rerun for the requested 1–5, 3–5, and 4–5 windows. This varies
only the lower event bound; it does not search another buffer, sizing,
allocation, regime policy, or exit. Each run used 183 development decisions
and 100 controls. All artifacts and trade hashes verified, and every fill was
at or below its recorded opening limit.

| Window | Return | Change vs old entry | Ctrl pctile | Max DD | Ann. vol | Return/DD | Trades | Limit rejects |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1–5 | +43.11% | +0.54 pp | 76 | −7.52% | 7.91% | 5.73 | 763 | 10 |
| **2–5** | **+43.28%** | **+1.13 pp** | **93** | **−6.62%** | 7.69% | 6.53 | 687 | 10 |
| 3–5 | +40.78% | +0.24 pp | 75 | −7.19% | 7.27% | 5.68 | 610 | 10 |
| 4–5 | +37.59% | −0.23 pp | 63 | **−5.53%** | **6.81%** | **6.79** | 548 | 9 |

SPY remained +35.94% with −24.50% drawdown, 18.30% volatility, and 1.47
return-to-drawdown. The 3% cap improved return in the 1–5, 2–5, and 3–5
windows and reduced drawdown in all four. It slightly reduced 4–5 return while
materially improving that window's drawdown.

**Inference:** 2–5 remains the frozen choice and is now the strongest overall
window under the actual entry policy. It has the highest return, by 0.16 point
over 1–5; the highest control percentile, by 17 points; and shallower drawdown
and lower volatility than 1–5. The 4–5 window has the best pure
return-to-drawdown ratio because its drawdown is exceptionally small, but its
37.59% return barely exceeds SPY, its control percentile is only 63, and its
mean weekly report contains only 13.2 names. It does not displace 2–5.

Opening-limit rejection was small and stable at 9–10 orders per run. No ticker
lacked an exact decision-session or next-session bar. All four daily-excess
CI95 intervals still span zero, so the wider sensitivity confirms robustness
of the development outcome—not statistical proof of repeatable excess return.
Artifacts for the added runs are under
`strategy_study/loo3_window_sensitivity/min_{1,3,4}_weeks/development_ld_loo3/`.

## One-shot holdout result — recorded 2026-07-18

The holdout was run exactly once after the final configuration was committed.
The command used only `--split holdout --confirm-holdout`, with no development
override. The manifest records commit
`80906cbd290fcd73476c5dd87c0fe3ad7f0ecb0e`, `git_dirty=false`, the complete
frozen YAML, 65 weekly decisions, 281 completed trades, and 100 random
controls. All five required artifacts and the trade hash verified. Every fill
was at or below its recorded 3% opening limit; 18 opening orders were rejected.

Artifacts: `data/backtest/pre_earnings_momentum/strategy_study/holdout/`.

### Primary endpoint — failed

| Measure | Portfolio | SPY | Required reading |
|---|---:|---:|---|
| Terminal equity | $122,416 | $149,174 | Portfolio must finish above SPY: **failed** |
| Total return | +22.42% | +49.17% | Terminal excess = **−26.76 pp** |
| Mean daily excess vs SPY | −0.000647 | — | Must be positive with CI95 above zero |
| Stationary-bootstrap CI95 | [−0.001337, +0.000041] | — | Includes zero and is predominantly negative: **failed** |

The frozen primary hypothesis therefore **failed**. The simulation did not
show positive terminal excess and did not produce a daily-excess confidence
interval wholly above zero. This result must not be described as a validated
return edge. Per section 11, development-period numeric performance is now a
historical research observation rather than a deployable performance claim.

### Important secondary risk profile

| Measure | Portfolio | SPY | Reading |
|---|---:|---:|---|
| Maximum drawdown | **−6.31%** | −8.88% | Portfolio was 2.57 pp shallower |
| Annualized volatility | **10.92%** | 16.65% | Portfolio was 5.73 pp lower |
| Return / drawdown | 3.55 | **5.54** | SPY was better because of its much higher return |

The lower drawdown and volatility are real desirable secondary outcomes, but
they do not rescue the failed primary endpoint. The risk-adjusted result is
mixed: the strategy was smoother, while SPY produced substantially more return
per unit of maximum drawdown.

### Frozen diagnostics — explanatory, not a pass

1. The portfolio ended at the **85th percentile** of its random-eligible
   controls. Their median terminal equity was $117,745 (range $109,364 to
   $129,580). Deterministic longest-event/least-represented selection therefore
   performed better than most random choices inside the same gated category,
   but neither the selected portfolio nor any control approached SPY's
   $149,174 terminal value.
2. Individual trades were not obviously poor: 60.14% were profitable, mean
   net trade return was +4.06%, median was +2.31%, and mean matched-period
   excess versus SPY was +1.09% (median +0.20%; 51.25% positive). This suggests
   the picks added some value while actually held.
3. **Capital deployment was the dominant shortfall.** The portfolio averaged
   16.2 open positions but only 47.0% marked capital exposure; it was below 50%
   invested on 180 of 322 sessions. Average marked exposure was about $54,800.
   A fully invested SPY gained 49.17%, so strong per-trade results could not
   overcome the strategy's cash drag and regime-scaled sizing in this rally.
   This is a diagnostic inference, not authorization to alter sizing after
   seeing the holdout.
4. Risk-Off trades were strong in this path (61 trades, +12.96% mean net and
   +4.43% mean matched-SPY excess) but were sized at only $1,200. Risk-On trades
   dominated count (209) and averaged +1.34% net. These regime splits are
   path-dependent diagnostics and cannot justify tuning on the spent holdout.
5. The 3% opening rule was not the material cause of underperformance: only 18
   orders were rejected, and the largest accepted opening gap was 2.91%.

### Post-holdout discipline

The 2025-04-04 through 2026-06-26 window is now spent and must never be reused
as an untouched holdout. Do not change deployment, regime sizing, entry rules,
or ranking and then call another run on this window validation. Any revised
strategy requires a new pre-registration and genuinely fresh prospective data
(or an explicitly labeled exploratory reuse that carries no holdout claim).
The honest conclusion is: **the frozen strategy failed to beat SPY in the
one-shot holdout, despite lower drawdown/volatility and encouraging trade-level
and random-control diagnostics.**

## Amendment log

- 2026-07-18: Added `--atr-stop-mult` / `--tag` development-only overrides to
  the runner for the pre-registered exit-policy counterfactual (holdout
  refuses overrides). No frozen-section changes.
- 2026-07-18: Development results recorded (section above). Holdout not run.
- 2026-07-18: Added post-freeze reading notes (control construction, stop-width
  empirics) to the Development results section and expanded the companion
  explainer, now stored as `EXPLAINER.md`. Frozen sections 1–11 and every
  recorded number are unchanged.
- 2026-07-18: Added `--select-by` (score / days_to_event / days_to_event_short)
  and `--block-risk-off-entries` development-only runner overrides plus a regime
  entry-gate (`study.regime_entry_block`); recorded the score-free candidate
  exploration (A/B) in the Development results. Holdout not run; no
  frozen-section changes. NOTE: selection-mode / entry-gate are dev-only
  overrides today — to FREEZE config A they must be written into `scan.yaml`
  (`selection: {order: days_to_event, use_bands: false}`) since the holdout
  refuses CLI overrides.
- 2026-07-18: Recorded the completed A-short/B-short development results and
  their byte-identical verification reruns. No holdout configuration was
  frozen, no runtime configuration changed, and the holdout was not run.
- 2026-07-18: Corrected the portfolio's pending-order cash reservation to
  enforce the pre-registered no-leverage rule, including entry costs. Marked
  all earlier portfolio results as pre-fix development observations requiring
  rerun before configuration freeze. Holdout not run.
- 2026-07-18: Added configurable `ranked` versus
  `least_represented_sector` portfolio allocation and a development-only CLI
  override. No allocation experiment was run, no mode was selected for freeze,
  and the holdout was not run.
- 2026-07-18: Changed the pending development configuration's eligible price
  range from $7–$150 to $10–$300 and updated its reporting buckets. No
  simulation was run, no range was selected for freeze, and the holdout was not
  run.
- 2026-07-18: Changed the pending development portfolio to $100,000 starting
  capital, fixed $4,000 base nominal per stock (regime-scaled), and at most 25
  concurrent positions. No simulation was run, this sizing was not selected
  for freeze, and the holdout was not run.
- 2026-07-18: Ran and recorded the eight-member post-fix development grid across
  event ordering, sector allocation, and Risk-Off entry blocking, all without a
  stop and with 100-seed controls. No configuration was frozen and the holdout
  was not run.
- 2026-07-18: Added mandatory portfolio-versus-SPY maximum drawdown, annualized
  volatility, and return-to-drawdown reporting as an important secondary
  holdout criterion. It is explicitly nice-to-have and cannot override the
  primary excess-return endpoint. Holdout not run.
- 2026-07-18: Added development-only event-window and output-group overrides for
  the requested 32-run one-to-five-week sensitivity grid. The grid is labeled
  exploratory because of multiple testing. Holdout not run.
- 2026-07-18: Completed and recorded all 32 event-window sensitivity runs under
  `min_1_weeks` through `min_4_weeks`, including controls and risk metrics. No
  configuration was frozen and the holdout was not run.
- 2026-07-18: Designated 2–5 week LD (longest-event,
  least-represented-sector, reduced-size Risk-Off entries allowed, no stop) as
  the provisional current development winner. A further test remains pending;
  this is not a freeze and the holdout was not run.
- 2026-07-18: Lowered `min_position_nominal` from $1,000 to $500 and added
  development-only fixed-nominal/max-position overrides plus safe nested output
  grouping for the requested $5,000×20 versus $2,500×40 sensitivity grids. No
  new result was yet selected and the holdout was not run.
- 2026-07-18: Completed and recorded both 32-run position-size grids (64
  development runs total), validated all manifests/artifacts, and compared them
  with the $4,000×25 baseline. The provisional winner was not silently changed;
  no configuration was frozen and the holdout was not run.
- 2026-07-18: Concluded development selection and froze the one-shot holdout
  configuration as 2–5 week LD with $4,000 base nominal, 25 positions,
  least-represented-sector allocation, reduced-size Risk-Off/Unknown entries
  allowed, and no stop. Updated both shared YAML files and the binding sections
  of this specification. Recorded $5,000/20 LD and robust LR as deferred
  research inferences only. No holdout was run.
- 2026-07-18: Before holdout, amended entry execution to a 3% limit-on-open
  based on an exact decision-session close, with immediate cancellation and no
  retry/replacement when the next open exceeds the limit. Reran only the
  selected 2–5 week LD $4,000×25 configuration on development, recorded its
  improved +43.28% return / 6.62% drawdown / 93rd control percentile, and
  re-froze the configuration. No alternate buffer was searched and no holdout
  was run.
- 2026-07-18: Reran 1–5, 3–5, and 4–5 week LD under the same 3%
  limit-on-open policy and compared them with the selected 2–5 result. The
  sensitivity confirmed 2–5 as the strongest overall window; no other policy
  parameter changed, no alternate buffer was searched, and no holdout was run.
- 2026-07-18: Ran the committed frozen holdout exactly once at commit
  `80906cbd`, recorded the failed primary endpoint (+22.42% portfolio versus
  +49.17% SPY; daily-excess CI95 spanning zero), and reported the better
  drawdown/volatility plus deployment and control diagnostics without treating
  them as a pass. The holdout window is now spent and cannot validate a retry.
