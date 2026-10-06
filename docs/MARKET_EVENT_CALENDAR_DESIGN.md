# Market Event Risk Calendar design

**Status:** Milestones 1 and 2 are accepted. Milestone 3 implementation is in
review.

**Primary user:** a local smallFish user planning SPY and QQQ 0-DTE trades

**Product boundary:** research and risk-awareness tooling. The calendar does
not predict market direction, recommend a trade, or claim that an event creates
a profitable options setup.

## 1. Objective

Build a provider-neutral market-event calendar that a user can scan on Sunday
for the next 31 calendar days. Its first responsibility is to identify known
scheduled events that may make a SPY or QQQ 0-DTE trading day materially
different from an ordinary session. Its second responsibility is to identify
events concentrated in another market or sector and name the locally cached
ETFs through which that exposure can be observed.

The scanner must answer:

1. Which coming trading sessions contain known scheduled event risk?
2. When does each event occur in Eastern and Pacific time?
3. Does it occur before entry, during a strategy's expected exposure, or after
   its planned exit?
4. Why could it help or hurt each configured strategy?
5. Is the event primarily relevant to SPY/QQQ, or to another sector or asset
   class?
6. Which ETFs already present in the smallFish universe and price cache are
   reasonable exposure proxies?
7. How fresh and complete is each source's coverage?

The system covers known events only. It must say explicitly that a forward
scan cannot anticipate geopolitical shocks, emergency central-bank actions,
unexpected company announcements, or other unscheduled news.

## 2. Product priorities

### 2.1 Priority 1: SPY and QQQ 0-DTE risk

The primary result is a ranked list of broad-index risk days. Candidate events
include:

- FOMC decisions, statements, projections, minutes, and Chair press
  conferences;
- material scheduled Federal Reserve speeches and the Beige Book;
- CPI, PPI, PCE, GDP, Employment Situation, JOLTS, jobless claims, ECI,
  retail sales, durable goods, industrial production, ISM, and other major
  growth or inflation releases;
- Treasury refunding announcements and material auctions;
- earnings from companies with material SPY or QQQ index exposure; and
- clusters of individually moderate events whose time windows overlap.

The calendar reports event and strategy risk, not a pre-release bullish or
bearish forecast.

### 2.2 Priority 2: sector and asset-class risk

The secondary result contains events whose most direct exposure is outside the
broad equity indexes, including:

- EIA petroleum and natural-gas releases;
- WASDE, Crop Production, Grain Stocks, Prospective Plantings, Acreage, and
  selected Export Sales releases;
- housing, retail, defense, metals, and other industry-sensitive releases; and
- individual-company earnings without material broad-index exposure.

These events remain visible without displacing the Priority 1 risk days. When
a Priority 1 provider also publishes useful secondary events, its adapter may
ingest them in the same run and classify them as secondary rather than discard
them.

## 3. Configured strategy profiles

Risk policy is evaluated against versioned strategy profiles. Raw event facts
must never contain trading rules.

### 3.1 Strategy 1: `short-premium-20d-0dte`

- sell a call or put around 20 delta near the market open;
- target a 25-35% profit;
- typical exposure is approximately two hours; and
- losing positions should be closed by noon rather than carried into the
  close.

The risk engine must distinguish a pre-open release from an event during the
opening-to-noon exposure. Elevated premium is a potential benefit, while a
large realized move, volatility expansion, or event during the holding window
is a potential risk. Elevated premium alone must not be presented as an edge.

### 3.2 Strategy 2: `long-premium-20d-0-or-1dte`

- buy a 20-delta call or put at or near the market open;
- use 0 or 1 DTE;
- use predefined profit-taking and stop-loss orders; and
- an adjustment may later be tested, but is not assumed by the calendar.

An event can create movement opportunity while still being harmful through an
incorrect directional choice, path dependency, event premium, or post-release
implied-volatility contraction. If a release occurs before entry, the initial
move may already have occurred. The calendar must describe both sides.

### 3.3 Strategy 3: `long-wings-repeated-0dte-premium`

- hold approximately 30-DTE OTM call and put protection on SPY or QQQ;
- sell daily 0-DTE call and put premium near expected-move boundaries; and
- retain the explicit warning that different expirations mean maximum loss is
  not simply strike width minus credit.

The long wings may help during a tail move or volatility expansion, but the
daily short legs can still be overwhelmed. The net result is commonly mixed
and must not be reduced to a guarantee that protection makes an event day safe.

### 3.4 Timing configuration

Strategy timing belongs in configuration rather than source adapters. Initial
configuration should represent:

```yaml
strategies:
  short-premium-20d-0dte:
    entry_time_et: "09:30"
    typical_exit_time_et: "11:30"
    hard_exit_time_et: "12:00"
  long-premium-20d-0-or-1dte:
    entry_time_et: "09:30"
    exposure_end_et: "16:00"  # conservative until the owner sets a tighter rule
  long-wings-repeated-0dte-premium:
    short_leg_entry_time_et: "09:30"
    short_leg_exposure_end_et: "16:00"  # conservative until explicitly changed
    long_wing_horizon_days: 30
```

Times are interpreted using `America/New_York` and an exchange calendar. They
are never fixed UTC offsets. Strategy timing changes create a new policy
version and do not rewrite prior scan results.

## 4. Assessment semantics

### 4.1 No unsupported directional prediction

Before a release, the system may state that an event could increase repricing
risk, produce a gap, increase realized movement, or affect implied volatility.
It must not label CPI, an auction, or an inventory release as inherently
bullish or bearish.

After a release, an official actual-versus-consensus surprise may be displayed
only when both observations describe the same metric, unit, scale, seasonal
adjustment, and reference period. Even then, the surprise is evidence, not a
deterministic price prediction.

### 4.2 Strategy assessment

Each event receives an explainable result per strategy:

```typescript
interface StrategyEventAssessment {
  strategyId: string;
  assessment:
    | "avoid"
    | "high_risk"
    | "mixed_opportunity"
    | "conditional"
    | "low_direct_relevance";
  timingRelationship:
    | "before_entry"
    | "during_typical_hold"
    | "after_typical_exit_before_hard_exit"
    | "after_planned_exit"
    | "all_day_exposure";
  potentialBenefits: string[];
  potentialRisks: string[];
  ruleIds: string[];
  policyVersion: string;
}
```

Benefits and risks remain separate. A single signed score would conceal the
fact that the same event can increase movement opportunity and option cost at
the same time.

### 4.3 Event importance and portfolio relevance

Importance is a configurable general assessment:

| Score | Label |
|---|---|
| 5 | Extreme |
| 4 | High |
| 3 | Moderate |
| 2 | Low |
| 1 | Informational |

Portfolio relevance is independent:

```text
DIRECT_BROAD_INDEX
INDIRECT_BROAD_INDEX
SECTOR_OR_COMMODITY
SINGLE_STOCK
LOW_RELEVANCE
```

The strategy result is produced by named rules using:

- event type and base importance;
- SPY/QQQ relevance;
- overlap with configured exposure times;
- whether the event is pre-open, during RTH, or after close;
- overlapping event clusters;
- schedule certainty;
- source freshness and coverage; and
- explicit, versioned overrides.

Do not introduce an opaque aggregate score. The result must retain the rule
IDs and plain-language reasons that produced it.

Dynamic escalation such as "major Treasury auction under stressed market
conditions" is deferred until a causal, dated market-state input is defined.
Milestone 1 uses static policy plus explicit reviewed overrides.

## 5. Canonical event model

The proposed flat event interface is insufficient because one occurrence can
contain multiple released measurements. CPI has headline and core measures;
Employment Situation has payrolls, unemployment, and earnings; an EIA release
has crude, gasoline, and distillate figures.

### 5.1 Event occurrence

```typescript
interface MarketEventOccurrence {
  id: string;
  canonicalKey: string;
  schemaVersion: number;

  title: string;
  eventType: EventType;
  category: EventCategory;
  country: string;
  referencePeriod?: string;

  scheduledAtUtc?: string;
  originalTimezone?: string;
  timePrecision: "exact" | "date_only" | "estimated" | "unknown";
  scheduleStatus: "confirmed" | "tentative" | "estimated" | "unscheduled";
  lifecycleStatus: "scheduled" | "released" | "cancelled" | "rescheduled";

  affectedAssetClasses: AssetClass[];
  affectedInstruments: string[];
  etfExposures: EventEtfExposure[];

  sources: EventSourceReference[];
  measurements: EventMeasurement[];
  importance: ImportanceAssessment;
  strategyAssessments: StrategyEventAssessment[];
  relatedEventIds: string[];
}
```

Store UTC and the source timezone. Eastern and Pacific display strings are
derived at the API boundary using `zoneinfo`; they are not persisted as
duplicate timestamps.

Schedule status and lifecycle status are separate. An emergency announcement
can be both `unscheduled` and `released`.

### 5.2 Measurements

```typescript
interface EventMeasurement {
  metric: string;
  label: string;
  value?: string;
  numericValue?: string;
  unit?: string;
  scale?: string;
  seasonalAdjustment?: string;
  valueKind:
    | "previous"
    | "revised_previous"
    | "consensus"
    | "forecast"
    | "actual";
  provider?: string;
  observedAtUtc?: string;
}
```

Preserve the lossless source representation. Numeric decimals are stored as
text, not SQLite binary floating point. Missing values remain unavailable and
must never render as zero.

Official sources generally do not publish market consensus. Consensus remains
unavailable until a provider with appropriate storage and display rights is
configured.

## 6. ETF exposure mapping

### 6.1 Contract

```typescript
interface EventEtfExposure {
  symbol: string;
  relationship:
    | "direct_underlying"
    | "sector_equities"
    | "industry_equities"
    | "rate_sensitive"
    | "input_cost_exposure"
    | "defensive_or_hedge_proxy";
  relevance: "primary" | "secondary" | "indirect";
  impactChannel: string;
  priceDataState: "current" | "stale" | "missing";
  latestPriceSession?: string;
}
```

Relationships matter. `USO` is direct crude exposure, `XLE` is energy-equity
exposure, and `JETS` has indirect fuel-cost exposure. They must not appear as
equivalent instruments or share an implied direction.

### 6.2 Configuration and validation

Mappings belong in a new versioned ETF-exposure YAML configuration under
`utilities/config/`, not inside provider parsers. Example:

```yaml
eia_petroleum_status:
  exposures:
    - {symbol: USO, relationship: direct_underlying, relevance: primary,
       channel: "Crude-oil price exposure"}
    - {symbol: DBO, relationship: direct_underlying, relevance: primary,
       channel: "Crude-oil futures exposure"}
    - {symbol: XLE, relationship: sector_equities, relevance: primary,
       channel: "Large energy producers"}
    - {symbol: XOP, relationship: industry_equities, relevance: primary,
       channel: "Oil and gas exploration and production"}
    - {symbol: JETS, relationship: input_cost_exposure, relevance: indirect,
       channel: "Jet fuel is a material airline operating cost"}
```

At configuration load time:

1. resolve every symbol against the generated smallFish universe;
2. reject duplicate event/symbol mappings;
3. attach price-cache freshness at scan time;
4. display the ETF even when price data are stale, but label it `stale`;
5. omit cached analytics when data are missing; and
6. fail validation for a mapping to an unknown symbol rather than silently
   inventing coverage.

The current starter universe already includes the eleven sector SPDRs plus
semiconductor, biotech, real-estate, banking, energy, metals, housing, retail,
transportation, defense, agriculture, rates, precious-metal, commodity,
currency, and volatility ETFs. Initial mappings should reuse these symbols.

Illustrative mappings:

| Event family | Primary ETF candidates | Secondary or indirect candidates |
|---|---|---|
| EIA petroleum | USO, DBO, DBE, XLE, XOP | JETS, IYT |
| Natural-gas storage | UNG | XLE, XOP |
| WASDE / crop reports | DBA, MOO | DBC, PDBC, XLP |
| FOMC / Treasury auctions | TLT, IEF, SHY | TIP, XLF, XLRE, XLK |
| Housing | ITB | XLRE, IYR, VNQ, XLF |
| Retail sales | XRT, XLY, XLP | SPY |
| Semiconductor-specific | SMH, SOXX | XLK, QQQ |
| Gold-sensitive | GLD, IAU | GDX, GDXJ |
| Industrial metals | DBB, XME | XLB, XLI |

These are exposure channels, not predictions. Every production mapping needs a
short reviewed rationale and synthetic test coverage.

### 6.3 Later descriptive analysis

The price cache may later support a non-persistent event-context panel such as
prior 5-session return, 20-session realized volatility, or post-event movement.
Those calculations must be labeled descriptive, validated for freshness, and
must not be used to claim that the event caused the move. They are outside the
first implementation milestone.

## 7. Provider and source plan

Prefer official publishers where practical. Schedule acquisition and released
value acquisition are separate capabilities; a provider may support one
without the other.

| Provider | Schedule surface | Released values/results | Initial priority |
|---|---|---|---|
| Federal Reserve | official calendar and FOMC pages | release pages and RSS | 1 |
| BLS | official iCalendar | Public Data API | 1 |
| Treasury | tentative/upcoming XML | Auction Query JSON/CSV/XML | 1 |
| BEA | official iCalendar | BEA API | 1 |
| Census | official release calendar | Economic Indicators APIs | 1 |
| DOL claims | official weekly publication | official release data | 1 |
| Earnings | provider abstraction; Finnhub initially | provider dependent | 1 |
| EIA | official schedule including holiday exceptions | EIA API/official CSV | 2 |
| USDA/NASS/FAS | official calendars | ESMIS and agency APIs/files | 2 |

ISM, University of Michigan sentiment, Conference Board confidence, and NAR
existing-home sales are privately published. Calendar or value ingestion needs
a terms and licensing review before implementation. Their absence must appear
as a coverage limitation rather than silently implying a complete calendar.

API keys and terms:

- BLS v1 has limited keyless access; BLS v2 registration increases limits.
- BEA, EIA, USDA QuickStats/FAS, and Finnhub may require keys.
- EIA requires source attribution and compliance with its API terms.
- Finnhub storage, display, estimates, and redistribution rights depend on the
  selected plan.
- Government works are generally public-domain in the United States, but
  agency API terms, trademarks, third-party material, and attribution still
  apply.

No raw provider data or API key is committed to the repository.

## 8. Architecture and dependency boundary

The calendar is a batch-owned artifact. FastAPI never fans out to providers
while serving a page.

```text
official/provider sources
        |
        v
services/market_events/          raw HTTP transport only
        |
        v
utilities/market_calendar/       parse, normalize, dedupe, validate, score
        |
        v
$SFP_DATA_DIR/market_calendar/calendar.sqlite
        |
        v
stock-app/                       read-only queries and REST
        |
        v
stock-app-ui/                    Event Risk under Research
```

Proposed source layout:

```text
models/
  market_events.py

services/
  market_events/
    http.py
    fed.py
    bls.py
    treasury.py
    bea.py
    census.py
    dol.py
    eia.py
    usda.py
    earnings.py

utilities/
  market_calendar/
    __main__.py
    sync.py
    database.py
    normalization.py
    deduplication.py
    importance.py
    risk.py
    migrations/
      001_initial.sql
    providers/
      base.py
      fed.py
      bls.py
      treasury.py
      bea.py
      census.py
      dol.py
      eia.py
      usda.py
      earnings.py
  config/
    market_calendar_sources.yaml
    market_calendar_importance.yaml
    market_calendar_risk.yaml
    market_calendar_strategies.yaml
    market_calendar_etf_exposures.yaml

stock-app/
  app/
    market_events_read.py
    routers/market_events.py

stock-app-ui/
  src/app/
    market-calendar/
    api/market-calendar.service.ts
    model/market-event.ts
```

Rules:

- `models/` remains standard-library-only.
- Raw transport under `services/` imports no application configuration,
  persistence, FastAPI, pandas, or NumPy.
- `utilities/` is the only calendar-database writer.
- `stock-app/` opens the database read-only and never imports `utilities/` or
  `studies/`.
- Provider failures preserve the last known-good database state.
- Network access is always injected in tests; automated tests open no socket.

## 9. Persistence

Use SQLite at:

```text
$SFP_DATA_DIR/market_calendar/calendar.sqlite
```

Allow an advanced `SFP_MARKET_CALENDAR_DB` override. Use WAL, foreign keys,
strict transactions, a busy timeout, and local-user-only file permissions.

Recommended tables:

```text
events
event_schedule_history
event_relationships
event_measurements
event_assets
event_etf_exposures
event_source_facts
ingestion_runs
source_sync_state
importance_assessments
strategy_assessments
schema_migrations
```

`event_source_facts` records provider, source record ID, credential-free source
URL, fetched time, ETag, Last-Modified, parser version, payload SHA-256, and
field provenance. Retain raw response bodies only when provider terms permit
it; a hash plus normalized facts is the default.

Each provider ingestion stages and validates its observations, then merges them
inside one transaction. Failure rolls back and retains the prior successful
state. `source_sync_state` records coverage start/end, last attempt, last
success, result count, parser version, and a safe error category. Provider
exception details remain in sanitized local logs, never API responses.

## 10. Identity, revisions, and deduplication

Do not derive the stable ID from the scheduled timestamp because a reschedule
would create a false new event. Example canonical keys:

```text
US:BLS:CPI:2026-09
US:BLS:EMPLOYMENT_SITUATION:2026-09
US:FED:FOMC_DECISION:2026-10-28  # first-seen seed retained by reconciliation
US:TREASURY:AUCTION:10Y:<CUSIP>
US:EIA:WPSR:2026-10-02
US:EARNINGS:NVDA:2027-FY-Q3
```

Deduplication order:

1. exact official source identifier;
2. canonical event type, country, subject, and reference period;
3. scheduled time as corroboration, not identity;
4. field-level source precedence; and
5. review-required state when two authoritative observations conflict.

Every schedule change is appended to `event_schedule_history`. FOMC statement
and press conference, Treasury announcement/auction/result, and earnings
release/call remain separate related occurrences because their risk windows
differ. The Federal Reserve feed has no stable record id for FOMC meetings,
their press conferences, or G.17 releases. The first-seen date/month key is
therefore persisted as a seed rather than recomputed as identity. On later
feeds, unchanged records are anchored first and unique remaining moves retain
their persisted seeds. Pure future insertions and pure deletions are accepted
after unchanged occurrences anchor. Mixed count changes and moves, and
ambiguous many-record changes, fail closed because a move plus an insertion
cannot be distinguished safely from an unchanged occurrence plus a new one.
Statement and press-conference reconciliation remains paired. This process
does not infer an economic reference period. Parser version changes must
migrate the normalized snapshot and event rows in one transaction; version 4
uses the full reconciled snapshot to merge and remove an already-diverged
cancelled date/month generation, including occurrences outside the current
scan horizon, so the API exposes one occurrence with the chosen identity and
complete schedule history.

Named strategy-exposure clusters require an exact event time within the
configured inclusive interval from strategy entry through hard exit. Pre-entry
and post-exit events do not use that relationship name, and legacy
`same_session_cluster` rows are removed during rematerialization.

## 11. Existing earnings compatibility

The current `data/events.csv`, `events_meta.json`, and `events_history/`
contract remains unchanged during the first calendar milestones. Wheel,
Momentum, and Pre-Earnings Momentum already depend on its freshness and
fail-closed semantics.

The existing Finnhub fetch also writes a private calendar-only normalized
sidecar with fiscal-period identity. This does not replace or widen the legacy
CSV artifact and does not add a second provider fetch. The sidecar fails closed
for calendar use and does not claim coverage if any in-horizon provider row
lacks a valid integral identity token: booleans and fractional values are
rejected, fiscal years must have four digits, and quarters must be 1 through 4.
Calendar-capable freshness also requires a schema-2 sidecar whose
`legacyArtifactSha256` binds it to the exact legacy CSV bytes, with complete
identity and sufficient coverage. `schemaVersion` must equal integer `2`,
`identityComplete` must be the boolean `true`, and `identityFailureCount` must
be the integer `0`; coercible strings, booleans, and fractional values are not
accepted. A fresh legacy cache remains valid for legacy consumers when that
sidecar is missing or outdated; without a Finnhub key the refresh prerequisite
reports this limited capability explicitly instead of claiming calendar
freshness. A later migration may derive a
legacy-compatible earnings projection from the canonical database only after
all consumers, freshness behavior, failure behavior, and historical workflows
have been audited. No duplicate active fetch implementation should remain after
such a migration.

## 12. Sync workflow

Add an explicit command rather than provider I/O on page load:

```text
./commands.sh market-calendar
```

Default behavior:

- scan today through today plus 31 calendar days;
- use cached source observations when still fresh and sufficiently covered;
- conditionally refresh stale or incomplete sources;
- publish source-by-source diagnostics;
- preserve the previous successful data for a source when refresh fails; and
- exit nonzero only when the primary broad-market calendar cannot be safely
  produced.

Freshness is evaluated from one stored UTC success instant through an inclusive
timestamp window in every ingestion path and the API; future success timestamps
are invalid rather than fresh. A date-only Finnhub generation is conservatively
interpreted as midnight Eastern: calendar capability is fresh for the
configured 24-hour interval and stale immediately after it. A larger legacy
`max_age_days` override applies only to the legacy CSV capability. Legacy
consumers may retain their documented calendar-day behavior, but that state is
reported separately and does not claim calendar capability.

An explicit UI action may invoke the allowlisted command through a job endpoint
later. It must show ready, running, success, and failure states, disable
duplicate submission, and continue showing the last successful scan with a
stale warning if refresh fails.

Do not add an implicit scheduler in the first milestone. Sunday execution can
be manual until a separate scheduling decision is made.

## 13. REST contract

Initial endpoints:

```text
GET /api/market-events
GET /api/market-events/{event_id}
GET /api/market-events/daily-summary
GET /api/market-events/sources
```

Supported query parameters:

```text
from
to
timezone
categories
eventTypes
minImportance
portfolioRelevance
assets
tickers
scheduleStatus
```

The collection response includes:

- scan generation time and requested horizon;
- source coverage and freshness;
- grouped trading dates;
- structured daily assessment;
- event occurrences with ET/PT display fields;
- strategy assessments; and
- ETF exposure mappings with price-cache freshness.

The API must distinguish:

- a successfully covered day with no matching events;
- a stale or insufficiently covered source;
- an optional provider that is not configured; and
- a failed refresh retaining prior data.

## 14. UX contract

### 14.1 Navigation

Add a separate `Event Risk` route under the existing **Research** navigation
group. Do not put it under Strategies or Research Studies. Suggested route:

```text
/market-calendar
```

Follow `stock-app-ui/docs/UX_GUIDANCE.md`, the global tokens in
`src/styles.scss`, and the scan/filter/results workflow established by
Momentum. Reuse shared panels, banners, badges, table shells, buttons,
skeletons, tooltips, drawers, and focus behavior. Do not introduce a parallel
palette or overlay implementation.

### 14.2 Page hierarchy

The initial page contains:

1. page header with title, concise purpose, scan action, and snapshot chip;
2. source-coverage banner when any required source is stale or incomplete;
3. compact stat strip: horizon, primary risk days, high/extreme events, and
   secondary events;
4. view tabs: `Upcoming Risk Days` and `All Events`;
5. compact filters; and
6. grouped daily results.

`Upcoming Risk Days` is the default. It puts Priority 1 SPY/QQQ risk first and
shows secondary events in a separate subsection. `All Events` supports Today,
Tomorrow, This Week, and Month ranges without changing the primary ranking.

### 14.3 Daily card/table content

Each day begins with:

- date and market-session state;
- `EXTREME`, `HIGH`, `MODERATE`, or `ROUTINE` risk label;
- event count and clustered-risk warning; and
- a concise 0-DTE assessment.

Each event row shows:

- ET and PT;
- title, category, importance, and schedule certainty;
- broad-index relevance;
- previous, consensus, and actual values where trustworthy;
- affected asset classes;
- primary ETF chips; and
- the strongest strategy assessment.

Opening a row uses the shared drawer and reveals:

- all three strategy assessments;
- separate potential benefits and risks;
- timing relationship;
- full ETF exposure list grouped by relationship;
- source and freshness details;
- related events; and
- the non-prediction caveat.

Color is never the only signal. Do not use decorative emoji. Missing values
render as an em dash. On narrow screens, use readable cards or intentional
horizontal scrolling rather than compressing the table into illegibility.

### 14.4 Example primary result

```text
Wednesday, October 14                         EXTREME RISK

5:30 AM PT / 8:30 AM ET  Consumer Price Index
Direct SPY/QQQ relevance

Strategy 1: HIGH RISK / CONDITIONAL
Potential benefit: option premium may be elevated.
Potential risk: a large surprise can continue moving after the open.

Strategy 2: MIXED OPPORTUNITY
Potential benefit: continued directional movement.
Potential risk: the initial move occurs before entry and implied volatility
may contract after the release.

Strategy 3: HIGH RISK
The long wings provide some tail exposure, but the daily short legs remain
exposed to a large move.
```

### 14.5 Example secondary result

```text
7:30 AM PT / 10:30 AM ET  EIA Petroleum Status Report
Sector scope: Energy | Broad-index relevance: Secondary

Primary ETFs: USO, DBO, XLE, XOP
Indirect: JETS, IYT

Unexpected petroleum inventory changes can rapidly reprice crude and energy
equities. Direction is unknown before the release.
```

## 15. Milestones

### Milestone 1: foundation and one end-to-end provider

- standard-library domain contracts;
- SQLite migrations and repository;
- source freshness and ingestion-run diagnostics;
- importance, strategy, and ETF mapping configuration loaders;
- deterministic risk engine;
- BLS schedule ingestion through official iCalendar;
- one CPI occurrence including headline/core measurements where available;
- read-only FastAPI routes;
- `Event Risk` Research navigation and route;
- Upcoming Risk Days UI with Today/Week/Month range support; and
- complete synthetic backend and Angular coverage.

BLS CPI is the first vertical slice because it exercises official scheduling,
multi-measurement normalization, pre-open timing, extreme broad-index risk, all
three strategy explanations, and keyless low-volume data access.

### Milestone 2: primary broad-market coverage

- remaining required BLS releases;
- Federal Reserve;
- Treasury;
- BEA;
- Census;
- DOL weekly claims;
- earnings-provider abstraction and current Finnhub implementation;
- event clustering; and
- source coverage diagnostics for the full primary calendar.

Index-constituent membership may identify earnings relevance initially, but a
claim that one company has material index weight requires dated weight data and
provenance. Without it, show membership and avoid fabricating a weight.

### Milestone 3: secondary sector and asset coverage

- EIA petroleum and natural gas;
- USDA/NASS/WASDE;
- selected FAS export sales;
- reviewed ETF exposure mappings; and
- the secondary-event subsection and filters.

Implementation keeps all five schedule families optional so their outages do
not weaken accepted primary coverage. EIA weekly petroleum and natural-gas
rules apply the official holiday tables. Selected NASS reports come from the
annual ASB iCalendar; WASDE uses the same-day Crop Production entries in that
official calendar, matching USDA's published 2026 WASDE schedule. FAS Export
Sales uses the current official program page to prove the Friday-through-
Thursday reporting period, Thursday 8:30 a.m. ET publication rule, the
"unless a change is announced" caveat, and its Friday holiday shift. The
reporting-period end is the stable occurrence identity, so a holiday shift
does not create a new event. Schedule acquisition remains separate from released values:
EIA and FAS value capabilities are explicitly `not_configured` without an
approved key-backed ingestion path. ISM, Michigan, Conference Board, and NAR
remain an explicit terms-review gap.

### Deferred

- automatic scheduling;
- push notifications;
- live provider polling around release time;
- inferred directional market forecasts;
- strategy performance claims;
- historical event-day causal studies; and
- PostgreSQL deployment.

## 16. Verification and acceptance criteria

### 16.1 Domain and ingestion

- DST is proven with dates on both sides of US timezone transitions.
- A reschedule retains the stable event ID and appends schedule history.
- Duplicate official and aggregator observations merge deterministically with
  field-level provenance.
- Conflicting authoritative observations fail to review rather than silently
  choosing one.
- One occurrence supports multiple measurements without flattening them into
  duplicate events.
- Failed refreshes preserve the last successful source state.
- Secrets never appear in URLs, logs, API responses, fixtures, or database
  metadata.
- Tests use injected fixtures and make no network calls.

### 16.2 Policy

- Every assessment contains rule IDs, benefits, risks, and timing relationship.
- An event during Strategy 1's opening-to-noon window ranks above the same
  event after its hard exit, all else equal.
- Strategy 2 never treats movement opportunity as guaranteed profit.
- Strategy 3 always retains the cross-expiration loss warning.
- Missing consensus remains unavailable and produces no surprise value.
- No event receives a pre-release bullish/bearish market direction from the
  calendar itself.

### 16.3 ETF mappings

- Every configured ETF resolves to the generated universe.
- Duplicate mappings fail configuration validation.
- Current, stale, and missing price-cache states are distinct.
- Direct, sector, and indirect exposures are displayed separately.
- No ETF exposure mapping implies a guaranteed direction.

### 16.4 API and UI

- The API distinguishes covered-empty from unknown/stale.
- ET and PT render correctly from the same stored UTC value.
- Priority 1 events precede secondary events by default.
- Source caveats and strategy risk appear adjacent to the qualified result.
- The route is keyboard accessible and works at desktop and narrow widths.
- Running state suppresses stale-result interpretation while preserving the
  last successful snapshot for recovery.
- The route is loaded and visually inspected with representative synthetic
  data after both `npm run test:ci` and `npm run build` pass.

### 16.5 Repository checks

Run the targeted Python suites, Angular tests/build, route inspection,
`python3 tools/check_docs.py`, `python3 tools/scan_secrets.py`, and
`git diff --check`. Preserve unrelated worktree changes and keep the calendar
implementation in focused commits.

## 17. Implementation stop rules

Stop and request an owner decision rather than guessing when:

- a private source's storage or display rights are unclear;
- a required strategy exposure end time would materially change a risk result;
- two authoritative sources conflict without a documented precedence rule;
- a source cannot prove coverage for the requested horizon;
- broad-index earnings importance would require an unavailable constituent
  weight;
- implementation would require `stock-app/` to import `utilities/` or
  `studies/`; or
- migration would change the existing `events.csv` behavior or a frozen study.
