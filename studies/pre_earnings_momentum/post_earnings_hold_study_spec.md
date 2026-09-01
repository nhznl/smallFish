# Pre-earnings selection with post-event hold — frozen development design

**Study family:** `pre-earnings-post-event-hold-v1`
**Status:** IMPLEMENTATION AND SYNTHETIC VERIFICATION AUTHORIZED; UNRUN
**Prepared:** 2026-09-01

## 1. Question and evidence boundary

This independent development study asks whether retaining otherwise-qualified
pre-earnings momentum positions for at most seven post-event sessions improves
the earlier daily-redeployment result, and whether causal SPY regime gates
improve entry timing. It does not amend or overwrite the daily-redeployment or
cash-staging studies and cannot establish a validated edge from the requested
2010–2016 development window.

Only implementation and offline synthetic verification are authorized by this
document. A historical run requires a separate owner instruction.

## 2. Independent variants

Each variant starts an independent equal-allocation portfolio with $50,000:

1. `baseline`: eligible entries are allowed in every market regime.
2. `risk-on`: entries are allowed only in `RISK_ON`.
3. `risk-on-neutral`: entries are allowed in `RISK_ON` or `NEUTRAL`.

The proportional arm, TLT, SPXS, and other defensive sleeves are out of scope.
Regime gates affect new stock entries only. They never force an existing stock
sale. Blocked or otherwise unreserved capital follows the inherited SPY
cash-staging rule.

## 3. Inherited entry, allocation, and accounting rules

Except for the exit and regime rules below, inherit the selected daily/$500
cash-staging method:

- daily EOD candidate scans and next-session-open execution;
- causal `momentum-v3` `BULLISH_CONTINUATION` with penalized `setupScore > 50`;
- inherited liquidity, freshness, event-consistency, and causal 2–5-week event
  gates;
- universe `universe.csv - retired_symbols.csv`, with survivorship bias visible;
- whole shares, $1,000–$5,000 stock targets, and at most three open-plus-pending
  positions per sector;
- equal allocation only and no routine resizing of surviving positions;
- the existing close +3% entry limit and deterministic affordability trimming;
- 10 bps per side for stocks and SPY;
- identical-order zero-cost shadow accounting;
- year-end checkpoint carry without forced liquidation; and
- reservation of firm next-session stock-entry cash before sweeping only excess
  whole-share-eligible cash to SPY.

## 4. Causal market regime

At EOD decision session D, use SPY bars through D only:

- compute SMA50;
- call the SMA rising only when its current value exceeds its value five
  completed SPY sessions earlier;
- `RISK_ON`: SPY close above SMA50 and SMA rising;
- `NEUTRAL`: SPY close above SMA50 without a rising SMA;
- `RISK_OFF`: SPY close at or below SMA50; and
- `UNKNOWN`: missing, invalid, or insufficient history.

`UNKNOWN` fails closed in both gated variants. The regime recorded at D governs
only stock orders decided at D for the next open. A later regime change neither
cancels an already approved order nor liquidates a holding.

## 5. Exit rules

The following inherited exits remain active before and after earnings:

- raw `DOWN`/bearish Momentum Scanner trend; and
- capital-scaled close decline, fixed at entry from 20% at $1,000 principal to
  10% at $5,000 principal.

`BULLISH_REVERSAL`, setup-score deterioration, sideways trend, and regime change
are not exits.

For this study only, planned T-1 and `EARLY_REPORT` exits are disabled and
replaced by the post-event policy below.

### 5.1 Event date and reference floor

T is the first realized earnings date after the position's entry decision,
observed no earlier than EOD T. Realized event history never affects selection
or a decision before T.

At the completed stock close on T, set:

```text
post_event_floor = max(entry_fill_price, close_on_T)
```

The floor becomes active on the first subsequent SPY session. A later completed
stock close strictly below the floor schedules a sale for the following open.
There is no intraday stop. If no valid stock close exists on the anchor session,
use the entry fill as the floor and record the missing-close fallback.

If T is not a SPY session, the first SPY session on or after T supplies the
anchor close. This convention is required because the event cache contains a
calendar date but no before-open/after-close timestamp.

### 5.2 Maximum T+7 exit

The maximum exit executes at the **open of the seventh SPY trading session
strictly after T**. The order is decided using information available at the
prior completed close. If the stock has no valid open on that session, retain
the pending order and execute at the next valid open with a delayed-exit flag.

### 5.3 Missing realized event

If no realized event has appeared by the predicted event date fixed at entry,
use that predicted date as an explicit `predicted_fallback` T. Apply the same
anchor and seventh-session rules and expose the fallback in positions, trades,
decisions, summaries, and checkpoints. Do not silently restore T-1 behavior.

### 5.4 Simultaneous exits and pins

Record every simultaneously true exit. Post-event maximum-hold and floor exits
take no-pin precedence. A pure bearish or capital-scaled decline exit pins the
symbol for 30 calendar days. A post-event floor or maximum-hold exit does not.

## 6. Artifacts and comparison

Each variant writes to its own study ID and output root and retains the existing
daily equity, decisions, orders, trades, year-end state, checkpoint, summary,
report, manifests, progress lines, and `YEAR_COMPLETE` message.

Additional audit fields include market regime, gate decision, event-date source,
event date, anchor session/close, post-event floor, T+7 target session, and exact
exit triggers. Annual series reports are created separately for each variant;
a post-earnings comparison joins the three validated equal-arm summaries with
their common SPY benchmark.

## 7. Development protocol

After implementation review and separate authorization, run 2010–2016 one year
at a time. Each variant is an independent checkpoint chain beginning at $50,000
in 2010 and carrying state through 2016. Preserve durable per-session logs and
review the three annual paths side by side before authorizing any later year.

## 8. Rejection conditions

Reject the implementation if it uses a future realized date before T, treats
regime changes as exits, executes T+7 at a close, allows T-1 behavior in these
configs, pins a post-event exit, changes a predecessor config/spec/artifact,
uses fractional shares, alters the cost model, imports `stock-app`, accesses
the network in tests, or runs historical market data without new authorization.
