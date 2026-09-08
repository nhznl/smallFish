# Study 7: weekly GLD Risk-Off staging

Status: owner-directed design specification (2026-09-08). This task authorizes
design only. Implementation, synthetic verification, historical execution, and
publication are separate work and are out of scope for the design agent.

Family: `pre-earnings-weekly-defensive-regime-staging-v1`.
Evidence: `NO_VERDICT / EXPLORATORY`, retrospective 2010–2025.

This study is a new experiment derived from the published
[Study 6 specification](post_earnings_regime_staging_spec.md) and result, plus a
subsequent read-only defensive-asset screen. It does not amend, replay, or
reinterpret Study 6. The rules below must be frozen before historical execution.

## 1. Frozen research question

Does replacing SPY with GLD only during Study 6's weekly Risk-Off allocation
improve the return/drawdown tradeoff of the stock-plus-SPXL/SPY portfolio, while
leaving the regime classifier, individual-stock rules, weekly clock, execution
mechanics, and costs unchanged?

The primary comparison is D minus C. It changes only the Risk-Off ETF
destination at the policy level:

- C uses SPY in Risk-Off.
- D uses GLD in Risk-Off.

Because every arm evolves from its own fills, the ETF change can alter later
cash, stock quantities, affordability, and compounding. D minus C therefore
measures the total portfolio effect of the Risk-Off destination policy. It is
not a static GLD-minus-SPY sleeve calculation.

The study has no confirmatory holdout and no performance `PASS` threshold. It
must report `NO_VERDICT / EXPLORATORY` regardless of outcome. The historical
period and GLD selection were already observed before this specification.

## 2. Arms and comparisons

All arms begin independently with $50,000, share one frozen Study 7 input
snapshot, and use the same session grid. No arm imports capital, positions,
orders, pins, or checkpoints from Study 5 or Study 6.

| Arm | Stable identifier | Individual stocks | Investable ETF destination |
|---|---|---|---|
| A | `passive-spy` | None | One initial SPY purchase held throughout |
| B | `stocks-spy-control` | Risk-On entries; inherited held-stock exits | SPY in every regime |
| C | `stocks-spxl-spy-control` | Same rules as B | SPXL in Risk-On; SPY otherwise |
| D | `stocks-spxl-gld-treatment` | Same rules as B and C | SPXL in Risk-On; GLD in Risk-Off; SPY in Neutral or Unknown |
| E | `etf-only-spxl-gld` | None | Entire investable portfolio follows D's ETF rule |

Although C is behaviorally identical to Study 6's stock-plus-SPXL/SPY
treatment, it must be a new independent Study 7 control chain. The saved Study
6 chain may be shown only as historical context because using it as C would
allow implementation or input drift to contaminate D minus C.

Predefined comparisons:

1. **D minus C — primary:** total portfolio effect of replacing Risk-Off SPY
   with GLD.
2. **C minus B — mechanism context:** total portfolio effect of replacing
   Risk-On SPY with SPXL.
3. **D minus E — stock-sleeve context:** total effect of adding the inherited
   stock strategy to D's ETF policy; this is not pure stock alpha because the
   portfolio paths and later ETF quantities differ.
4. **B, C, D, and E versus A — market context:** active portfolio outcomes
   versus one passive SPY purchase under the same price snapshot and costs.

Do not choose a different comparison after observing the result. Report all
arms and all predefined comparisons even when they are unfavorable.

## 3. Selection disclosure and excluded hypotheses

GLD was selected after examining Study 6's 2010–2025 exact weekly defensive
holding intervals. In that descriptive screen, compounded Risk-Off-window
price returns were SPY +168.19%, GLD +129.29%, BIL +5.25%, and SPXS -98.91%.
GLD was chosen as a fixed candidate that retained materially more historical
return than cash-like alternatives while showing lower within-window downside
than SPY. Those observations are hypothesis-generating evidence, not a validated
GLD advantage.

The following are outside Study 7:

- selecting a different defensive asset by calendar year or episode;
- ranking defensive assets from trailing performance;
- changing allocations based on a Risk-Off streak length;
- using SPXS, another inverse ETF, or a leveraged defensive sleeve;
- splitting or scaling the ETF sleeve by confidence, volatility, or drawdown;
- changing the SPY regime definition or decision frequency;
- tuning the stock rules, allocation caps, costs, or dates after results appear.

The separate read-only persistence diagnosis did not support an aggressive
inverse sleeve. It is not an arm, sensitivity, or secondary analysis in this
study.

## 4. Regime and causal weekly clock

Use SPY alone to classify the market regime. SPXL and GLD must never influence
the regime:

| Cutoff classification | Frozen SPY rule | B destination | C destination | D/E destination | New stocks in B/C/D |
|---|---|---|---|---|---|
| Risk-On | SPY close above SMA50 and SMA50 above its value five sessions earlier | SPY | SPXL | SPXL | Allowed, subject to inherited gates |
| Neutral | SPY close above SMA50 and SMA50 not rising | SPY | SPY | SPY | Blocked |
| Risk-Off | SPY close at or below SMA50 | SPY | SPY | GLD | Blocked |
| Unknown | Insufficient usable indicator history | SPY | SPY | SPY | Blocked |

For every ISO week:

- `execution_session` is the final actual NYSE session.
- `information_cutoff_session` is the immediately preceding exchange session.
- The complete decision is made immediately before the execution session opens,
  using evidence available through the cutoff close.

Normally the study classifies at Thursday's close and trades at Friday's open.
Exchange holidays move both dates according to the validated NYSE calendar.
There are no daily regime trades.

Before any execution-session price is accessed, freeze the regime, ETF
destination, held-stock exits, candidate gates and ranks, planned stock
quantities, entry limits, and cash reservations. Execution opens may affect
only predefined fill eligibility, affordability, and ETF funding formulas.
Daily closes mark equity and become later historical evidence; they do not
create orders. The selected ETF remains held until a later weekly execution
changes it or stock funding requires a partial sale.

Persist the last executable weekly regime across annual checkpoints. Do not
reset its reporting label to `UNKNOWN` at January 1 when a position and regime
carry from the prior year. Keep the daily descriptive regime separate from the
last executable weekly regime in every output.

## 5. Origin, annual boundaries, and terminal date

- Hold each arm's $50,000 in cash from the first 2010 session through the common
  first execution on 2010-01-08.
- At that open, A makes its single maximum-affordable whole-share SPY purchase.
  B, C, D, and E execute their first weekly plans independently.
- Carry every arm continuously through independent annual checkpoints. Never
  reset capital, positions, pins, realized P/L, cost basis, or peak equity on
  January 1.
- Allow an intermediate-year cutoff to create an order for the next year's
  in-window execution. Preserve and execute that order exactly once after
  checkpoint restoration.
- Admit decisions by execution date. The 2025-12-31 cutoff targeting the
  out-of-window 2026-01-02 open must be recorded as a boundary exclusion and
  must create no order or terminal pending batch.
- Mark final holdings at the last 2025 adjusted close. Do not force liquidation.
  Separately calculate hypothetical terminal net-liquidation value using the
  frozen per-share exit cost.

Each active weekly arm must contain 834 in-window weekly decisions if the
validated NYSE calendar matches Study 6. A calendar difference is an input or
implementation failure that must be resolved before execution, not silently
accepted.

## 6. Inherited individual-stock rules

B, C, and D inherit Study 6's complete Risk-On stock contract without retuning:

- `BULLISH_CONTINUATION` and penalized `setupScore > 50`;
- causally forecast earnings events two to five weeks away;
- $10–$500 entry prices and the existing liquidity gates;
- three open-plus-pending individual-stock positions per sector;
- $1,000–$5,000 equal-allocation targets and whole shares;
- 103% frozen entry limits and the 30-calendar-day pin;
- capital-scaled drawdown, post-event floor, and maximum T+7 exit conditions;
- weekly pre-open selection and Risk-On-only new entries.

Do not scale the $5,000 maximum with portfolio equity, relax gates to increase
stock exposure, or add baseline/all-regime entries. A regime change alone never
forces an existing stock exit. Existing stocks may remain in Neutral or
Risk-Off until an inherited exit condition is present at a weekly cutoff.

Exclude SPY, SPXL, and GLD from individual-stock candidates, stock-position
counts, and sector occupancy. ETF positions do not consume individual-stock
slots. E never evaluates or creates individual-stock candidates, positions,
orders, or fills.

## 7. Weekly opening execution

At the cutoff, calculate deployable capital from cash, the held ETF's cutoff
market value, and inherited estimated proceeds from scheduled stock exits. Use
the inherited allocator to freeze stock quantities and maximum reservations.

At the weekly execution open, B, C, and D execute in this order:

1. Fill planned stock exits at the open. A missing stock open cancels that
   order, retains the position, clears pending-exit state, and waits for the
   next weekly evaluation.
2. Cancel stock entries with missing opens or opens above frozen limits. If a
   failed stock exit leaves a sector occupied, cancel the lowest-ranked planned
   entries needed to retain the three-position cap. Do not substitute a stock
   or recompute a decision.
3. If the ETF destination changed, sell the entire previous ETF position at its
   open. If it did not change, sell only enough whole ETF shares to fund accepted
   stock reservations after actual stock-sale proceeds are known:
   `min(held_shares, ceil(max(0, reservations - cash) /
   (held_ETF_open - per_share_fee)))`.
4. Fill accepted stocks at their opens in frozen rank order. Apply the inherited
   deterministic lowest-rank reduction or cancellation if cash is insufficient.
   Never exceed a planned quantity, borrow, substitute, or retry later.
5. Buy the maximum affordable whole shares of the destination ETF with residual
   cash: `floor(cash / (destination_open + per_share_fee))`. Retain the
   remainder as cash.

E applies the same full-switch, unchanged-holding, maximum-affordable residual
purchase, whole-share, and cash-remainder rules without stock activity. It does
not sell and repurchase an unchanged ETF each week. A makes no trade after its
initial SPY purchase.

Validate positive net sale proceeds and sufficient cash before arithmetic.
Same-open proceeds funding same-open purchases is a simulation convention, not
evidence of executable live sequencing or opening-auction liquidity.

## 8. Prices, costs, and input readiness

Use actual local SPY, SPXL, and GLD histories through the canonical validated
price reader. Inherit the repository's [adjusted-price convention](../../docs/DATA.md):
adjusted OHLC, one adjustment vintage, two-decimal stored precision, and no
duplicate dividends or split adjustments.

SPXL's observed path already contains its daily reset, expenses, financing,
tracking differences, and compounding. Never synthesize it as three times SPY
or subtract a current expense ratio again. Treat GLD as its own observed asset;
do not synthesize gold returns from spot prices or futures.

Charge $0.0008 for every filled stock, SPY, SPXL, or GLD share on either side,
and zero for canceled shares. Charge A's initial SPY purchase and hypothetical
terminal liquidation consistently. Add no spread, slippage, taxes, cash
interest, borrowing, or portfolio-level daily leverage rebalance. Disclose
these omissions and the implications of per-share costs on adjusted historical
prices.

Before historical execution:

1. Validate complete 2009 warm-up and 2010–2025 in-window SPY, SPXL, and GLD
   session coverage against an independently validated NYSE calendar.
2. Reject missing, duplicate, inconsistent, non-positive, stale, or otherwise
   invalid required ETF opens or valuation closes before simulating an affected
   year. Do not forward-fill, synthesize, skip a session, or fall back to another
   ETF.
3. Pin configs, implementation commit, dependency versions, exchange calendar,
   universe, retired symbols, earnings inputs, and every consumed price hash.
4. Make every arm manifest carry the complete common Study 7 snapshot identity,
   including SPY, SPXL, and GLD, even when an arm does not trade every asset.
5. Confirm that B, C, D, and E share identical relevant inputs. Generate A from
   the same SPY snapshot.
6. Keep execution local and network-free. Input acquisition or cache repair is
   separate work and must precede the frozen run.

## 9. Isolation, artifacts, and execution guard

Use dedicated Study 7 configs, wrapper, engine/state identity, output root, and
confirmation flag. Reuse pure Study 6 calendar, signal, allocation, and
accounting helpers where safe, but do not modify Study 6 configs, schemas,
manifests, checkpoints, or artifacts.

Freeze these implementation identities before historical execution:

| Arm | Study ID | Output variant directory |
|---|---|---|
| A | `pre-earnings-weekly-defensive-regime-staging-passive-spy-v1` | `passive_spy` |
| B | `pre-earnings-weekly-defensive-regime-staging-stocks-spy-v1` | `stocks_spy_control` |
| C | `pre-earnings-weekly-defensive-regime-staging-stocks-spxl-spy-v1` | `stocks_spxl_spy_control` |
| D | `pre-earnings-weekly-defensive-regime-staging-stocks-spxl-gld-v1` | `stocks_spxl_gld_treatment` |
| E | `pre-earnings-weekly-defensive-regime-staging-etf-only-spxl-gld-v1` | `etf_only_spxl_gld` |

Use one pinned config per arm under `studies/pre_earnings_momentum/config/`, a
guarded `post_earnings_defensive_regime_staging.py` wrapper, an isolated
`post_earnings_defensive_regime_staging_engine.py`, and a separate comparison
command. The output root is
`backtest/pre_earnings_momentum/weekly_defensive_regime_staging/`, with separate
variant/year/run-ID directories. Do not write Study 7 output beneath Study 5 or
Study 6 roots. The historical confirmation flag is
`--confirm-weekly-defensive-regime-staging-exploratory-run`.

The guarded historical runner must require all of the following before loading
historical inputs or writing output:

- a dedicated Study 7 confirmation flag;
- a clean, explicitly pinned implementation commit;
- origin year 2010 and an execution year in 2010–2025;
- the exact frozen config for the requested A–E variant;
- the immediately preceding same-variant Study 7 checkpoint for continuation;
- verified predecessor manifest and output hashes;
- a new, nonexistent destination.

Reject Study 3/4/5/6 checkpoints, wrong-arm checkpoints, altered configs,
implementation drift, dirty or indeterminate Git state, invalid year order,
and protected or existing destinations. A comparison command must admit only
complete, independently originated A–E chains with the same snapshot identity.

A is a dedicated arm and artifact chain, not a benchmark recalculated separately
inside B–E. It records its one initial fill, daily cash and SPY marks, annual
checkpoints, accumulated cost, and hypothetical terminal liquidation. It does
not create weekly strategy decisions.

Persist annual per-week decisions, held-stock evaluations, candidate gates and
ranks, frozen orders, funding calculations, ETF switch reasons, fills and
cancellations, daily equity, executable and descriptive regimes, costs,
positions, checkpoints, input hashes, authorization evidence, and boundary
exclusions. Maintain a zero-cost shadow for B–E by copying actual quantities
rather than reallocating saved fees.

Hash the complete saved Study 6 artifact tree and published study catalog before
and after implementation and historical execution. Any change is a failure.
Study 6 remains `NO_VERDICT / EXPLORATORY` with its published numbers unchanged.

No FastAPI, Angular, catalog, or materialized-study change is part of initial
implementation or execution. Publication requires complete validated results
and a separate owner decision.

## 10. Predefined reporting and interpretation

Use one daily session grid and report close-marked equity and comparable
hypothetical net-liquidation equity separately. Never compare a close-marked
arm with a liquidation-marked benchmark.

For A–E and every predefined pair, report:

- terminal net-liquidation value and return, CAGR, and every calendar-year
  return;
- continuous maximum drawdown with peak, trough, recovery date, recovery
  sessions and calendar days, and unrecovered-at-end status;
- annualized volatility from daily simple returns using square root of 252,
  Calmar, worst day, Friday-ended week, month, and year;
- mean return of the worst 5% of daily observations, plus the ten worst daily
  returns with dates and the executable weekly regime;
- gross filled-principal turnover and costs by stocks, SPY, SPXL, and GLD,
  split into buy, sell, ETF-switch, and stock-funding activity;
- completed stock trades, primary and overlapping exits, unfilled entries,
  capacity cancellations, and late post-event exits;
- daily-average and annual/monthly allocation dollars and percentages for
  stocks, SPY, SPXL, GLD, and cash, plus ranges and sessions without stocks;
- weekly regime counts, ETF switches, switch reversals within one and two weekly
  executions, and allocation conditional on the last executable weekly regime;
- ledger P/L after filled fees for stocks, SPY, SPXL, and GLD, both overall and
  by executable regime, with overnight P/L assigned to the outgoing regime and
  execution-open-to-close P/L assigned to the incoming regime;
- a nominal S&P ETF exposure proxy `(SPY_value + 3 * SPXL_value) / equity`,
  explicitly excluding GLD and individual stocks and explicitly not described
  as measured portfolio beta;
- detailed paths for 2011, 2015, 2018, 2020, and 2022, selected before execution,
  alongside the complete unfiltered 2010–2025 result.

Also report the exact dates and adjusted-price returns of SPY and GLD during D's
executable Risk-Off holding intervals. Keep these interval returns separate
from ledger attribution and whole-portfolio results.

Classify the primary D-versus-C result mechanically:

- **return dominance:** D has at least as high terminal net-liquidation value
  and CAGR as C;
- **drawdown dominance:** D has a no-more-negative maximum drawdown than C;
- **strict portfolio dominance:** both conditions hold and at least one metric
  is strictly better;
- **risk-adjusted improvement:** D has a higher Calmar ratio than C;
- **tradeoff:** return and downside comparisons disagree.

These labels summarize the historical tradeoff. None creates a `PASSED` verdict
or validates an edge. Report exact differences and limitations rather than
choosing a preferred arm from a single aggregate score.

## 11. Interpretation constraints

- The static present-day stock universe creates survivorship and availability
  bias.
- Daily-bar opening fills omit spread, market impact, opening-auction liquidity,
  taxes, and live order sequencing.
- Same-open sale proceeds are assumed available for purchases.
- Adjusted historical prices and per-share costs do not reconstruct literal
  historical brokerage share counts.
- SPXL is a daily-reset leveraged product; long-period results are path-dependent.
- GLD selection followed inspection of Study 6 and 2010–2025 defensive windows.
- The 2010–2025 result cannot provide new out-of-sample confirmation, cannot
  reopen a spent holdout, and cannot replace any published study conclusion.
- A future causal confirmation requires a separate study identity and evidence
  unavailable when this design was frozen.

Present every result as historical research evidence, never as a prediction or
investment recommendation.

## 12. Synthetic implementation acceptance tests

Historical data must not be used for implementation acceptance. Use synthetic
fixtures, fake loaders, and mocked Git controls to verify:

1. Exact A–E regime destinations, Risk-On-only stock entries, and inherited
   stock exits; SPXL and GLD cannot influence regime classification.
2. Equal-price and flat-SMA boundaries, Unknown behavior, holiday weeks, one
   decision per final-session open, and the terminal 2026 exclusion.
3. Execution-session OHLCV mutations cannot change frozen signals, ranks,
   quantities, limits, or destinations. Open changes may affect only predefined
   fills, affordability, and funding.
4. Every D transition among SPXL, GLD, and SPY; unchanged-ETF retention; partial
   funding sales; maximum-affordable residual buys; and whole-share rounding.
5. A buys SPY once and never trades again. E never creates stock decisions or
   orders.
6. B, C, and D produce identical stock decisions when given identical portfolio
   state and inputs. Their configured policy differences are limited to ETF
   destinations; later path-dependent quantity divergence is allowed and must
   remain internally consistent.
7. Missing stock opens cancel only through inherited rules. Missing required
   ETF prices abort before simulation. No fallback, negative cash, or borrowing
   is permitted.
8. Annual checkpoint restoration exactly matches uninterrupted multi-year
   execution, including positions, cash, cost basis, peak equity, pins, pending
   orders, and last executable weekly regime.
9. Costs, ledger P/L, liquidation marks, and zero-cost shadows reconcile. Shadow
   and costed arms retain identical quantities and order/fill identities.
10. D-minus-C attribution assigns pre-open movement to the outgoing ETF/regime
    and same-session movement after the open to the incoming ETF/regime.
11. Missing confirmation, dirty or failed Git status, invalid origin/year,
    altered config/checkpoint, implementation drift, cross-arm continuation,
    protected paths, and existing destinations fail before historical loading
    or writes.
12. The comparison rejects an incomplete arm, mismatched session grid, different
    snapshot identity, broken annual chain, non-independent origin, or modified
    artifact hash.
13. Existing Study 3/4/5/6 synthetic regression behavior and saved artifact
    hashes remain unchanged. Run the complete utilities suite, documentation
    check, secret scan, and diff check before implementation handoff.

Passing these tests proves only that an implementation follows this design. It
does not authorize or validate the historical result.

## 13. Historical-run stop rules

The execution agent must stop without substituting judgment when:

- the implementation commit or worktree is not exactly admissible;
- a required ETF session, opening price, or valuation price is unavailable;
- common input hashes or session grids differ across arms;
- an annual predecessor or manifest cannot be verified;
- output already exists;
- any Study 6 artifact or published catalog hash changes;
- a result cannot be reconciled to orders, holdings, cash, and costs.

Preserve failure evidence in a new unprotected diagnostic location only when
the guarded implementation explicitly supports atomic failure reporting.
Never repair, overwrite, or rerun a completed historical chain in place.
