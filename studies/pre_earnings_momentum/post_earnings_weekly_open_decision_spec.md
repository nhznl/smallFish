# Post-earnings single weekly pre-open decision (Study 5)

**Study family:** `pre-earnings-post-event-weekly-open-decision-v1`

**Status:** protocol, implementation, synthetic verification, and the
retrospective 2010-2025 exploratory run were authorized by the owner on
2026-09-04. Historical execution is delegated to a separate agent and is out
of scope for the implementation agent.

## 1. Question and evidence boundary

Study 5 asks what happens when the Study 4 portfolio is managed only once per
week: immediately before the week's final NYSE session opens. At that time it
computes held-stock exits, new candidates, stock quantities, SPY funding, and
the residual-SPY instruction, then executes the batch at that same session's
open.

This is a new method, not a correction or rerun of Study 4. Study 4's weekday
scans, sticky exits, frozen configs, artifacts, published numbers, and
exploratory status remain unchanged.

The proposed 2010-2025 interval is already observed, including Study 3's spent
holdout and Study 4's historical extension. Study 5 can therefore provide only
**retrospective exploratory evidence**. It has no confirmatory endpoint and
must publish `NO_VERDICT / EXPLORATORY`; it cannot validate an edge, replace a
published result, or reopen any spent holdout.

## 2. Variants and inherited rules

Run two independent equal-allocation portfolios from $50,000 in 2010 through
2025, with state carried continuously across annual checkpoints:

1. `baseline`: eligible entries are allowed in every market regime.
2. `risk-on`: eligible entries are allowed only when the information cutoff
   classifies SPY as `RISK_ON`.

Except for the decision cadence defined here, inherit Study 4's frozen low-fee
post-earnings rules without retuning: `BULLISH_CONTINUATION`, penalized
`setupScore > 50`, causally forecast events two to five weeks away, $10-$500
prices, the existing liquidity gates, three open-plus-pending positions per
sector, $1,000-$5,000 equal-allocation targets, whole shares, the 30-calendar-
day pin, capital-scaled drawdown, the post-event floor and maximum T+7 hold,
SPY cash staging, and `$0.0008 x filled shares` on every stock and SPY side.
The passive SPY benchmark uses the identical per-share cost.

The variants differ only in the market-regime entry gate. Existing Study 4
configuration files are inputs for comparison, not files that Study 5 may
modify or reuse as its own identity.

## 3. Calendar and causal clock

For each ISO week:

- `execution_session` is the final available session in the validated SPY/NYSE
  calendar. It is normally Friday. If Friday is closed, it is normally
  Thursday; this is an exchange-calendar rule, not generic weekday arithmetic.
- `information_cutoff_session` is the immediately preceding NYSE session.
- `decision_time` is immediately before the regular-market open of
  `execution_session`, after the cutoff data has been validated.

Thus a normal week uses evidence through Thursday's completed close and trades
at Friday's open. A Good Friday week uses evidence through Wednesday's
completed close and trades at Thursday's open.

The decision may use only information that was available by the cutoff close:
completed price/volume bars, earnings-calendar history, portfolio state, pins,
and the SPY regime. No open, high, low, close, volume, fill, or quote from the
execution session may influence eligibility, exit signals, ranking, planned
stock quantities, or entry limits. Opening prices may influence only whether a
frozen order fills and the deterministic cash-funding mechanics in Section 5.

Historical artifacts must record both dates rather than calling the cutoff
session the execution date or implying that the execution-session open was
already known.

## 4. Exactly one weekly evaluation

Study 5 performs no Monday-through-Thursday candidate scan, provisional
allocation, held-position exit check, or sticky exit latch. Intermediate
sessions remain ordinary historical inputs for indicators and event-state
reconstruction, but they do not produce decisions.

At `decision_time`, from one immutable cutoff snapshot:

1. Reconstruct each held position's earnings lifecycle through the cutoff. The
   post-event anchor remains the earliest eligible completed session on or
   after the realized event, with that session's close; it must not move to the
   cutoff merely because this is the first weekly evaluation after the event.
2. Evaluate the held position at the cutoff close for the inherited bearish-
   trend, capital-scaled drawdown, post-event-floor, and post-event-maximum
   conditions. A condition that appeared and recovered on an intermediate
   weekday is not sticky and does not trigger an exit.
3. Remove scheduled exits from sector occupancy, revalidate the current
   universe, apply the variant's regime gate, rank the surviving candidates,
   and allocate from the real portfolio state.
4. Freeze the stock exit list, ranked entry list, planned whole-share
   quantities, 103% limits, maximum cash reservations, and the SPY funding and
   residual formulas before the execution-session opening prices are used.

The maximum T+7 rule remains a maximum-condition test, but because exits are
checked once weekly its fill may occur after the nominal target. The report
must preserve the existing late-exit flag and quantify the delay. The
execution-session close marks equity only; it does not create another signal.

## 5. Same-session opening batch

Execute only during `execution_session` and in this fixed order:

1. **Stock exits:** sell the planned long quantities at the session open.
2. **SPY funding:** after actual stock-sale proceeds are known, sell only the
   whole SPY shares needed to cover the maximum reservation of entry orders
   whose opening price is present and does not exceed their frozen 103% limit.
3. **Stock entries:** fill accepted entries at the session open in frozen rank
   order. If opening gaps or missing data make cash insufficient, apply the
   existing deterministic affordability rule to the lowest rank; do not add a
   substitute symbol or chase an unfilled order.
4. **Residual SPY:** after stock orders are terminal, buy the maximum affordable
   whole SPY shares using the same opening price and retain residual cash.

The pre-open decision therefore freezes *what may be traded and each stock's
maximum order*. The exact SPY share delta is the frozen funding/residual
formula applied to actual opening fills; opening prices never feed back into
signals, ranks, stock targets, or limits. This is the same atomic opening-batch
accounting convention used by Study 4 and must be labelled as a simulation
limitation.

Missing opening data, a stock open above its limit, or insufficient cash
cancels or reduces only through the frozen rules. Nothing is carried forward
or submitted later in the week.

## 6. Run controls and independent artifacts

Implementation must introduce separate Study 5 IDs, configs, wrapper,
checkpoints, output roots, series tags, and reports for both variants. It must:

- read local frozen inputs only and never fetch provider data;
- accept only origin year 2010 and years 2010-2025;
- require an explicit Study 5 confirmation flag and a clean pinned
  implementation commit;
- begin each variant with $50,000 and no predecessor checkpoint;
- require each later year to consume only that variant's immediately prior
  Study 5 checkpoint;
- preserve the identical-order zero-cost shadow and passive-SPY ledger; and
- never read, write, delete, relabel, or splice Study 3 or Study 4 artifacts.

The implementation agent must not perform the historical run. The delegated
execution agent must use the explicit confirmation flag from a clean committed
worktree and preserve the complete run log.

## 7. Required evidence and comparisons

For every weekly decision, retain the cutoff and execution dates, input hashes,
regime evidence, every held-position trigger evaluation, full candidate gate
results, ranks, sector occupancy, planned quantities and reservations, SPY
formula inputs, orders, fill/cancel/reduction reasons, costs, and end-of-session
portfolio state.

The final report must show baseline, Risk-On, and the common identically costed
passive-SPY path, including:

- terminal return and excess return versus SPY;
- CAGR, annual returns, annualized volatility, maximum drawdown, and Calmar;
- stock and SPY filled sides, completed stock trades, dollar costs, and
  turnover;
- time in stocks, time in SPY, residual cash, unfilled entries, and exit-reason
  counts; and
- post-event maximum exits filled late, including sessions late.

A separate descriptive table may compare Study 5 with the already-published
Study 4 extension on terminal return, CAGR, maximum drawdown, Calmar, turnover,
completed trades, and exit reasons. It must identify any inception or benchmark
boundary difference and must consume Study 4 artifacts read-only.

## 8. Rejection tests and interpretation

Reject the implementation or run if any of these checks fail:

- exactly one decision snapshot exists per variant and eligible week;
- holiday weeks map to the final NYSE session and its immediately prior cutoff;
- no intermediate session produces a candidate, allocation, or exit signal;
- a temporary intraweek exit condition that recovers by the cutoff does not
  become a sticky exit;
- mutating execution-session data cannot change the frozen decision; changing
  its open may change only fills, affordability, and SPY execution mechanics;
- every entry limit and planned stock quantity derives from the cutoff close;
- every cost equals filled shares times $0.0008;
- checkpoint identity and config hashes remain continuous and variant-local;
- the zero-cost shadow makes identical decisions; and
- Study 3 and Study 4 files remain byte-for-byte unchanged.

Because the full interval is retrospective, no performance threshold produces
a `PASSED` result. Report direction, magnitude, risk, turnover, data
limitations, survivorship bias, and simulated-execution limitations without a
financial recommendation. Any future confirmatory claim requires a separately
preregistered prospective study on genuinely new point-in-time evidence.
