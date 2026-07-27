# smallFish requirements

This document records the current product requirements, operating contracts, and
backlog for smallFish.

## Platform contracts

- Runtime configuration belongs in ignored root `app.env`, created from
  `app.env.example`. Source code must not contain credentials or machine-specific
  paths.
- `SFP_DATA_DIR` is the shared data root and `SFP_LOG_DIR` is the log root.
  `./commands.sh` runs from the repository root, so `data` and `logs` are valid
  relative values; direct module launches should use absolute paths or the same
  repository-root working directory.
- `utilities/` and `stock-app/` use separate virtual environments and dependency
  manifests. They must not import one another; both may import standard-library-
  only contracts from root `models/`.
- The Angular application consumes the FastAPI API on port 8000. Preserve its
  published JSON shapes unless the UI is changed in the same work.
- Generated data belongs under root `data/`; source packages contain no generated
  artifacts.

## Data contracts

### Universe and prices

- `data/universe.csv` is the symbol registry. Its rows contain
  `symbol,name,type,memberships,source,pinned,last_seen,sector`.
- `data/retired_symbols.csv` is the authoritative retirement journal. Both
  packages compute the live universe as `universe.csv − retired_symbols.csv`.
- Cached price files live at `data/{year}/{SYMBOL}.txt` and contain
  `MM-dd-yyyy,open,high,low,close,adjClose,volume` rows.
- The event backtest derives its SPY regime from the adjusted canonical SPY
  cache, with a 50-session warm-up requirement. The retired raw
  `data/spy_daily.csv` artifact and its downloader are not part of the runtime
  contract.
- Retrieval uses adjusted price history and keeps `adjClose == close` in the
  cache. A dividend or split detection requires a whole-history rewrite so one
  file never mixes adjustment vintages. Adjustment drift below the audit's
  0.5% per-field tolerance may persist between corporate actions; it is
  bounded and accepted.
- The first incremental scrape after a year rollover also fetches the previous
  year's remaining sessions (appended to the prior-year file) so a corporate
  action inside the rollover gap still triggers the whole-history repair.
- If a triggered repair's history fetch fails, the symbol is recorded in
  `pendingAudit.txt` and the repair is retried at the start of every later
  scrape run until it succeeds.
- A provider history that omits a cached date or year must never be used to
  rewrite that symbol: guarded audit runs
  (`--require-full-year-coverage`) mark it `PARTIAL_FETCH` and refuse the
  rewrite. Each such symbol then goes through lineage review: confirm whether
  the omitted date was a real exchange session (against the benchmark
  calendar); if the symbol is no longer in the live universe, delete its cache
  and slope artifacts; if it is live, repair from an alternate source or
  record an explicit accepted exception in the audit log before any rewrite.
- Weekly/monthly return "slopes" are **not** a stored artifact. They are a pure
  function of the cached bars, are rendered only on the stock-detail page, and
  are computed on demand per symbol and memoized in the stock-app cache
  (`cache.slopes`, invalidated on reload). There is no `data/slopes/`
  directory, no `slopes` command, and no rewrite-repair hook: a price-cache
  change cannot leave a derived slope artifact stale because none exists.

### Ledgers

- Imported Tastytrade activity is stored separately in ignored
  `data/ledger_options/options_activity.csv`; editable group metadata and
  membership are stored in `data/ledger_options/options_groups.csv` and
  `data/ledger_options/options_group_members.csv`.
- Current broker marks are stored in
  `data/ledger_options/options_position_marks.csv` and feed the options-risk
  dashboard. Timestamped live Tastytrade DXLink IV/Greeks observations are
  stored separately in `data/ledger_options/options_greeks.csv`. Risk uses a
  fresh exact-contract Tastytrade IV first, then chain IV, then a labelled RV
  fallback. Timestamped Tastytrade market-metric beta is stored in
  `data/ledger_options/options_betas.csv` and is the sole beta input to
  beta-delta and portfolio-risk totals. The locally computed 252-session beta
  remains a visible comparison diagnostic. Invalid, future, stale, or missing
  governed observations fail closed. Manual options risk rows are retired.
- CSV writes are lock-protected and use a temporary file plus atomic rename.
- Options monetary values are parsed and calculated with `Decimal`. API responses
  convert final values to JSON numbers only at the boundary.

#### Holdings gain/loss trend alerts

- Each retirement holding's gain/loss % is tracked across syncs in ignored
  `data/ledger_retirement/holdings_trend.csv`, keyed by `(account_id, symbol)`,
  so a position whose gain is shrinking or whose loss is deepening can be flagged
  for action. Updated once per holdings sync (`snaptrade_service._update_trend`,
  best-effort); the read path (`portfolio()`) only displays the stored state.
- The reference is a **peak high-water mark** of the gain/loss %. A favorable move
  (gain grows or loss shrinks) ratchets the peak up and clears any alert. An
  adverse move that worsens the % by at least a **relative** threshold
  (`(peak − current)/|peak| ≥ 10%`, `SFP_HOLDINGS_TREND_THRESHOLD`) trips a sticky
  alert and re-baselines the peak, so a further leg down alerts again; a
  sub-threshold adverse move holds the peak so a slow multi-sync slide keeps
  accumulating. A materiality floor (`SFP_HOLDINGS_TREND_MIN_BASE`, default ±5%)
  treats near-breakeven holdings and cash as flat. Options are excluded (they
  trend via the option event ledger).
- The Angular holdings table flags an alerting row (red border + a ▼ badge with
  the relative drop, tooltip detail) and offers a "Declining only" filter. Needs
  at least two syncs with a move between them to surface an alert.

## Analysis workflow

The normal sequence is:

```bash
./commands.sh universe
./commands.sh scrape
./commands.sh fetch
./commands.sh scan
./commands.sh wheel
./commands.sh chains [--horizon-dte 7,37] [--symbols AAPL,MSFT] [--min-otm-pct 5]
./commands.sh verify-premiums [run-id]
```

The wheel screen is analytics tooling, not trade advice. Its historical
frequencies are descriptive measurements over overlapping windows, not
probabilities or recommendations. It displays sample counts, price/event
freshness, terminal expiry-ITM frequency, touch frequency, realized volatility,
and earnings-window state.

Wheel reports use `smallfish.wheel` schema version 2 and declare
`run_mode=CURRENT_CONTEXT_ONLY`: price calculations are bounded by the requested
as-of date, but current universe/event snapshots bar the output from
point-in-time validation claims. Each overlapping frequency has a disjoint-window
diagnostic sampled at one-horizon strides. The command maintains dated
compatibility CSVs and archives every invocation under `data/wheel/runs/` with a
manifest, source digests, exclusions, configuration, and artifact hashes.

The subsequent `chains` stage discovers exact standard option contracts through
Yahoo, then enriches them with Tastytrade DXLink bid/ask observations. Premium
schema version 3 preserves quote-provider status, exact streamer identity,
side-specific bid/ask timestamps and sizes, the provider event timestamp,
retrieval time, market session, and typed quality reasons. Freshness uses the
older of the bid and ask timestamps. Yahoo quotes remain diagnostic-only when
Tastytrade is missing; they cannot authorize entry economics. Every invocation
is archived under `data/premiums/runs/` with provider coverage/quality counts,
source hashes, a manifest, and dated/latest compatibility views. The Wheel UI's
`Collect Option Quotes` action and `./commands.sh chains` run this same path.

Wheel horizons are 7, 14, 30, 37, and 45 calendar DTE. Bar calculations use
`round(DTE × 252 / 365)` exchange sessions. Wheel reports use one row per symbol
and horizon.

### Sector rotation from ETF price leadership

The platform must provide a separate sector-rotation analysis using the 11 US
Select Sector SPDR ETFs, benchmarked to SPY:

| Sector | ETF |
|---|---|
| Communication Services | XLC |
| Consumer Discretionary | XLY |
| Consumer Staples | XLP |
| Energy | XLE |
| Financials | XLF |
| Health Care | XLV |
| Industrials | XLI |
| Materials | XLB |
| Real Estate | XLRE |
| Technology | XLK |
| Utilities | XLU |

The analysis is a price-leadership and rotation proxy, not a literal fund-flow
measurement. Adjusted ETF price and volume can show one sector gaining relative
strength while another loses it, but cannot establish dollars flowing between
funds without point-in-time shares-outstanding, creations/redemptions, or AUM
data. UI and reports must use “rotation,” “leadership,” or “relative strength”
language rather than claiming measured money flows.

- Align validated daily ETF bars to the SPY session calendar and fail closed for
  a sector ETF that lacks the required history.
- Calculate 5-, 20-, and 63-session total returns; excess return versus SPY;
  cross-sector percentile/rank; and the change in rank and relative strength
  from the prior comparable window.
- Calculate pairwise relative-strength ratios for explaining potential switches.
  For example, a rising `XLV / XLK` ratio means Health Care is outperforming
  Technology over that interval; it does not by itself prove investor cash
  moved directly from XLK to XLV.
- Surface a possible `Technology → Health Care` rotation only when XLV is
  strengthening and improving in cross-sector rank while XLK is weakening and
  losing rank. Show the underlying 5-/20-/63-session evidence and optionally
  volume confirmation rather than emitting an unexplained categorical call.
- Treat volume versus its prior-20-session baseline as confirmation only. ETF
  volume is trading activity, not net subscriptions, and cannot convert the
  signal into a fund-flow measurement.
- Write a dated snapshot plus history under `data/sector_rotation/`, with the
  standard reproducibility manifest. Provide a dedicated command such as
  `./commands.sh sector-rotation`; do not merge this logic into the earnings
  stock scan.
- Before presenting the output as predictive, define and test a forward
  relative-leadership endpoint. Until then it is descriptive market-regime
  context, not trade advice or an edge claim.

**Implemented — descriptive slice (2026-07-25).** `utilities/sector_rotation.py`
plus `./commands.sh sector-rotation` compute the descriptive measurement above;
`GET /sectorRotation` serves the archived snapshot and the Angular **Sectors**
tab renders it.

- *Placement.* Sectors is a top-level tab in the **Research** nav group beside
  Momentum/Strategy/Wheel, not a sub-tab of Portfolios. It measures the market,
  while Portfolios tracks user-authored holdings; grouping it there would
  conflate the two and bury a one-click screen two levels deep.
- *Alignment.* SPY defines the session calendar. A sector ETF missing any
  benchmark session in the required lookback, failing the shared price
  validation, or lacking aligned history is excluded with a typed reason
  (`missing_benchmark_sessions`, `price_validation_failed`, `no_cached_history`,
  `insufficient_aligned_history`) and stays visible in the UI rather than
  silently disappearing. Bars are never interpolated and windows are never
  silently shortened. `required_sessions` = each window plus its prior
  comparable window plus the anchor bar (127 for a 63-session window).
- *Measurements.* 5/20/63-session total return, excess versus SPY, cross-sector
  rank and percentile, rank change and relative-strength change versus the prior
  comparable window, pairwise relative-strength ratios, and window volume over a
  prior-20-session baseline. `rank_change` is positive when a sector moves toward
  rank 1. `LEADING`/`LAGGING` require the excess return and the rank to agree, so
  a top-ranked sector that still trailed SPY reads `NEUTRAL`.
- *Rotation candidates.* A `SOURCE -> TARGET` pair surfaces only when the target
  is strengthening AND improving in rank while the source is weakening AND losing
  rank. Each candidate carries its per-window evidence and a
  `windows_confirmed / windows_evaluated` count, so a partially-supported pair
  reads as such instead of an unexplained categorical call.
- *Language.* Volume is confirmation only. The UI leads with a "rotation proxy,
  not fund flow" banner, and the archived `measurement_basis` / `not_validated`
  strings are carried verbatim through the API so the UI cannot state the result
  more strongly than the measurement supports.
- *Artifacts.* `data/sector_rotation/{as_of}.csv` (sector × window),
  `{as_of}.pairs.csv`, `{as_of}.rotation.json`, and `latest.json`, with the
  standard manifest on the primary CSV. The reader fails closed on an
  unsupported schema, a snapshot disagreeing with its pointer, or a pointer
  naming a path outside the archive directory.
- *Verified 2026-07-25* against the live cache as of 2026-07-23: all 11 sector
  ETFs aligned over 127 sessions, hand-checked against the raw cache (XLK 63-session
  +13.01% vs SPY +4.06%, excess +8.95%). 12 utilities tests and 6 stock-app tests
  cover alignment fail-closed behavior, sign conventions, and the both-sides-agree
  rotation rule.

**History backfilled (2026-07-26).** All 11 sector ETFs now hold 2020–2026 in
the price cache at 1,647 rows each, identical to SPY, so they align to the
benchmark session calendar with no exclusions. The earlier 2026-only constraint
is gone: the 63-session window plus its prior comparable window consumes 127 of
~1,647 available sessions rather than 139, so longer windows are now
configurable in `utilities/config/sector_rotation.yaml` and `--as-of` supports
historical snapshots. The backfill did not change any current measurement —
`load_aligned_bars` reads a trailing window of exactly `required_sessions`, and
the re-run reproduced the pre-backfill leadership numbers byte for byte, which
also confirms the backfill left the existing 2026 bars untouched.

Slopes need no step after a backfill: they are derived on demand from the price
cache rather than stored, so a backfill is visible on the next detail-page view.

**Predictive gate permanently closed for the product (user decision
2026-07-26).** The 11-sector page remains descriptive market-regime context and
must never be promoted as a predictive signal or edge claim. A complete
11-sector history cannot be extended to 1998: XLRE began in 2015 and XLC in
2018, leaving too few disjoint 63-session observations for the page's current
universe. A separate, pre-registered study of the nine legacy Select Sector
SPDRs is permitted under `studies/sector_rotation/sector_rotation_study_spec.md`, but its
result cannot lift this product gate or be generalized to XLC, XLRE, or the
current 11-sector taxonomy.

**Legacy-nine historical study completed once (2026-07-26) — primary FAILED.**
The frozen protocol was committed before pre-2020 outcomes were added or
scored. The authoritative runner then used 81 disjoint pre-2020 signal
decisions and 2,611 candidate pairs. Mean 63-session date-aggregate
target-minus-source spread was +0.0833%, with a dependence-aware 95% CI of
-0.4524% to +0.6362%; the interval crossed zero. The realized 80%-power minimum
detectable mean was 0.8297% per 63 sessions, so the observed estimate was also
far below the study's practical detection threshold.

The result ranked at the 74.3rd percentile of 1,000 matched random-pair
controls, did not reliably beat plain 63-session momentum, and had a 50.33%
pair hit rate. Pair-event mean was +0.1219% gross and -0.2781% after the frozen
40-basis-point pair cost. The 2020+ sensitivity was adverse (-1.2928%, 95% CI
-2.4046% to -0.2565%, 26 decisions) but remains secondary.
Pair/window FDR flags in the archived secondary table are invalid for inference
because their normal approximation did not account for same-date pair
dependence; they cannot rescue the failed date-level primary. The analytical
artifacts reproduced byte for byte. See
`studies/sector_rotation/sector_rotation_study_spec.md` for the protocol, result, limitations,
and immutable artifact contract.

**Post-study methodology correction.** The protocol's claim that 2020–2026 was
already “spent/development” was false. No prior ETF-focused forward study had
observed those outcomes; contemporaneous descriptive snapshots did not spend
the historical forward period. All 108 available decisions could have been in
the frozen primary, rather than only the 81 ending before 2020; one additional
decision crossed the artificial 2019/2020 boundary. The 2020+
outcomes became observed when the authoritative run calculated its sensitivity,
so they cannot now be retroactively promoted into the primary. The original
artifacts remain immutable and the correction is recorded rather than silently
rewriting the protocol.

**Legacy-nine v2 full-period exploration completed once (2026-07-26).** The
post-outcome plan included all 108 disjoint decisions through 2026-07-24 while
preserving the exact v1 signal and date-level dependence treatment. Pooled
target-minus-source spread was -0.2277% per 63 sessions (95% interval -0.7320%
to +0.2757%); its point estimate was negative and its interval crossed zero.
The pair hit rate was 49.38%, mean pair spread was -0.1790% gross and -0.5790%
after the frozen cost, and the result ranked at only the 3.2nd percentile of
1,000 matched random-pair controls. Rotation did not reliably outperform plain
momentum.

The 2020+ mean was 1.3762 percentage points below the pre-2020 mean; its
exploratory regime-difference interval was -2.5906 to -0.1814 percentage
points. Because both period results were observed before v2 was planned, this
is a post-outcome historical stability diagnostic, not a fresh confirmatory
test. V2 has `EXPLORATORY_NO_VERDICT` status, performs no pair/window hypothesis
tests, and cannot lift the permanently descriptive 11-sector product gate. See
`studies/sector_rotation/sector_rotation_study_v2_spec.md` and the immutable v2 artifacts.

## Options ledger

The Options view has two deliberately separate broker-sourced layers: imported
activity for trade grouping/P&L and current broker position marks for the
portfolio-risk calculation.

### Tastytrade broker activity and groups

- `Sync Tastytrade` reads the configured live account through the official SDK;
  it never places, changes, or cancels an order.
- Sync defaults to January 1 through today and is idempotent by the local
  `TRADING` ledger account plus transaction ID. It imports option executions and lifecycle events,
  plus same-year equity executions for symbols with option activity so assigned
  shares and stock-based management remain in group P/L.
- `SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS` keeps intentionally external or
  dismissed symbols out of subsequent imports.
- Imported executions are immutable broker facts. Group name, notes, status,
  and event membership are editable in separate files.
- Groups are constrained to one account and underlying symbol. The first sync
  creates one default group per symbol; users may create additional groups and
  reassign executions when one symbol has multiple distinct strategies.
- Group P/L is signed broker `net_value` cash flow, including fees, plus signed
  current marked value for reconstructed open positions. A flat group is
  realized P/L. An open group is labelled `INDICATIVE` because Tastytrade does
  not provide a defensible mark-observation timestamp with the position mark.
  Missing marks or event-to-position quantity mismatches make P/L unavailable.
- Tastytrade imports use the local `TRADING` ledger account. The integration
  reads its `TT_*` credentials directly from ignored root `app.env`; account
  numbers and secrets are not written to the activity ledger.

The initial live 2026 import on 2026-07-20 stored 80 events across 22 default
symbol groups with zero ungrouped events. A targeted follow-up imported four
reviewed JOBY fills from order `408233162` on 2025-09-19 and proved that its
option assignment and expiration quantities reconciled. The user subsequently
chose to track BTU separately and remove the closed JOBY trade. Both symbols and
their 11 events were removed from the active ledger and added to
`SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS`, leaving 73 events across 20 groups with
no position mismatches. The temporary local recovery backup was retired after
owner approval.

- Broker risk rows are derived from current Tastytrade position marks. They may
  include `SHORT_PUT`, `SHORT_CALL`, `LONG_PUT`, `LONG_CALL`, `STOCK`, or
  `OTHER`, and are treated as `OPEN` until the broker position disappears on
  the next sync.
- Historical broker events remain immutable facts used for trade grouping and
  P/L. Editable group metadata and event membership remain separate from those
  broker facts.

### API and UI

- `GET /options?account=` returns current broker-position `rows`, risk totals,
  and warnings, with optional account filtering.
- `POST /options/activity/sync` performs the read-only, idempotent broker import;
  `GET /options/activity` returns executions, groups, P/L, and reconciliation
  state. `/options/groups*` and `/options/activity/{id}/group` edit only group
  metadata/membership.
- The Angular Options tab has activity/group controls, account filtering,
  broker-position totals, warnings, and the risk dashboard. It cannot create
  or close a manual options trade.

## Options risk dashboard

Risk is calculated at read time from current standard broker positions.

- Gross cash commitment is stock purchase debit for held shares plus
  `strike × qty × 100` for open short puts. It is not broker margin or buying
  power.
- Stock market value and stock cost are displayed separately.
- Beta-weighted delta uses Black-Scholes option delta, cached price history,
  annualized realized-volatility fallback, dividend yield, configured rate, and
  a beta calculated from aligned SPY returns. Missing or stale inputs produce an
  explicit unavailable value rather than a default.
- The broker-fed dashboard covers only the Tastytrade `TRADING` account; Fidelity
  retirement positions are intentionally outside this risk view. Its approved
  cash limit is `$50,000`, configured in `stock-app/config/options_risk.yaml`.
- The dashboard must state that delta is a point-in-time first-order estimate and
  does not capture gamma, vega, gap, or broker-margin risk.

Warnings include settlement-needed rows, near-ATM short options near expiry,
earnings concentration, covered-call break-even conflicts, and ex-dividend early
assignment risk when the data is available.

## Architecture

```text
smallFish/
├── models/                 # shared standard-library data contracts
├── utilities/              # shared retrieval, analytics, wheel, and chains
│   ├── options/            # Wheel, chain quotes, and archive verification
│   ├── sector_rotation.py  # Select Sector SPDR leadership vs SPY
│   └── strategies/
│       └── pre_earnings_momentum/ # scan, scoring, config, and backtests
├── stock-app/              # FastAPI API, ledgers, and risk analytics
├── stock-app-ui/            # Angular dashboard
├── data/                   # shared cache and generated artifacts
└── logs/                   # operational logs
```

`models/` may contain dataclasses, enums, CSV field order, parsers,
serializers, and basic validation. It must not contain framework dependencies,
network calls, filesystem discovery, or strategy/risk algorithms.

## Verification

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
```

Latest verification (2026-07-25): 293 utilities tests and 204 stock-app tests
pass, covering the Wheel collection-scope and sector-rotation additions. The
Angular production build passes and `./commands.sh build-ui` regenerates
`stock-app/static`.

Earlier refactor verification (2026-07-18): 87 stock-app tests and 190 utilities
tests passed before the study-2 additions; the focused strategy-suite follow-up
passed 51 tests (30 backtest, 21 event/candidate/forecast). `./commands.sh
scan` completed through the new strategy package, wrote 18 candidates to the
namespaced report directory, and the stock app read all 18. The Angular
development build passes; the production build also passes (944.92 kB initial
bundle, explainer routes lazy-loaded) and `./commands.sh build-ui` regenerates
`stock-app/static`.

## Backlog

### Completed foundation (2026-07-17)

- The general analytics review/remediation work is complete and consolidated in
  `studies/pre_earnings_momentum/README.md`. Options audit,
  remediation, architecture, and remaining-work decisions are consolidated in
  this document’s options backlog below; detailed pre-fix forensics and the
  former standalone decision records remain available in Git history.
- Live scan, technical backtest, and event backtest now share corrected core
  indicator conventions: prior-20-session volume baseline and a five-completed-
  session SMA slope. RSI, MACD, OBV, YTD, volume, and numeric-width defects
  identified in the analytics review have been repaired and regression-tested.
- The scanner now fails closed on an unknown market regime, applies a three-
  session freshness gate, has bounded downside-extension checks, reports stale
  exclusions, and caps each sector at 10 candidates. Its current stock scan
  requires events two to five weeks away and 20-day average volume of at
  least four million shares.
- Price readers used by the live scan and both backtests validate hard
  corruption (wrong-year rows, conflicting duplicates, non-finite/non-positive
  data, and impossible high/low/close relationships). Quarantines are reported
  and recorded in artifact manifests. Opening-auction prices are accepted as
  vendor-supplied data and do not trigger a quarantine or skipped entry.
- Cache audit repairs, cache/universe cleanup, and slope cleanup have been run.
  Cache files whose symbols are absent from `universe.csv` have been removed;
  the current cache has no out-of-universe OHLCV or slope artifacts.
- Scan, technical-backtest, and event-backtest outputs now receive reproducible
  metadata sidecars (arguments, configuration, data hashes, commit, and
  dependency versions).
- A canonical technical candidate engine now powers the live scan and is
  replayed by the technical backtest. It has deterministic ordering and frozen
  as-of mutation fixtures. The backtest uses the live universe rather than
  arbitrary cache files and requires contiguous warm-up through end-year cache
  coverage.
- The pre-earnings strategy is isolated under
  `studies/pre_earnings_momentum/`, including its scanner,
  candidate engine, scoring, backtests, and behavioral configuration. Shared
  price, universe, indicator, event-fetching, and manifest services remain at
  the `utilities/` level. Strategy reports, scan snapshots, and backtests are
  namespaced by strategy, while the legacy `commands.sh scan`, `backtest`, and
  `event-backtest` forms continue to default to this strategy.
- The frozen pre-earnings E3 study runner now uses the canonical candidate
  engine plus a causal naive-anniversary earnings-date forecaster, conservative
  limit-on-next-open fills, predicted-T-1 exits, forced exits after early
  reports, costs, sector/position constraints, random controls, and bootstrap
  daily-excess diagnostics. Its one-shot holdout was completed: the no-sweep
  portfolio returned +22.42% versus SPY +49.17% (281 trades), so its primary
  portfolio endpoint failed. See `backtest_spec.md` for the result and limits.
- Study 2's optional SPY cash-sweep runner is implemented and replayed once on
  the study-1 dates. It reserves pending entries on Friday, sells SPY at the
  next entry-session open only as needed, and re-sweeps released cash at the
  close. The spent-window replay returned +53.16% versus SPY +49.17%, but is
  explicitly exploratory because both the dates and sweep were chosen after
  study 1. Outputs are labeled accordingly and isolated under
  `strategy_study/sweep/`; see `backtest_spec_2.md`.

### Remaining work / handoff

**Research Studies app refactor is approved and planned (2026-07-26).** The
Strategy area will become a common Research Studies experience for the
pre-earnings and sector relative-leadership studies, including shared study and
variation metadata, outcomes, typed statistics, provenance, and optional scan
behavior. Implementation scope, architecture decisions, compatibility
boundaries, acceptance criteria, and the live progress tracker are in
`RESEARCH_STUDIES_REFACTOR_PLAN.md`.

**Stock-strategy studies are closed for now (user decision 2026-07-18).** The
registered ticker-clustered trade CI, E1/E2 point-in-time data acquisition,
full event-source parity, and a new prospective validation are intentionally
deferred. The completed study-1 and study-2 results retain their documented
limitations and must not be promoted as validated edge evidence. Reopening
this workstream requires a new explicit decision and, for any validation claim,
a dated pre-registration before new outcomes are observed.

**Options analytics resumed for a bounded broker-ledger slice on 2026-07-20.**
Previously implemented and verified safeguards include strict wheel quality,
expiry-first chain context, immutable/versioned premium observations, exact
contract identity, fail-closed quote quality and portfolio completeness,
bid-based seller economics, side-specific entry strikes, and broker-sourced
activity/group and risk-position data. This is not completion of the options
remediation plan. Live Tastytrade IV provenance for exact open contracts is now
implemented; historical IV remains deferred. Typed cash-flow/lifecycle
accounting, full assignment/reconciliation, integrated verification, a
meaningful RTH prospective quote history, and historical options research
remain deferred. The prospective collection mechanism itself is implemented.
The immutable Tastytrade activity ledger and editable same-symbol groups are
now populated for 2026, and current broker marks feed the options-risk
dashboard. Historical broker events remain the trade/P&L source; current marks
remain the risk-position source. The documentation cleanup, manual-ledger
retirement, and user-led broker-data verification are complete. The user chose
not to add further reconciliation warnings; the current broker/group mismatch
state remains available as a diagnostic. The broker-ledger app remains paused in
its current operating state; the options-analytics workstream resumed on
2026-07-24 for the Wheel v2 quality/uncertainty and prospective-quote slices.

1. **Resumed — complete the options-analytics remediation plan.** The
   broker activity/group P/L slice, immutable quote/context snapshots, typed
   quality states, prospective collection mechanism, archive observability, and
   deterministic archive verification are implemented. The remaining active
   task is to collect RTH observations over calendar time before judging Gate B
   evidence. A historical options P&L backtest remains
   deferred until point-in-time option-chain, event, and membership data are
   acquired and a simulator is specified. At the 2026-07-18 pause checkpoint,
   horizon mismatches and ITM entry-yield presentation fail
   safely, and incomplete/placeholder-limit portfolio risk cannot receive a
   control verdict. Wheel underlying quality now reflects strict validation and
   exchange-session freshness rather than a constant `OK`; chain observations
   now receive immutable run IDs, metadata, and manifests while retaining the
   daily compatibility view. Chain rows now distinguish quote, retrieval, and
   last-trade time; enforce typed freshness/session/validity states; and suppress
   entry economics when bid/ask time is unknown, stale, or invalid. Fresh,
   timestamped RTH seller economics use bid rather than midpoint and separately
   report intrinsic/extrinsic value, yields, put basis/cushion, and covered-call
   called-away/breakeven scenarios. Because yfinance does not expose the
   displayed bid/ask timestamp, its current observations fail closed as
   `UNKNOWN` instead of being presented as fresh. Premium snapshots now use
   declared schema version 2 and persist exact provider contract identity,
   currency, multiplier/deliverable, standardness/adjustment state, and typed
   identity quality. Selected expiry/side/strike are reconciled to the provider
   symbol; missing, mismatched, ambiguous, or non-standard terms fail closed.
   The risk reader rejects legacy/incomplete premium schemas and ambiguous
   normalized-key matches. Put entries now select only below-spot strikes and
   call entries only above-spot strikes, with independently configured sigma
   bands. Nearest ITM contracts are explicitly `ROLL_EXIT`, never entry-
   eligible, and written to a separate view artifact; deprecated symmetric
   strike configuration is rejected. Delta/cushion and ledger break-even/share-
   coverage selectors await their governed input contracts. Chain orchestration
   now builds a horizon-independent underlying pool, discovers listed expiries,
   and derives actual-DTE session/RV/event context before requesting a chain.
   Eligibility and rank caps apply independently to each actual-expiry pair;
   unknown non-exact event coverage, missing RV/session context, earnings-policy
   exclusions, and rank-cap drops are recorded explicitly. Context rows retain
   the recurring-holiday NYSE calendar source and declared RV step mapping, and
   the former 37-DTE-authorizing shortlist configuration is rejected. Chain runs
   also reject wheel CSVs missing the strict quality/context columns, so legacy
   constant-`OK` artifacts cannot authorize current option work.
   Tastytrade contract-level IV integration now proves exact contract identity
   and retains separate provider-observation and local-retrieval timestamps
   before its IV can authorize live risk outputs. Yfinance IV remains
   diagnostic/unknown; chain IV and then fresh, labelled realized volatility
   remain fallbacks when an exact fresh Tastytrade observation is unavailable.
   Historical IV, ledger hardening, and the remaining integrated packages are
   still open and deferred.

   **Wheel v2 update — 2026-07-24:** the Wheel artifact now declares schema
   version and `CURRENT_CONTEXT_ONLY` run mode, archives every invocation in a
   creation-only run directory with a reproducibility manifest and source
   digests, and reports disjoint-window terminal-ITM/touch frequencies alongside
   the overlapping estimates. The API deliberately translates legacy v1 reports
   but rejects unsupported future versions; the live chain stage requires v2.
   The UI shows quality, hides non-`OK` rows by default, exposes disjoint sample
   counts/diagnostics, and removes assignment-probability wording. This advances
   O2/Gate A but does not supply point-in-time universe/event history or complete
   Gate C/O7 verification.

   **Prospective quote update — 2026-07-24:** premium schema version 3 now
   enriches exact Yahoo-discovered contracts from Tastytrade DXLink Quote events.
   The older provider bid/ask timestamp governs freshness; side timestamps,
   sizes, event/retrieval times, streamer identity, provider coverage, quality
   counts, source hashes, and partial errors are archived immutably. Missing
   contracts retain Yahoo only as diagnostic data and remain entry-ineligible.
   A full live proof collected all 575 requested contracts across 17 symbols in
   two bounded batches; because it ran outside RTH and the snapshot supplied no
   side-update timestamps, every row correctly remained unavailable for entry.
   This starts prospective R2 data
   collection; it does not yet provide a meaningful time series or Gate D.

   **Expiry-tolerance decision — 2026-07-24:** the 7-DTE target remains exact.
   The 37-DTE target now accepts a listed expiry within ±9 calendar days
   (28–46 DTE), so a valid 28-DTE contract can be collected rather than being
   silently discarded. Every row continues to record requested DTE, actual DTE,
   deviation, expiry, and the actual-expiry-derived context.

   **Completed — quote-archive observability / O7a (2026-07-25):** the Wheel
   page now has a second **Option Quotes** tab. It reads only the immutable run
   named by `data/premiums/latest.json`; opening, filtering, or refreshing this
   tab never calls Tastytrade. `stock-app/app/premium_archive.py` validates the
   latest pointer, canonical `runs/<run_id>/premiums.csv` and `run_meta.json`,
   and schema-v3 columns before `GET /optionQuotes` returns typed rows and
   run/coverage summaries. Missing archives are a normal empty state; malformed
   pointers, incomplete runs, and unsupported schemas fail closed. The tab shows
   run metadata, requested/actual DTE, bid/ask, source/status, side timestamps,
   quality reasons, liquidity, and eligibility; it explicitly distinguishes an
   `ENTRY` analysis view from `entry_eligible`. A successful `Collect Option
   Quotes` switches to this tab and reloads the archive. Backend synthetic-v3,
   missing-pointer, and bad-pointer/schema regression coverage is in
   `stock-app/tests/test_premium_quotes.py`.

   **Completed — O7b deterministic archive verification (2026-07-25):**
   `utilities/options/verify_premiums.py` rebuilds ENTRY and ROLL_EXIT views from an
   immutable v3 `premiums.csv`, then compares headers, row order, and every
   value against the immutable and dated compatibility reports. It verifies the
   manifest’s artifact hash, run metadata, and recorded source hashes too.
   `./commands.sh verify-premiums [run-id]` runs the check locally (omitting the
   ID uses `latest.json`). `write_chain_artifacts` invokes it after writing the
   run/views but before promoting the run to `latest.json`, so an inconsistent
   collection fails closed. Fixtures cover a valid archive, changed derived
   view, and changed manifest hash in `utilities/tests/test_verify_premiums.py`.

   **Completed — Wheel controls scope quote collection (2026-07-25):** the
   Wheel page's Horizon, OTM cushion, and filter controls now narrow what
   `Collect Option Quotes` actually collects, behind a default-on **Scope
   collection to this view** toggle; turning it off runs the full configured
   sweep. Scope is **subtractive only** — it can never widen a run, re-admit a
   gated symbol, or relax a quality gate, so a scoped archive is always a subset
   of the unscoped run.
   - *Horizon* selects a subset of the configured `chain_dtes`. The wheel table
     offers 7/14/30/37/45 but chain collection is configured for 7 and 37, so a
     scoped run at 14/30/45 fails closed with the configured list named. The UI
     disables the button and states the reason; `chains.py` rejects it before
     any provider request (`normalize_collection_scope`).
   - *OTM cushion* becomes a minimum-OTM floor applied to `ENTRY` strikes on top
     of the configured sigma band, never instead of it. `ROLL_EXIT` strikes are
     ITM by definition and are unaffected. A cushion wider than the whole band
     legitimately yields no entry candidate; that is recorded per symbol in
     `collection_scope.symbols_without_entry_strikes` rather than silently
     falling back to a nearer strike. Cushioned entry rows declare
     `selection_policy = {SIDE}_OTM_SIGMA_BAND_MIN_OTM`. The cushion bound is
     inclusive on both sides within a relative epsilon, so binary rounding
     cannot drop a strike sitting exactly on the boundary.
   - *Filters* (symbol text, ETFs-only, hide-bearish, quality) scope the symbol
     set. The symbol list is applied inside `build_underlying_pool` **before**
     the RV rank cap, so an explicitly requested symbol is ranked within the
     requested set instead of being displaced by higher-RV symbols the user did
     not ask for; a symbol still missing failed a real gate and is reported in
     `collection_scope.symbols_not_in_pool`. The UI omits the symbol list when
     more than 100 rows are visible — an unfiltered table runs to four figures,
     which would overflow the URL and narrow nothing the pool cap does not
     already bound — and says so.
   - Scoped runs promote to `latest.json` normally (user decision 2026-07-25).
     The accepted scope is recorded in the run manifest's `collection_scope`
     block and rendered as a warning banner in the Option Quotes tab, so a
     partial archive is never read as a full sweep. Archives written before this
     change report no scope at all rather than claiming either.
   - CLI: `./commands.sh chains [--horizon-dte 7,37] [--symbols AAPL,MSFT]
     [--min-otm-pct 5]`. `GET /runChains` accepts `horizonDte`, `symbols`, and
     `minOtmPct`, validates them, and passes them to the shell as positional
     parameters rather than interpolating them into the command string.

   **Next task — prospective RTH quote evidence:** collect and retain several
   ordinary RTH observations across calendar dates, then inspect their
   side-specific timestamps, freshness, quality distribution, and eligibility
   reasons in the Wheel Option Quotes tab. This is a data-collection/monitoring
   task, not a scheduled job or an options-edge claim.
2. **Resumed and implemented — separate sector-rotation ETF analysis
   (deferred 2026-07-24, resumed and built 2026-07-25).** The descriptive slice
   is complete: 5-/20-/63-session leadership, SPY-relative returns, cross-sector
   rank changes, pairwise ratios, and volume confirmation, isolated from the
   stock earnings-catalyst scan and labelled a rotation proxy rather than
   measured fund flow. See the "Sector rotation from ETF price leadership"
   section above for the implementation, artifacts, and verification. The
   predictive gate is permanently closed for the 11-sector product, so the
   output stays descriptive market-regime context and must not be presented as
   predictive. The separately pre-registered legacy-nine historical study
   cannot lift that product gate.
3. **Completed — retirement options immutable transaction event ledger so closed
   contracts persist with realized P/L (proposed 2026-07-23; completed and
   confirmed against the live SnapTrade connection 2026-07-24).**

   **Requirement.** A closed retirement options contract must keep showing in the
   Trade Groups view with its realized P/L, and the user marks the group
   `ARCHIVED` when done with it — identical to the Tastytrade `TRADING` tab
   behavior. Before this implementation, the retirement view derived group P/L
   from *current* SnapTrade positions only (`retirement_options._build_groups` over the
   `snaptrade_holdings.csv` legs: `net_cash_flow`/`total_pnl` come from
   `_cost_basis`/`_open_pnl` of live legs). The editable `ACTIVE`/`ARCHIVED`
   status already exists — but when a contract closes it leaves the positions
   feed, the group has no legs, and the whole row (and its realized P/L)
   disappeared before the user could review or archive it. The implemented path
   derives group P/L from a retained event ledger instead of live positions,
   exactly as the Tastytrade tab does with `options_activity.csv`.

   **Target behavior (mirror the Tastytrade tab).**
   - A group persists once it has any events, regardless of whether a live
     position remains. `position_status` is derived `OPEN`/`FLAT` from the net
     event quantity; a fully-closed underlying stays as a `FLAT` group showing
     realized P/L rather than vanishing.
   - `status` stays user-editable `ACTIVE`/`ARCHIVED` (already implemented,
     `PATCH` validates the same vocabulary). Archiving is a manual, reversible
     metadata edit — never automatic on close.
   - Realized P/L for a flat group is `Σ net_value`; an open group reports
     `total_pnl = cash_flow + open_market_value` and is labelled `INDICATIVE`
     (marks lack a defensible observation timestamp), matching
     `options_activity._group_summary`.
   - Keep the greeks/betas purge to current contracts unchanged — those are live
     risk-analytics inputs, never P/L inputs.

   **Data source (confirmed 2026-07-24 against the live connection).** Read
   `account_information.get_account_activities` (`GET /accounts/{accountId}/
   activities`; per-account, `startDate`/`endDate` window, `offset`/`limit`
   paging). Of three linked Fidelity accounts, only **BrokerageLink** (id
   `8ba4e269-c2a1-4582-a4e7-06f95e44a679`) has option activity; 252 activities for
   a Jan 1 → today window returned in one call. Each option row carries structured
   fields — parse these, not the free-text description:
   - `option_symbol.underlying_symbol.symbol`, `.option_type` (PUT/CALL),
     `.strike_price`, `.expiration_date`, `.ticker` (OCC), plus an activity-level
     `option_type` action (`SELL_TO_OPEN` / `BUY_TO_CLOSE`).
   - `amount` is signed net cash flow **including fees** (credit +, debit −):
     `SELL_TO_OPEN` → `type=SELL, amount=+370.34, units=-1.0, fee=0.66`;
     `BUY_TO_CLOSE` → `type=BUY, amount=<negative>, units=+1.0`. So
     `realized_pnl = Σ amount` mirrors Tastytrade `net_value`.

   **Design.**
   - Add `retirement_option_events.csv` (config accessor + `SFP_*` override),
     immutable and append-only, keyed by the SnapTrade activity `id` (uuid).
   - A new sync reads `get_account_activities` over a full window (Jan 1 → today)
     and upserts by activity id, never deleting — idempotent, and it self-heals
     batches that post late. Map structured fields into `underlying`,
     `option_type`, `strike`, `expiry`, `action`, `units`, `net_value` (=`amount`);
     retain the OCC `ticker` and `id`. At design time the holdings `sync()` did
     not call this endpoint; the implemented flow now invokes the event sync
     best-effort from the manual Fidelity holdings sync. The existing
     greeks/betas purge remains untouched.
   - Build groups and P/L from events like `options_activity._group_summary`
     rather than from live legs, producing `net_cash_flow`, `realized_pnl`,
     `total_pnl`, `position_status`, and `pnl_completeness`.

   **Runtime behavior to handle: transactions lag positions.** SnapTrade serves
   Fidelity positions real-time but transactions on a slower cadence, so there is
   a window where a contract has already left the positions feed but its closing
   `BUY_TO_CLOSE` has not yet posted to activities (observed in practice: the
   position leaves the feed while the close is still absent hours later, and a
   manual `refresh_brokerage_authorization` is rejected on the non-real-time
   plan tier — HTTP 403 code 1141). During that window the ledger sees only the
   `SELL_TO_OPEN`, so the
   group reads `OPEN`/`INDICATIVE` and must not report a realized figure; it flips
   to `FLAT`/realized once the close event lands on the next transaction sync.
   This is normal operation, not a build blocker.

   **Implemented — backend (2026-07-24).** `retirement_option_events.csv`
   (`config.retirement_option_events_csv`, `SFP_RETIREMENT_OPTION_EVENTS`), a
   `snaptrade_service.fetch_activities` paginated provider,
   `retirement_options.sync_events` (full-window upsert by activity id), and
   event-derived groups in `snapshot()` are in place. The holdings-sync flow
   invokes that internal function best-effort. Groups now carry
   `position_status`, `realized_pnl`, and `pnl_completeness`; a closed underlying
   stays `FLAT`/`COMPLETE` with realized P/L, and the existing `ACTIVE`/`ARCHIVED`
   status edit archives it. Because the broker feed can be incomplete or lag,
   `_build_event_groups` reconciles events with live legs per underlying: a
   held leg whose opening event is missing is valued from the holdings cost
   basis/mark, and an event residual with no live leg reads `UNAVAILABLE`.
   Verified live: closed GOOG/INTC/PLTR groups persist with realized P/L, MSFT
   reads `UNAVAILABLE` pending its close, open legs read `INDICATIVE`.

   The Angular retirement Options tab now renders `position_status` (OPEN/FLAT),
   a realized/indicative/awaiting-close label, and the `ACTIVE`/`ARCHIVED`
   control, and holdings sync auto-fills the event ledger (`sync()` best-effort
   calls `sync_events`). The snapshot also returns the per-group `events`, and a
   **Details** button on each Trade Group opens a broker-events drill-down
   (Date, Contract, Action, Qty, Price, Net cash, Fees) mirroring the Options
   Trading Ledger — e.g. a closed PLTR group shows its full 8-event roll history.
   144 stock-app tests pass. **Closed by user decision (2026-07-24):** no
   scheduled/periodic `sync_events` is needed. The manual **Sync from Fidelity**
   action already refreshes the event ledger, and the user will run it again when
   a delayed closing transaction needs to be collected.

   **Known gap:** a true `EXPIRATION`/`ASSIGNMENT` activity shape has not been
   observed yet (none occurred in 2026; `SELL_TO_OPEN` and `BUY_TO_CLOSE` are
   confirmed). They are handled generically by structured action and `net_value`;
   confirm the exact shape whenever the first one posts.
