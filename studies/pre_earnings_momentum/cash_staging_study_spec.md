# Daily redeployment cash-staging study

**Study ID:** `pre-earnings-daily-redeployment-cash-staging-v1`
**Phase:** development
**Owner authorization:** 2026-09-01

## Purpose and scope

This is an independent, equal-allocation-only development study. It tests one
execution change against the completed 2010-origin daily/$500 baseline:
preventing cash needed for already-approved next-session stock entries from
being unnecessarily swept into SPY overnight. It does not amend, overwrite, or
reinterpret `pre-earnings-daily-redeployment-v1` or its completed artifacts.

The initial authorized window is 2010–2016 inclusive. Years 2017–2022 require
a separate owner decision after reviewing the first-half result.

## Inherited rules

Except where this document explicitly differs, the binding methodology is
[`daily_redeployment_study_spec.md`](daily_redeployment_study_spec.md): causal
`momentum-v3` entry evaluation, `BULLISH_CONTINUATION` and `setupScore > 50`,
2–5-week forecast event gate, daily scans, $500 price cap, whole shares,
$1,000–$5,000 stock targets, three-position sector cap, 10 bps per side on
stocks and SPY, no routine resizing, raw bearish and capital-scaled price exits,
T-1 planned exits, pins, zero-cost diagnostic, and annual checkpoint carry.

Only the equal arm runs. The proportional arm is intentionally omitted; this
study does not compare allocation methods.

## Cash-staging rule

At each valid session close, after all held-position decisions and the entry
scan have produced firm stock-entry orders for the next SPY session, calculate:

```text
entry_cash_reserve = sum(
  pending_entry.shares * pending_entry.limit_price * (1 + 0.001)
  for pending stock-entry orders due at the next session
)

sweepable_cash = max(0, closing_cash - entry_cash_reserve)
```

Buy whole SPY shares at that close using only `sweepable_cash`; visible
whole-share residue remains cash. The reserve stays cash for one overnight
interval and funds the already-approved next-open stock entries. It is never
created for prospective candidates, forecasts, or a later scan.

If an entry is cancelled for a missing open or limit breach, its unused reserve
becomes sweepable at that same session's close, subject to any newly approved
next-session entries. If an entry opens within its limit, its reserve covers the
maximum modeled principal plus the 10 bps buy cost. If cash still proves
insufficient only because of an execution anomaly, the existing deterministic
affordability trimming rules apply.

This rule applies to cash from planned T-1 exits, bearish/price exits, SPY
sales, and any other realized source. It does not use realized earnings dates
or other future information. All unreserved cash is swept to SPY, so the only
intentional cash exposure is a firm next-session-entry reserve plus irreducible
whole-share residue.

## Required evidence

The implementation must prove with synthetic tests that the reserve:

- prevents a same-overnight SPY sweep followed by a next-open SPY sale for a
  firm stock entry;
- covers limit-price principal and modeled cost without negative cash;
- sweeps only excess cash and returns cancelled-entry cash to the next close
  sweep;
- preserves whole shares, sector caps, causal next-open timing, and identical
  zero-cost shadow orders; and
- leaves the baseline configuration/artifacts unchanged.

Every annual artifact must record `cash_staging_enabled` and the reserve
sessions, reserved dollars, and swept dollars. The completed first-half report
must compare calendar-year equity, SPY, transaction counts, stock/SPY costs,
and the new staging metrics against the matching 2010–2016 baseline rows.
