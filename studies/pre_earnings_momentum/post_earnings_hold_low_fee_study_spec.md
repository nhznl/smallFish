# Post-earnings hold with brokerage per-share fees — frozen development design

**Study family:** `pre-earnings-post-event-hold-low-fee-v1`

**Status:** IMPLEMENTATION, SYNTHETIC VERIFICATION, AND 2010–2022 DEVELOPMENT RUN AUTHORIZED

**Prepared:** 2026-09-02

## 1. Question and frozen predecessor

This development study measures the effect of replacing the predecessor's
10-basis-point-per-side transaction-cost assumption with the owner's observed
brokerage charge of $0.0008 per filled share per side. The predecessor study,
its configs, implementation commit, 2010–2022 artifacts, and reports remain
frozen and must not be edited, deleted, overwritten, or relabelled.

This is a new study identity because transaction costs affect affordability,
whole-share sizing, SPY sweeps, benchmark shares, and subsequent checkpoints.
The 2023–2025 period remains untouched and unauthorized.

## 2. Variants and only methodology change

Run two independent equal-allocation variants from $50,000 in 2010 through
2022 as continuous annual checkpoint chains:

1. `baseline`: eligible stock entries are allowed in every market regime.
2. `risk-on`: stock entries are allowed only in `RISK_ON`.

Every selection, signal, event, allocation, cash-staging, exit, pin, price,
liquidity, sector, timing, and reporting rule is inherited unchanged from
[`post_earnings_hold_study_spec.md`](post_earnings_hold_study_spec.md).
`risk-on-neutral` is out of scope.

The sole economic change is:

```text
transaction_cost = filled_shares * $0.0008
```

Apply that formula independently to every filled stock and SPY buy and sell.
There is no notional-basis-point fee, commission, per-order minimum, or fee
rounding. The passive SPY benchmark uses the same per-share fee on its opening
purchase and terminal liquidation mark.

This fee models only the stated brokerage charge. It does not claim that
bid/ask spread, slippage, or market impact are zero; those remain limitations.

## 3. Evidence and execution boundary

- Use only local frozen inputs; do not fetch provider data.
- Run 2010 first with no checkpoint and $50,000 per variant.
- Run each later year from that variant's immediately prior checkpoint.
- Keep the two variants independent.
- Preserve durable per-session and per-year logs.
- Use separate study IDs, config files, output roots, series tags, and reports.
- Never read predecessor checkpoints into the low-fee chains.
- Never write into the predecessor `post_earnings_hold/` artifact tree.
- Do not run 2023, 2024, or 2025.

## 4. Required diagnostics and rejection conditions

Retain the identical-order zero-cost shadow. Reject the implementation or run
if any filled order cost differs from `shares * 0.0008`, the benchmark uses a
notional cost, the zero-cost shadow changes decisions, configs drift within a
chain, checkpoints are crossed between variants, predecessor files change, or
any post-2022 year is run.

Annual reports must expose transaction counts and dollar costs. The final
comparison must contain both variants and their identical passive SPY path for
every year from 2010 through 2022.

## 5. Interpretation

All results are development evidence from a static survivorship-biased
universe. They are not a financial recommendation or a validated edge. The
untouched 2023–2025 period cannot be used without a separate owner decision.
