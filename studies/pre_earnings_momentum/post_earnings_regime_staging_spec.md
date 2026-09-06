# Study 6: weekly SPXL/SPY allocation

Status: owner-approved frozen implementation specification (2026-09-05).
Approval authorizes implementation and synthetic verification only. Historical
2010-2025 execution still requires a separate explicit authorization.

Family: `pre-earnings-weekly-regime-staging-v1`.
Evidence: `NO_VERDICT / EXPLORATORY`, retrospective 2010–2025.

The owner selected Risk-On-only individual-stock entries and accepted the
terminal execution-date boundary. The rules below are binding for Study 6.

## 1. Question and comparisons

Does putting residual capital in SPXL during Risk-On, and SPY otherwise, improve
the return/drawdown tradeoff of Study 5? Does individual-stock selection add
value relative to applying that ETF rule to the entire portfolio?

| Series | Individual stocks | Residual capital | Evidence source |
|---|---|---|---|
| H: historical Study 5 context | Existing frozen Risk-On rules | SPY | Existing artifacts, read-only; inputs have since drifted |
| A: `stocks-spy-staging-control` | Risk-On entries; inherited held-stock exits | SPY | New independent Study 6 control chain |
| B: `stocks-regime-staging` | Risk-On entries; inherited held-stock exits | SPXL in Risk-On, SPY otherwise | New independent Study 6 treatment chain |
| C: `etf-only` | None | SPXL in Risk-On, SPY otherwise | New independent Study 6 mechanism chain |
| Study 6 passive SPY | None | One SPY purchase held throughout | New common benchmark from the Study 6 input snapshot |

Run A, B and C as new historical chains after separate authorization. Each
starts independently with $50,000 and no imported position state. H is the saved
`study5-risk-on-2010-2025-9c6af9f` series. Do not replay, alter or import H's
checkpoints. Each new chain's equity, affordability and subsequent orders must
evolve from its own fills. Baseline/all-regime stock entries are out of scope.

Compare B minus A for the effect of the changed ETF allocation policy under one
frozen input snapshot, and B minus C for the total effect of adding the stock
strategy. These are portfolio comparisons, not a claim that different realized
exposures isolate stock alpha. Use the new common passive-SPY ledger for market
context. H remains descriptive context and must be labelled non-comparable for
causal attribution because its price inputs differ. Do not select a winning rule
and retune it after observing these results.

## 2. Regime and weekly clock

Use SPY to classify the regime, never SPXL:

| Cutoff classification | Rule | ETF destination | New stocks in B |
|---|---|---|---|
| Risk-On | SPY close above SMA50; SMA50 above its value five sessions earlier | SPXL | Allowed, subject to all inherited gates |
| Neutral | SPY close above SMA50; SMA50 not rising | SPY | Blocked |
| Risk-Off | SPY close at or below SMA50 | SPY | Blocked |
| Unknown | Insufficient usable indicator history | SPY | Blocked |

Unknown is not permission to trade using a missing ETF opening price. Missing
required ETF prices are an input failure as specified below.

For each ISO week, execution is the final actual NYSE session. The information
cutoff is its immediately prior exchange session. Evaluate once immediately
before execution opens, using evidence through that cutoff close only. Normally
this is Thursday-close evidence and Friday-open fills; holiday weeks use the
validated exchange calendar. A daily regime change does not trigger a trade.

Freeze the regime, target ETF, held-stock exit signals, candidate ranks, stock
quantities, limits and cash reservations before opening prices are accessed.
Only fills and the predefined funding formulas may depend on those opens.
Daily closes mark equity and supply later history; they create no extra orders.
The ETF destination remains selected until the next weekly decision, even if
the daily descriptive regime changes in between.

## 3. Origin, annual boundaries and terminal date

- Maintain $50,000 cash from the first 2010 session until the common first
  execution, 2010-01-08. This matches H and its passive-SPY inception.
- Carry A, B and C continuously through independent annual checkpoints to the
  final 2025 NYSE close. Never reset capital at January 1.
- For intermediate years, allow a prior-year cutoff to create an order for
  the following year's in-window weekly open. Preserve it once in the checkpoint
  and execute it exactly once after restoration.
- For Study 6 only, admit decisions by execution date: the final
  2025-12-31 cutoff targeting 2026-01-02 is outside the window. Record a boundary
  exclusion, but create no decision orders or terminal pending batch for it.
- Do not liquidate actual holdings at the terminal close. Report both close-marked
  equity and a separately calculated hypothetical net-liquidation value.

H retains its 835 recorded snapshots, including the final out-of-window pending
batch; A/B/C
would have 834 in-window weekly decisions. H has no 2026 fills. This difference
must be disclosed and must never be "corrected" in H's preserved evidence.

## 4. Stock rules and opening execution

A and B inherit the Study 5 Risk-On config's selection, event forecasting, ranking,
sector cap, $1,000–$5,000 equal-allocation targets, whole-share sizing, 103% entry
limits, pins, capital-scaled drawdown and post-event floor/T+7 conditions. Do not
scale the $5,000 cap with equity or loosen gates to increase stock exposure.
Exclude SPY and SPXL from individual-stock candidates and stock sector occupancy.
ETF positions do not consume individual-stock slots. A regime change alone
does not force a stock exit.

At the cutoff, use cash plus the held ETF's cutoff market value plus inherited
estimated scheduled stock-sale proceeds as deployable capital. Use the inherited
allocator to freeze whole-stock quantities and reservations. The changed ETF
value can change B's later stock allocation; this is part of the frozen method.
A always selects SPY as its ETF destination; otherwise it uses the same execution
and accounting path as B so B minus A isolates the destination rule.

At the weekly open, execute in this order:

1. Fill planned stock exits at the open. A missing stock open cancels that order,
   retains the position and clears its pending-exit state. Reevaluate only at the
   next weekly cutoff; never retry on Monday or latch the canceled trigger.
2. Cancel stock entries with missing opens or opens above their frozen limits.
   If a failed stock exit leaves a sector occupied, cancel the lowest-ranked
   planned entries needed to preserve the three-position cap. Do not substitute
   candidates or recompute signals. Log these deterministic capacity cancellations.
3. If the ETF destination changed, sell the entire old ETF position at its open.
   If it did not change, sell only enough whole ETF shares to fund the accepted
   stock-entry reservations after actual stock-sale proceeds are known:
   `min(held_shares, ceil(max(0, reservations - cash) / (ETF_open - per_share_fee)))`.
4. Fill accepted stocks in frozen rank order. Apply the inherited affordability
   reduction/cancellation to the lowest rank if funds are insufficient. Never
   exceed planned quantities, borrow, add substitute symbols or retry later.
5. Buy the maximum affordable whole shares of the destination ETF with residual
   cash: `floor(cash / (destination_open + per_share_fee))`. Retain the remainder.

C uses the identical ETF switching and residual-buy formulas, with no stock
orders. It holds its ETF position between switches; it does not sell and rebuy
unchanged holdings every week. No account-level daily leverage rebalancing is
added. SPXL's internal daily reset is already reflected in its observed prices.

Validate positive net sale proceeds and sufficient cash before arithmetic.
Required SPY/SPXL opening and valuation data must be complete before admitting
a year. Missing/invalid required ETF data stops the year before simulation;
unexpected failures stop the chain and preserve evidence. Do not invent fills,
silently skip sessions, replace SPXL with synthetic 3x SPY, or fall back to a
different asset because a price is missing.

Same-open proceeds funding same-open purchases is a simulation convention,
not evidence of executable live order sequencing or opening-auction liquidity.

## 5. Prices, costs and data readiness

Use actual local SPXL and SPY histories through the canonical validated price
reader. SPXL began on 2008-11-05 and targets 300% of the S&P 500's daily return,
not three times its multi-year return. Its realized price path includes fund
expenses, financing effects and tracking differences; do not subtract a current
expense ratio again. Source: [Direxion fund description](https://www.direxion.com/product/daily-sp-500-bull-bear-3x-etfs),
reviewed 2026-09-05.

Inherit the repository's [price-cache convention](../../docs/DATA.md): adjusted
OHLC, one consistent adjustment vintage, and two-decimal stored precision.
Do not add dividend payments or split share adjustments again to already-adjusted
prices. Whole shares and $0.0008 per-share fees on adjusted prices are an inherited
simulation convention, not a reconstruction of literal historical broker shares.
Disclose its consequences for fee realism and price rounding, especially for
early SPXL history. Do not switch to another price format within this study.

Charge $0.0008 for every filled stock, SPY or SPXL share on either side, zero for
canceled shares. No additional slippage, taxes, cash interest or borrowing.
These omissions limit interpretation; they are not permission to claim live
returns. Report entry fees separately from hypothetical liquidation fees.

Read-only cache inspection on 2026-09-05 found complete SPXL coverage: all
4,024 NYSE sessions from 2010 through 2025, with no missing or extra exchange
dates and no price-reader validation issues. The 2009 warm-up partition has all
252 NYSE sessions and also passes validation. Every inspected cached row keeps
the repository convention that `adjClose` mirrors `close`.

A separate input comparison found that the current cache does not reproduce H's
saved input hashes. Depending on year, roughly 90–135 overlapping symbol
histories differ, and 2021–2024 have 30–37 newly available histories. The core
universe, retired-symbol and earnings hashes still agree in sampled years. The
2025 SPY manifest hash also differs because its stored calendar frame includes
later 2026 data. Therefore B minus H cannot be described as an SPXL-only policy
effect. This is why A and a new passive-SPY benchmark are required. Preserve a
complete machine-readable drift report before execution.

Before any historical run:

- Revalidate the frozen SPY/SPXL snapshot against an independent NYSE calendar;
  the current readiness check passes for all 4,024 in-window sessions.
- Pin all configs, dependencies, calendars and input hashes. Record the observed
  differences from H without restoring an old cache or modifying H. Verify that
  A, B, C and the new passive-SPY benchmark consume one identical frozen input
  snapshot wherever their required inputs overlap.
- Keep historical execution local and network-free. Hash predecessor artifact
  trees and published catalog files before and after the new work.

## 6. Outputs, isolation and execution guards

Use a dedicated Study 6 config per new variant, a guarded wrapper, and an ETF-aware
engine/state contract. Reuse pure calendar, stock-signal and allocation helpers
where safe; do not repurpose the old `spy_shares` field to mean SPXL or change
Study 3/4/5 checkpoint schemas. No FastAPI, Angular or catalog work is needed.

Output root: `backtest/pre_earnings_momentum/weekly_regime_staging/`, with
separate `stocks_spy_staging_control`, `stocks_regime_staging` and `etf_only`
year/tag subdirectories.

Require dedicated Study 6 confirmation, a clean explicitly pinned implementation
commit, origin 2010 and years 2010–2025 at the shared execution boundary as well
as the wrapper. Git failures must fail closed. Continuations must verify the
immediately previous checkpoint's output hash, year, Study 6 variant, config and
implementation identity against its manifest before loading market inputs.
Reject predecessor Study 3/4/5 checkpoints and output paths under their roots.
Refuse existing output destinations; never overwrite a failed or completed run.

Persist complete annual logs, per-week snapshots, held-stock evaluations,
candidate gates/ranks, frozen stock orders, target ETF and regime inputs,
funding calculations, ETF switch reasons, fills/cancellations, daily equity,
costs, end positions and variant-local checkpoints. Include authorization,
commit, source hashes and boundary exclusions in manifests.

Maintain a zero-cost shadow for each new portfolio, copying actual order/fill
quantities rather than independently reallocating its saved fees. Verify daily
holdings/order identity and cash differences against accumulated costs.

Read H only in the comparison/reporting stage. Generate the Study 6 passive-SPY
ledger from the new common input snapshot. Study 3/4/5 specs, configs, artifacts
and published numbers remain untouched. No historical run is implied by
implementation or by a passing test suite.

## 7. Predefined reporting and interpretation

Report A, B, C and the Study 6 passive-SPY benchmark together, identifying source
commit, input snapshot and price conventions. Report H separately as historical
context with its input-drift warning. Show raw close equity and net-liquidation
equity for each; never mix a portfolio close mark with a liquidation-marked
benchmark without disclosure. Calculate comparable metrics on both bases. Derive
any H comparison mark from saved positions without writing back to its artifacts.

Required outputs:

- Terminal return, excess return, 16-year CAGR and calendar-year returns.
- Continuous maximum drawdown with peak/trough dates, recovery time in sessions
  and calendar days, and explicit unrecovered-at-end status.
- Daily simple-return sample volatility annualized by square root of 252,
  Calmar, worst day/week/month/year, and dates of major losses. Use the same
  full-period session grid and initial cash interval for all portfolios.
- Gross filled principal turnover for stocks, SPY and SPXL, buy/sell sides,
  costs, completed stock trades, primary and overlapping exit reasons,
  unfilled entries and late post-event exits with sessions late.
- Annual/monthly/daily-average stock, SPY, SPXL and cash allocations in dollars
  and portfolio percentages, ranges, and sessions with no stocks.
- Weekly regime counts, ETF switches, switch-related turnover, and allocation
  conditional on the last executable weekly regime. Keep daily descriptive
  regimes separate to avoid suggesting trades happened before their cutoffs.
- An ETF exposure proxy `(SPY_value + 3 * SPXL_value) / equity`. Label it a
  nominal daily ETF target proxy, not measured total portfolio beta; report
  individual-stock market value separately.
- Predefined annual risk detail for 2020 and 2022, alongside every other year,
  and the share of performance attributable by ledger to stocks and each ETF.
  Attribute actual marked P/L and fees, not an assumed 3x SPY annual return.

There is no performance PASS threshold. Higher CAGR with worse drawdowns is a
tradeoff, not automatic success. Report all comparisons and limitations even if
unfavorable. A 2010–2025 run is post-selection exploratory evidence, includes
already-observed years, and cannot reopen Study 3's holdout or validate an edge.

## 8. Implementation acceptance tests before historical authorization

Use synthetic fixtures and mocked controls only:

1. Exact Risk-On/Neutral/Risk-Off/Unknown destination and stock-entry rules;
   equal-price and flat-SMA boundaries; SPXL cannot influence SPY regime.
2. Final-session exchange calendar, holiday weeks, intermediate Dec/Jan carry,
   one snapshot per execution, and terminal 2026 exclusion.
3. Execution-day OHLC/volume mutations leave frozen decisions unchanged; open
   changes affect only predetermined fills/funding. No daily regime trades.
4. ETF switching in both directions, unchanged ETF retention, stock-sale
   funding, frozen rank/limit/quantity, insufficient-cash reduction and residual
   rounding. C never creates stock orders.
5. Missing stock opens produce terminal cancellations, no sticky retry, correct
   next-week reevaluation and preserved sector capacity. Missing required ETF
   input aborts before simulation. No negative cash or account borrowing.
6. Every cost and the zero-cost shadow reconcile; uninterrupted synthetic
   multi-year execution matches checkpoint-restored execution exactly.
7. Missing confirmation, dirty/failed Git status, invalid years/origin, wrong
   variant, altered checkpoint/config, revision drift and protected output
   paths are rejected before historical loading or writes.
8. A and B produce identical stock decisions when supplied identical portfolio
   state and ETF values; their only configured difference is the ETF destination
   policy. A, B, C and passive SPY share one pinned input snapshot.
9. Existing Study 3/4/5 synthetic regression behavior remains intact, and
   reference hashes remain unchanged. Run the full utilities suite, docs
   checks, secret scan and diff check for implementation acceptance.

The approved implementation freezes this spec and its configs before any
historical data execution. Historical run authorization is a distinct step.

## Appendix: why Study 5 held so much SPY in 2022

Read-only reconstruction of the saved Risk-On `daily_equity.csv`, `decisions.csv`
and `orders.csv` for 2022 gives:

- 251 sessions; mean stock allocation 6.5023%, SPY 93.4329%, cash 0.0648%.
- Stock allocation ranged from 0% to 37.8040%; no stocks on 122 sessions.
- 12 Risk-On weekly decisions, 35 Risk-Off and 5 Neutral. The other 40 weeks
  prohibited new stock entries; existing stocks still followed their exit rules.
- 43 filled stock buys and 53 filled stock exits, including carried positions.
- Mean stock position count 3.92; the unchanged $5,000 per-position ceiling
  limits the portfolio percentage represented by each new position as equity grows.

| Month | Mean stocks | Mean SPY |
|---|---:|---:|
| January | 25.62% | 74.31% |
| February | 5.39% | 94.53% |
| March | 0.00% | 99.97% |
| April | 25.55% | 74.40% |
| May | 5.99% | 93.94% |
| June | 0.00% | 99.92% |
| July | 0.17% | 99.75% |
| August | 7.44% | 92.48% |
| September | 0.99% | 98.94% |
| October | 0.00% | 99.95% |
| November | 2.76% | 97.17% |
| December | 6.06% | 93.87% |

This is a daily-average allocation, not a constant 7% stock target. Risk-Off
blocks new stocks but leaves substantial index exposure. Even the all-regime
baseline averaged only 14.33% stocks in 2022, so the regime gate is not the sole
constraint; stock eligibility, holding exits and dollar sizing also matter.
