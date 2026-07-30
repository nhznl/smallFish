# smallFish — open work

What is still outstanding, deferred, or decided-and-closed. Nothing here is a
record of finished work: completed implementation lives in the code, and the
durable contracts it satisfies are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/DATA.md`](docs/DATA.md), and
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

Standing decisions are included because they constrain future work. A closed
item is not dormant — reopening one takes an explicit decision, and where noted,
a dated pre-registration.

## Active

**Prospective RTH quote evidence.** Collect ordinary regular-trading-hours
option-quote observations across several calendar dates, then inspect their
side-specific timestamps, freshness, quality distribution, and eligibility
reasons in the Wheel → Option Quotes tab.

The collection mechanism is built; what is missing is a time series. Off-hours
snapshots correctly fail closed as ineligible, so they do not accumulate
evidence. This is data collection and monitoring — not a scheduled job, and not
an options-edge claim.

## Open gaps

**Retirement option lifecycle shapes are unconfirmed.** `SELL_TO_OPEN` and
`BUY_TO_CLOSE` are observed and handled. A true `EXPIRATION` or `ASSIGNMENT`
activity has never posted, so its exact structure is unverified. Both are
handled generically by structured action and net value; confirm the real shape
when the first one arrives rather than assuming the generic path is right.

## Deferred

Each needs either new data or an explicit decision before it can start.

**Historical options P&L backtest.** Blocked on point-in-time option-chain,
event, and membership data, and on a specified simulator. Without those, any
result would be reconstructed from present-day data and could not support a
claim.

**Historical implied volatility.** Live exact-contract Tastytrade IV is
implemented and governs current risk output. Historical IV provenance is not,
so no historical option study can use IV.

**Options-analytics remediation, remaining packages.** Typed cash-flow and
lifecycle accounting, full assignment and reconciliation, and the integrated
verification package. The safety-critical parts — fail-closed quote quality,
exact contract identity, immutable archives, deterministic archive
verification — are done; these are the completeness items.

**Stock-strategy studies.** Closed 2026-07-18. Ticker-clustered trade
confidence intervals, point-in-time data acquisition, full event-source parity,
and a new prospective validation are all deferred.

Reopening requires an explicit decision, and any validation claim requires a
dated pre-registration made **before** new outcomes are observed. The completed
studies keep their documented limitations and must never be promoted as
validated edge evidence.

## Standing decisions

These are settled. Treat them as constraints, not open questions.

**The 11-sector page is permanently descriptive** (2026-07-26). It must never be
promoted as a predictive signal or an edge claim. A complete 11-sector history
cannot reach back far enough to test one — XLRE begins in 2015 and XLC in 2018,
leaving too few disjoint observations. The separately pre-registered legacy-nine
study cannot lift this gate or be generalised to the current taxonomy.

**Published studies are frozen.** A spent holdout cannot be rerun, a published
parameter cannot be retuned to improve a result, and a failed verdict stays
published as failed. A revised method needs a new pre-registered study. See
[`studies/README.md`](studies/README.md).

**No scheduled retirement event sync** (2026-07-24). The manual *Sync from
Fidelity* action already refreshes the event ledger. SnapTrade serves positions
in real time but transactions on a slower cadence, so a closing transaction can
lag its position by hours; rerunning the manual sync collects it. That window is
normal operation, and the ledger correctly reads `OPEN`/`INDICATIVE` until the
close lands.

**Brokerage access is read-only.** smallFish never places, modifies, or cancels
an order, and never receives a brokerage password.

**Provider I/O boundary is complete** (2026-07-29). Production Tastytrade and
SnapTrade SDK imports belong only in `services/`, which owns environment-backed
credentials, session/client lifetime, streaming/paging, and raw payloads.
Consumer runtimes retain normalization, policy, artifact writes, and public API
shapes. Do not reintroduce SDK calls into `stock-app/`, `utilities/`, `studies/`,
or `tools/`.

## Housekeeping

Small, unblocked, no decision needed.

- **CI actions target the deprecated Node 20 runtime.** `actions/checkout@v4`
  and `actions/setup-python@v5` are being force-run on Node 24. They pass today;
  bump them when convenient.
- **`npm audit` reports advisories in the Karma test toolchain.** Dev-only, none
  reachable from the application bundle. Do not `npm audit fix` reflexively — it
  would move Angular tooling off the committed lockfile.
