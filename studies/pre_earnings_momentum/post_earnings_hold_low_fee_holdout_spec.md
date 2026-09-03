# Post-earnings low-fee 2023-2025 holdout protocol

**Study family:** `pre-earnings-post-event-hold-low-fee-holdout-v1`

**Frozen and authorized:** 2026-09-03, before any 2023-2025 result was run

## Question and evidence boundary

This is the one-shot historical holdout for the selected low-fee post-earnings
method. It uses only the previously untouched calendar years 2023 through 2025.
Once execution begins, these years are spent for this method and must not be
rerun, retuned, or used to select a replacement rule.

The simulation uses the static `universe.csv - retired_symbols.csv` universe
and is therefore survivorship-limited rather than point-in-time evidence. A
positive result cannot establish a live trading edge.

## Frozen variants and state

Run two independent equal-allocation portfolios:

1. `baseline`: allow eligible stock entries in every market regime.
2. `risk-on`: allow eligible stock entries only in `RISK_ON`.

Each variant starts with $50,000 and no positions at the beginning of 2023.
State then carries continuously from 2023 to 2024 and from 2024 to 2025. No
development checkpoint is accepted. The two variants never share checkpoints.

Every strategy and accounting rule is inherited byte-for-byte from the
low-fee development design. The only config differences are the holdout phase,
holdout study IDs, and holdout output roots. In particular, transaction cost is
`filled shares * $0.0008` per stock and SPY side, price maximum is $500, scans
are daily, cash staging is enabled, and the maximum post-event exit is T+7.

## Primary endpoint and reporting

The selected strategy is `risk-on`. Its primary endpoint passes only if its
cumulative 2023-2025 ending equity is strictly greater than the identically
costed passive SPY benchmark's ending equity. Baseline is a diagnostic and
cannot rescue a failed Risk-On endpoint.

Always report annual and cumulative return, terminal excess, maximum drawdown,
annualized volatility, transaction count, costs, completed stock trades, and
exit reasons for both variants. Report the static-universe limitation beside
the verdict.

## Execution controls

- Use local data only; provider/network access is forbidden.
- Require a clean, pinned implementation commit for every annual run.
- Require `--confirm-low-fee-holdout` and reject years outside 2023-2025.
- Require origin year 2023 and the immediately prior holdout checkpoint after
  the origin year.
- Write to a new holdout artifact root; never alter development artifacts.
- Preserve per-session and per-year logs and validate all artifact hashes,
  config/commit continuity, whole shares, costs, sector caps, benchmark parity,
  and zero-cost order identity before revealing the result.
