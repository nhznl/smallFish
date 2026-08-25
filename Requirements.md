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

There is no active refactoring program. The architecture, brokerage, dead-code,
and documentation refactors are complete as of 2026-07-30, and their temporary
plans and phase documents have been retired. Architecture-shaped items under
**Deferred** are optional future decisions, not unfinished phases or migration
work.

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

**Compatibility-surface consumer audit.** Several public surfaces have no
current Angular consumer but may be used by scripts, notebooks, external HTTP
clients, or out-of-tree Python imports: `brokerage_ids()`, `descriptors()`,
`GET /stocks`, `GET /api/brokerages`, the two Symbol Ledger archive reads,
and the deprecated GET aliases for run jobs. Audit access logs and out-of-tree
consumers before an explicit retain-or-remove decision. Do not change response
shapes during the audit.

**Beta and Greek materialization trim (deferred by decision).** The owner
confirmed on 2026-07-30 that there are no external consumers. In-repository
value consumption is limited, but Greek scalar columns, beta values, IV fields,
provider fetches, and related sync counts will be retained for now. Any future
trim requires a new explicit decision and must preserve the currently used IV
and `as_of.market` semantics. See
[`docs/BETA_GREEK_CONSUMER_MEASUREMENT.md`](docs/BETA_GREEK_CONSUMER_MEASUREMENT.md).

**Durable long-running job lifecycle.** Run jobs prefer POST and have per-job
single-flight locks, but execution remains synchronous and responses have no
durable job identity. Decide whether the current local deployment is adequately
served by synchronous execution or needs a small in-process job registry with
status, identity, and idempotency. A distributed queue is not currently
justified.

**Deep brokerage API response contracts.** Brokerage write requests have
Pydantic models, but deep GET envelopes remain dictionaries serialized directly
by projection functions. Decide whether stronger response models provide enough
compatibility and maintenance value to justify the migration. Preserve existing
wire shapes.

**Optional Angular view-model decomposition.** Wheel has the first extracted
view model. Momentum Scanner, Stock Detail, Sector Rotation, Portfolios, and
Studies still mix transport, transformation, and presentation state. Extract
facades only where behavior tests demonstrate a concrete lifecycle, loading, or
error-state benefit; do not introduce a global state framework. Add a direct
StrategyStocks behavior spec when this work begins.

**Cross-screen formatting convergence.** Brokerage Holdings and Combined
Ledger share proven-identical formatters, and Symbol Ledger shares only its P/L
tone helper. Other money, date, sign, locale, and empty-value rules differ by
screen. Measure and approve each rendered contract before consolidating; do not
silently force Symbol Ledger or other screens onto `en-US` currency semantics.

**Frozen-study duplicate-helper cleanup.** Candidate and scan modules still
contain overlapping higher-low, days-since-cross, days-in-band, and
trailing-return helpers. Remove only copies proven unreachable, with
artifact-level regression checks. No frozen formula, artifact, or verdict may
change.

**Company-info transport boundary.** Stock Detail company information remains
the documented live-network exception inside FastAPI. Decide whether to keep
that narrow exception or separately design a `services/` transport or
materialized cache with explicit freshness semantics. This is optional
architecture work, not a current defect.

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

Ticker-clustered trade confidence intervals, point-in-time data acquisition,
full event-source parity, and a new prospective validation remain deferred for
the earlier completed studies. Their documented limitations remain unchanged
and they must never be promoted as validated edge evidence.

## Standing decisions

These are settled. Treat them as constraints, not open questions.

**The RSI/SuperTrend study is permanently closed without a holdout**
(2026-08-25). The complete 1999–2021 development run was technically valid but
unfavorable: the primary mean daily excess-return interval was entirely below
zero, and the strategy underperformed buy-and-hold in all 14 primary ETFs.
Pine/shared-TA primary outcomes were identical, but TradingView parity was not
completed. The owner declined to run the 2022–2025 holdout. Study
`rsi-supertrend-pine-v1` remains unpublished with `NO_VERDICT`; do not run its
holdout, reopen it, or present its development result as confirmatory evidence.
The current-universe stock cohort remains exploratory and survivorship-biased.
See
[`studies/rsi_supertrend/rsi_supertrend_study_spec.md`](studies/rsi_supertrend/rsi_supertrend_study_spec.md).

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
