# Pre-earnings momentum, study 2 — SPY cash sweep (DRAFT, not yet frozen)

**Status: all decisions resolved 2026-07-18; adopted as an EXPLORATORY local
study.** The sweep rules in section 4 are fixed and implementation must match
them. Runs cover the study-1 dates only. Every result is exploratory by
construction — the dates were mined by study 1 and the sweep was designed
after observing them — so no run under this mode can validate the strategy or
support an edge claim. A prospective (or PIT-historical) holdout remains
possible later via a dated amendment. Prepared 2026-07-18.

**Predecessor:** `backtest_spec.md` (study 1). Its one-shot holdout failed the
primary endpoint (+22.4% vs SPY +49.2%); its 2025-04-04..2026-06-26 window is
**spent** and can never validate anything again. Study 2 is a new
pre-registration, not a retry.

## Outcome at a glance — exploratory spent-window replay completed

**Result: descriptive only; no validation weight.** Over the study-1 spent
window (decisions 2025-04-04..2026-06-26), the fixed SPY-sweep replay returned
**+53.16%** versus **SPY +49.17%** (**+3.98 percentage points**), across 281
completed trades. Mean matched-SPY excess per trade was **+1.09%**. The
reported mean daily excess versus SPY was **+0.0094%** (CI95 **−0.0313% to
+0.0496%**), which crosses zero. These results were produced after study 1
observed this window and after the sweep was designed, so they are
exploratory—not evidence of an edge or a successful holdout.

## 1. Motivation and hypothesis

Study 1's holdout decomposed cleanly: average capital exposure was 47%, and a
zero-skill model (0.47 × SPY's +49.17% ≈ +23.1%) predicted the portfolio's
+22.4% within 0.7 points. Meanwhile the trades themselves were mildly good
while held:

| Split | Trades | Mean matched-SPY excess per trade | σ per trade | Naive 2SE |
|---|---:|---:|---:|---:|
| Development (frozen config) | 687 | +1.28% | 9.19% | ±0.70% |
| Holdout | 281 | +1.09% | 12.53% | ±1.49% |

The development figure is selection-tainted (champion of ~116 variants) and
the holdout figure alone is not significant, but both point the same
direction: **the picks earn a small positive excess during their holding
windows; the portfolio loses to SPY because half the account sits in
zero-yield cash.**

**Hypothesis of study 2:** sweeping all idle cash into SPY converts that
per-trade excess into portfolio-level excess over SPY, because the benchmark
exposure gap disappears and the stock sleeve's alpha (if real) is the only
remaining difference.

This study also improves the measuring instrument: with roughly half the
portfolio literally holding SPY, the daily-excess noise falls by about half
(σ ≈ 1.0% → ≈ 0.5%/day), and the registered primary endpoint moves to the
trade level, where power is adequate (section 8).

## 2. Claim scope

CAN estimate: whether the frozen study-1 pipeline plus a mechanical SPY cash
sweep earns excess return over SPY, prospectively, on live data — free of
survivorship bias for the prospective period.

CANNOT claim: anything from re-running the spent 2025-04..2026-06 window
(exploratory only); performance of alternative sweep destinations (T-bills,
regime-dependent) — those are explicitly out of scope and would need their own
pre-registration; downside protection — the sweep removes the cash cushion by
design, so drawdowns comparable to SPY's are expected and accepted.

## 3. Inherited configuration (frozen, unchanged from study 1)

Everything in the study-1 final freeze carries over verbatim: $10–$300 price
range, 2–5 week predicted-event window, naive anniversary date forecaster with
consistency gate, days-to-event-descending selection with score bands off,
least-represented-sector allocation, $4,000 base nominal × regime size factor,
25 positions / 3 per sector / $500 dust floor, reduced-size Risk-Off entries
allowed, 3% limit-on-open entry with no retry, predicted T-1 close exit, no
protective stop, 70-session safety cap, 10 bps per side stock costs, $100,000
initial equity, weekly Friday decisions.

**No parameter above may be searched, tuned, or varied in this study.** The
only new machinery is the sweep, fixed in section 4 before any run.

## 4. Sweep rules (Option 1: sweep at each sale — frozen upon freeze)

1. **Sweep-in (cash → SPY).** Any cash freed during session `s` — planned-exit
   proceeds, forced-exit proceeds, rejected-entry releases — is used to buy
   SPY at session `s`'s **closing** price. This is causal: planned exits are
   scheduled orders known before the close; forced early-report exits are
   known from the realized report date before that session's close; entry
   rejections are known at the open. Exception: cash reserved under rule 3
   is not swept. The sweep is always its own separate SPY order in that
   session's closing auction — never netted into or contingent on the stock
   order — and pays its own SPY-side cost (rule 6).
2. **Whole shares.** SPY is bought in whole shares; the sub-one-share residue
   (≲ one SPY share) remains cash. No sweep executes if the sweepable amount
   is below $500 (mirrors the dust floor).
3. **Friday netting (no pointless round trips).** At each Friday decision,
   the nominal plus entry costs of all scheduled Monday orders is reserved.
   The Friday close sweep applies only to cash above the reservation.
4. **Sweep-out (SPY → cash).** If reserved needs exceed free cash at the
   Friday decision, a SPY sale for exactly the shortfall (plus SPY-side cost)
   is scheduled at **Monday's opening** price, alongside the entry orders.
   If Monday entry rejections then leave excess cash, it re-sweeps at
   Monday's close (rule 1).
5. **SPY sleeve is plumbing, not a pick.** It is exempt from the position
   count, sector caps, gates, locks, event windows, and scoring. It is marked
   daily like any position and participates fully in the equity curve.
6. **SPY transaction cost: 5 bps per side** (user-set 2026-07-18, decision
   D1; deliberately conservative versus SPY's ~0.2 bp half-spread, and half
   the 10 bps stock assumption).
7. **Regime throttle consequence (acknowledged, accepted).** The regime size
   factor still scales stock-sleeve nominals, but freed capacity now rides in
   SPY rather than cash. The portfolio is therefore ~fully invested in
   equities at all times, including Risk-Off periods. Study 1's shallow
   drawdowns will not recur; this is the deliberate trade.

## 5. Portfolio simulation

Identical to study 1 (cash reservation for pending orders, no leverage, daily
close marks) plus the sweep rules above. The no-leverage check must account
for scheduled Monday SPY sells as a funding source for that Monday's entries
only.

## 6. Benchmarks

1. **SPY buy-and-hold** — now a like-for-like comparison (both sides ~fully
   invested), which is the point of the study.
2. **Matched-dates SPY excess per trade** — unchanged; feeds the primary
   endpoint.
3. **Random-eligible controls (100 seeds) with the identical sweep** —
   controls must sweep too, so the real-vs-control comparison isolates
   within-pool picking, not deployment.
4. **Descriptive only:** the study-1 frozen no-sweep configuration run over
   the same sessions, to display the sweep's contribution. Carries no
   endpoint weight.

## 7. Splits and discipline (exploratory mode, resolved 2026-07-18)

- **Development replay: decisions 2021-07-02 .. 2024-12-27.** One run of the
  inherited configuration + sweep. Verifies implementation (cash never
  negative; sweeps at recorded prices; sleeve accounting exact) and records
  the mechanical effect of the sweep on the mined window.
- **Spent-window replay: decisions 2025-04-04 .. 2026-06-26.** One run.
- Both results carry the label "exploratory replay — no validation weight"
  in every table that reports them. **No variant runs, no sweep-rule
  tuning** — one run per window, as specified, or the exploratory results
  lose even their descriptive value to selection.
- Study-1 artifacts under `strategy_study/` are read-only for this study;
  study-2 outputs are written under a separate `strategy_study/sweep/`
  grouping and must not overwrite any study-1 directory.
- **Prospective holdout: deferred.** Starting one later requires a dated
  amendment naming the first decision Friday and the horizon before any
  prospective decision is logged.

**Reproduction.** The sweep is an opt-in runner mode (`--sweep`, default SPY
cost 5 bps via `--spy-cost-bps`); with it off the runner is byte-identical to
study 1. Outputs land under `strategy_study/sweep/<split>/`.

```
./commands.sh backtest earnings --split development --sweep
./commands.sh backtest earnings --split holdout --sweep --confirm-holdout
```

## 8. Endpoints and power (computed before any run)

- **Primary (single hypothesis): mean matched-SPY excess per trade > 0**,
  with a ticker-clustered stationary bootstrap (10,000 draws) CI95 excluding
  zero, over the prospective holdout's completed trades.
  - Power arithmetic, from measured study-1 dispersion (σ ≈ 12.5% per trade,
    holdout trade rate ≈ 4.3/week): 24 months ≈ 450 trades → naive
    2SE ≈ ±1.18%; ticker clustering (holdout: 281 trades over 145 tickers)
    inflates this by roughly √1.5–√2, so the detectable mean is ≈ 1.4–1.7%.
    The observed study-1 means (+1.1% to +1.3%) sit just below that; 36
    months ≈ 670 trades brings the detectable mean to ≈ 1.1–1.4%. Decision
    D2 chooses the horizon with this table in view — the effect being hunted
    is at the edge of a 24-month instrument.
- **Secondary (report, cannot rescue the primary):** portfolio mean daily
  excess vs SPY with block-bootstrap CI95 (acknowledged detection floor even
  post-sweep: ≈ ±6%/yr over 24 months); terminal excess vs SPY; drawdown,
  volatility, and return-to-drawdown vs SPY (parity expected, reported for
  honesty, desirable directions not claimed in advance).
- **Diagnostics (exploratory, labeled):** sweep-attributable return (SPY
  sleeve P&L vs a zero-yield-cash counterfactual), sleeve exposure
  distribution, SPY churn and cost totals, control percentile, forecaster
  miss incidence.

## 9. Result language

A positive prospective outcome is reported as: "positive per-trade excess over
SPY in a fully invested live-data test of the frozen pipeline" — with the
naive-forecaster and classification-map caveats carried forward. A negative
outcome retires the pipeline's performance claims (the sweep mechanism itself
— owning SPY instead of cash — needs no defense; what fails is the claim that
the picks add anything on top of it). The spent-window replay is never quoted
as validation in any outcome.

## 10. Decisions before freeze (user)

- **D1 — SPY cost: RESOLVED 2026-07-18.** 5 bps per side (user-set; written
  into sweep rule 6).
- **D2 — prospective horizon: RESOLVED 2026-07-18.** No prospective holdout
  for now; PIT historical data also declined on cost. The study runs as an
  exploratory local backtest on the study-1 dates, with the explicit
  understanding that no result validates the strategy (section 7). The
  trade-level power analysis in section 8 is retained for whenever a
  prospective run is amended in.
- **D3 — spent-window replay: RESOLVED 2026-07-18.** Yes — replayed once,
  exploratory-labeled.
- **D4 — live execution match: RESOLVED 2026-07-18.** User confirmed Option 1
  live behavior: every stock sale is accompanied by a same-session SPY sweep,
  placed as a separate order (written into sweep rule 1).

## Amendment log

- 2026-07-18: D1 (5 bps SPY cost) and D4 (Option 1, sweep as separate
  same-session order) resolved and written into section 4.
- 2026-07-18: D2 and D3 resolved — prospective holdout and PIT historical
  data both declined; study adopted as an exploratory local backtest over the
  study-1 development and spent windows, one run each, no validation claims.
  Sweep rules of section 4 fixed as the implementation contract. No run has
  occurred as of this entry.
