# Post-earnings Friday-only execution replay

**Study family:** `pre-earnings-post-event-weekly-batch-v1`

**Status:** implementation and synthetic verification authorized; 2022–2025
exploratory development replay authorized by the owner on 2026-09-03.

## Question and evidence boundary

This study measures whether consolidating the existing low-fee post-earnings
strategy's transactions into one weekly Friday-open batch materially reduces
turnover or changes its performance. It is a new method, not a replay of the
published daily-execution result.

The 2022–2025 window is already observed. In particular, 2023–2025 was the
prior method's one-shot holdout. This new study may run those years only as
**exploratory development evidence**. It must not be described as a fresh
holdout, a confirmatory result, or a replacement for the published Study 3.

## Variants, capital, and unchanged rules

Run independent equal-allocation portfolios from $50,000:

1. `baseline`: entries allowed in every market regime.
2. `risk-on`: entries allowed only in `RISK_ON`.

Retain the low-fee post-event method's frozen selection, liquidity, event,
trend, sizing, sector, pin, price, whole-share, cash-staging, and accounting
rules. In particular: `BULLISH_CONTINUATION`, penalized `setupScore > 50`,
causally predicted events two to five weeks away, $10–$500 prices, three
open-plus-pending positions per sector, $1,000–$5,000 stock targets, and
`$0.0008 × filled shares` for every stock and SPY side.

State carries continuously from 2022 through 2025. Each variant has its own
checkpoints and output root. No predecessor checkpoint or artifact may be
read, rewritten, or relabelled.

## Friday-only execution

- Monday through the session before the week's execution session: evaluate
  held stocks, earnings state, trend, close-based drawdown, post-event floor,
  and maximum T+7 condition after each close. Candidate scans run daily and
  are recorded as `weekly_tracking` until the final decision session.
- An exit trigger is sticky. A later favorable close cannot cancel it.
- The final decision session is normally Thursday. It is the session before
  Friday's open. If Friday is an SPY-market holiday, use that week's final
  available SPY session as the execution session and the immediately prior
  session as the decision cutoff.
- At that final decision close, revalidate, rank, and allocate candidates from
  the real portfolio state. No weekday virtual fill, virtual P&L, virtual cash,
  or virtual position size affects allocation or reported performance.
- At the execution-session open, sell scheduled stock exits first, sell only
  the SPY shares required to finance accepted stock entries, buy accepted
  stocks, then buy whole SPY shares with the remaining cash.
- Friday's close is marked for equity but does not create a new signal. The
  next signal opportunity is the following week's first session.

All stock and SPY buy/sell fills therefore occur only at the weekly execution
open. A post-event max-hold condition may be executed after its nominal T+7
target; reports must preserve the late-exit flag.

## Run and reporting boundary

Use local frozen data only. Require a clean pinned implementation commit and
`--confirm-weekly-batch-development-run`. The wrapper accepts only origin year
2022 and years 2022–2025. Record per-session progress, year completion timing,
annual checkpoint chains, costs, filled sides, completed trades, exit reasons,
maximum drawdown, volatility, and the common passive SPY path.

After both variants complete, produce a side-by-side annual comparison. The
result may guide a future separately preregistered prospective evaluation, but
it cannot spend or reinterpret the prior 2023–2025 holdout.
