# Brokerage API and ledger refactor plan

**Status:** Core refactor complete; consumer-first legacy cleanup in progress.
All eight phases, including the synthetic lifecycle browser check, are complete
and committed. Both brokerage pages run on the provider-neutral API for their
Options and Option-Adjusted Basis tabs. This document is the source of truth
for implementation, cleanup boundaries, phase status, decisions, and
verification evidence.

## Resume here

Read this section first. The rest of the document describes the *design*; this
says where the work actually is.

**Done and committed** (oldest first):

| Commit | Phase |
|---|---|
| `10eec2c` | 1 — contract baseline and characterization |
| `d43a46c` | 2 — registry, adapters, canonical facts |
| `0a3740c` | 3 — common projections and additive read APIs |
| `a0094c5` | 4 — Symbol Ledger lifecycle, archives, mutation APIs |
| `b671845` | 5 — shared Symbol Ledger UI on both brokerage pages |
| `3917d77` | 5 fix — spent option leg reading as unreconciled |
| `754bb09` | 5 fix — reconcile share lots rather than declaring closed equity unknowable |
| `05f09b3` | 5 fix — retain adjusted basis after options close |
| `75db1d8` | 6 — history, archive, reset, and shared UI consolidation |
| `9deb69b` | 7 — common shell cutover and group/risk UI removal |
| `ae3fe96` | Adjusted-basis UI now uses the common projection |
| `dea8d5c` | 8 — full regression and synthetic lifecycle browser verification |
| `aab964e` | Lifecycle vocabulary and history-display follow-ups |
| `df4830a` | Aggregate adjusted basis across brokerage accounts |
| `5aeb6d8` | Start cleanup by removing the unused legacy Combined client |
| `b6e719f` | Symbol Ledger action follow-ups: conditional review/archival UI |

**Last broad verified baselines:** backend `stock-app/tests` 481 passing;
Angular `npm run test:ci` 69 passing; `npm run build` clean, with docs, secret,
and diff checks clean. Re-run the affected full suites before declaring a new
cleanup step complete.

**Current architecture:** all 14 settled `/api/brokerages` routes are served.
`/options` and `/retirement` share one shell, and every tab on it — Holdings,
Symbol Ledger, and Option-Adjusted Basis — is driven by the public brokerage id
alone through one Angular client. Group mutations are 410 tombstones and common
sync creates no group/membership artifacts. **No Angular code calls
`/brokerage-ledgers/*` any more**; the only route left on that prefix is
`/combined`, retained as the baseline the projection parity tests compare
against. The peak/adverse-move trend rule lives once in
`app/brokerages/trend.py`, fed by each provider's own extraction.

**Next action:** audit the legacy Retirement holdings surface —
`/retirement/holdings/sync`, `/retirement/holdings/gain-loss-snapshots`,
`/retirement/enrichment/{symbol}`, and `snaptrade_service.portfolio`. It has no
Angular consumer, but unlike the Holdings routes it is still a frozen contract
and `retirement_options.py` still calls `portfolio()` internally for a portfolio
total, so removal needs that caller re-pointed at the common projection first.
After that, the remaining group-only backend paths. Do not remove provider
ingestion, parsers, canonical facts, or read-only rollback artifacts merely
because they contain legacy terminology.

**Do not restart from Phase 1.** The kickoff prompt at the foot of this document
has been rewritten for resumption; use that, not the original start-from-scratch
version.

### Deviations from the original design, and why

These are deliberate and covered by tests. Do not "correct" them back without
reading the reasoning.

1. **`boundary_event_id` column on the archive artifact.** Not in the suggested
   field list, but the design requires a boundary that is deterministic when
   several events share a timestamp, which a date alone cannot be.
2. **`ActivityFact.quantity`.** Added in Phase 3. A lifecycle removal reports
   how much was removed without a signed delta; resolving that needs the size.
3. **A closing lifecycle event with nothing open resolves to zero movement**
   rather than being an unexplained delta. A removal cannot take more than is
   held, and the broker comparison still catches genuine disagreement. Without
   this, a contract whose opening trade predates the retained window reads as a
   permanent mismatch that no manual reconciliation can clear.
4. **Equity is reconciled against retained executions, with a cost-basis
   fallback.** A share lot that opened and closed inside retained history
   contributes its real cash. One whose opening trade predates the window reads
   `UNRECONCILED` — the gap a manual reconciliation row exists to close. Shares
   still held whose opening lots predate the window keep the broker's cost basis
   and read indicative, because that is missing history rather than a
   disagreement.
5. **Public provenance names the institution, not the connector.** `SNAPTRADE`
   must never reach a response; Angular would have an adapter name to branch on.
6. **The Phase 1 automated gate was amended** to include
   `test_brokerage_adapter_contract.py`.

### Current handoff boundary

The owner has confirmed that legacy brokerage routes are **not externally
consumable**, authorizing their removal after their in-repository consumers
migrate. This is not authorization to degrade behavior or remove brokerage
ingestion.

The Holdings routes met all three of their release conditions and were removed
on 2026-07-29: the common contract carries the editable enrichment, gain/loss
snapshot columns, and declining-trend state; `BrokerageHoldingsComponent`
consumes only the common brokerage service for both brokerages; and route,
component, and backend sweeps proved no caller remained. Apply the same three
conditions, in that order, to every remaining legacy surface. A route with no
Angular consumer is not thereby free to delete — check the backend callers too,
which is what still holds `snaptrade_service.portfolio` in place.

The recent Symbol Ledger UX decisions are deliberate: hide the `Needs review`
summary when its count is zero; show the archive button only for an eligible
period; otherwise show no archive card, blocker, or empty-state message.

## Handoff operating model

This work is organized into eight commit-sized phases. The implementation agent
is authorized to commit each completed phase after its automated gate passes.
Each commit must be focused on that phase and must include the corresponding
dashboard/progress-log update. Do not combine an unrelated refactor with a
phase.

Speed is intentional:

- Run the automated checks specified for every phase before committing.
- Do not open a browser or request manual UI review after every phase.
- Phase 5 is the first integrated UI checkpoint. After its automated checks and
  commit, pause and ask the owner to verify Trading and Retirement. Do not begin
  Phase 6 until the owner confirms that checkpoint is acceptable.
- After Phase 5 approval, proceed through Phases 6-7 without further manual
  browser pauses when their automated gates pass.
- Phase 8 performs the final full automated regression and browser/route
  verification. Fix discovered problems and rerun the affected gates before
  completing the phase.
- UI commits between the two manual checkpoints must be recorded as
  "automated checks passed; browser verification pending." A green build alone
  is never reported as final UI verification.
- Preserve pre-existing user changes in the worktree. Inspect `git status`
  before every phase and stage only that phase's intended files.
- Do not push or open a pull request unless the owner asks.

## Objective

Replace user-managed option trade groups with one durable ledger per symbol.
The ledger retains every imported stock and option event available to
smallFish, reports a running economic P/L, becomes Active when the symbol has
open exposure, and becomes Closed when the symbol is confidently flat.

Calendar years, option expirations, rolls, assignments, and later re-entry do
not create new groups. A user may deliberately reset a flat symbol to begin a
new current P/L period while preserving the completed period in a separate
archive.

This design covers the domain model, public API, reset behavior, compatibility,
and migration. It does not authorize implementation by itself.

## Product decisions

1. **The symbol is the ledger.** There is exactly one symbol ledger for a
   normalized underlying within a configured brokerage. The stable natural key
   is `(brokerage_id, symbol)`, for example `(fidelity, AAPL)`.
2. **Broker accounts remain visible components.** Multiple linked accounts may
   contribute to the same brokerage-level symbol ledger, but quantities,
   coverage, provenance, and reconciliation remain account-aware. Shares in
   one account never cover a short call in another.
3. **Broker events remain immutable facts.** A sync upserts them by provider
   identity. Editing metadata, closing a position, or resetting a symbol never
   rewrites, deletes, or physically moves an imported event.
4. **There is no event-to-group assignment.** An event belongs to the symbol
   identified by its normalized underlying. There are no ungrouped events and
   no second group for the same symbol.
5. **Lifecycle state is derived.** Users do not manually set Active or
   Closed. A new open position makes the symbol Active. A symbol becomes
   Closed only after all of its account-level equity and option positions are
   flat and the activity reconciles with the broker snapshot.
6. **Uncertainty fails toward Active.** If smallFish cannot prove that a symbol
   is flat because a close is delayed, activity is missing, or reconciliation
   fails, the symbol remains Active with a visible warning and unavailable P/L.
   It must not be presented as a completed archive.
7. **Normal reopening preserves the tally.** A new trade in a Closed symbol
   returns that same ledger to Active and continues the current-period and
   lifetime totals. It does not create a new group or implicit reset.
8. **Reset is explicit and flat-only.** A user can archive the completed current
   period only when the symbol is flat, reconciled, and complete. Reset creates
   an immutable logical boundary and starts an empty current period.
9. **P/L is economic, not tax-lot accounting.** smallFish does not select or
   reproduce brokerage tax lots. Values must be labelled as broker-derived or
   smallFish cash-flow P/L and must never be described as taxable realized P/L.
10. **Coverage is part of the result.** "Lifetime" means all retained, supported
    history since the response's `history_start`; it does not imply the user's
    entire brokerage lifetime.

## Terminology

| Term | Meaning |
|---|---|
| Symbol Ledger | The one brokerage-level record for an underlying symbol |
| Current Period | Events after the most recent reset, or all retained events when no reset exists |
| Archived Period | A completed, flat period sealed by an explicit user reset |
| Lifetime | Current plus all archived periods within retained history |
| Event | An immutable provider transaction or a separately identified manual reconciliation row |
| Component | An account-specific equity position or exact option contract within the symbol |
| Reset | The action that seals the current flat period and begins a new empty period |

The UI should use **Symbol Ledger**, **Trade History**, **Active**, **Archived**,
and **Archive completed history**. "Group" should disappear from new product
surfaces and new API names.

## Identity and normalization

The top-level identity is `(brokerage_id, normalized_symbol)`:

- `brokerage_id` is a configured, user-facing brokerage identity such as
  `fidelity` or `tastytrade`.
- The brokerage descriptor separately carries its portfolio role, such as
  `RETIREMENT` or `TRADING`.
- The backend registry separately selects an adapter type such as `SNAPTRADE`
  or `TASTYTRADE`; adapter names are not public brokerage identities.
- `normalized_symbol` is the canonical uppercase underlying already produced
  by provider adapters.
- Exact option contracts remain components identified by account plus OCC
  contract identity; they are never treated as symbol-ledger identities.
- Account identifiers remain on components and events even though they do not
  create additional top-level symbol ledgers.

Ticker changes, mergers, and provider symbol corrections are not silently
joined. A future explicit symbol-alias migration may link them after review.

## Lifecycle

The ledger uses a binary product state with orthogonal completeness and
reconciliation fields:

| Condition | `state` | P/L behavior |
|---|---|---|
| At least one current equity or option position is nonzero | `ACTIVE` | Marked P/L is normally `INDICATIVE` |
| Every component is flat and activity reconciles | `CLOSED` | Current-period P/L can be `COMPLETE` |
| Flatness cannot be established safely | `ACTIVE` | P/L is `UNAVAILABLE` and warnings explain why |

This preserves simple Active/Closed filtering without hiding incomplete
positions. Detailed fields such as `reconciliation_status`,
`pnl_completeness`, and `warnings` explain why an apparently closing symbol has
not archived yet.

Closing one contract does not archive the symbol while another contract or
equity position remains open. If shares are included in the symbol ledger,
closing a covered call leaves the symbol Active until the shares and every
other option component are flat.

## P/L contract

The API owns all formulas; Angular only formats returned values.

For a period with complete imported cash flows:

```text
net_cash_flow = cash_in + cash_out
open_market_value = signed current equity value + signed current option value
total_pnl = net_cash_flow + open_market_value
```

For a flat, reconciled period:

```text
open_market_value = 0
realized_pnl = total_pnl = net_cash_flow
```

Fees enter exactly once through broker net cash flow. Assignment and exercise
remain explicit lifecycle events. Tax-lot selection, wash-sale treatment, and
tax reporting are outside smallFish.

The response separates:

- `current_period_pnl`: results after the latest reset;
- `archived_pnl`: the sum of all reset periods;
- `lifetime_pnl`: current plus archived results within retained history; and
- equity, option, cash-flow, and open-market-value blocks so a combined number
  is never shown when an input is unavailable.

An open equity position may use broker cost basis for its indicative current
P/L. A closed equity lifecycle requires supported imported equity executions
or a provider-supplied realized result. Until that exists, affected period and
lifetime totals fail closed rather than inferring closed stock P/L from a
current-position snapshot.

## History ingestion and retention

Calendar years are fetch windows, not ledger boundaries. Sync continues to
upsert by stable provider event identity and never removes retained prior-year
events merely because the default fetch begins on January 1. An opening event
in one year and its closing event in another therefore remain in the same
symbol and current period unless the user explicitly reset the flat symbol in
between.

A new installation cannot claim history that its provider did not return.
Before lifetime P/L is considered complete, each adapter must record the
earliest supported activity date and whether the requested backfill reached
the provider's available boundary. The API exposes that as coverage rather
than treating the oldest locally retained event as proof of complete history.

Reset does not solve missing ingestion. A symbol with incomplete required
history cannot be reset because its current-period P/L is not complete.

## Reset and archive semantics

A reset is valid only when all of the following are true:

- the current period contains at least one event;
- the symbol is `CLOSED` because every component is flat;
- reconciliation is `RECONCILED`;
- current-period P/L is `COMPLETE`; and
- the period has not changed since the user loaded it.

The reset creates an archive-boundary record. It does not copy or move broker
events. Event membership in a period is derived from immutable event ordering
and the boundary's sealed event set/version.

Each boundary records at least:

```text
archive_id
brokerage_id
symbol
period_started_at
period_ended_at
first_event_at
last_event_at
event_count
realized_pnl_at_creation
event_set_hash_at_creation
period_version
request_id
note
created_at
```

`request_id` is client-generated and unique so retrying a successful request
returns the same archive instead of creating a duplicate. `period_version` is
an opaque server value returned with the current period. If a sync or manual
correction changes the period before reset, the server returns `409 Conflict`
and requires the client to refresh.

### Late and corrected broker events

Provider facts remain authoritative after reset. A boundary uses the stable
chronological event ordering `(executed_at, provider_event_id)`, not import time
or a calendar date alone. If a later sync inserts a backdated event at or before
an archive boundary, that event belongs to the archived period on the next
read. If a provider corrects an existing event under the same identity, the
corrected fact also participates in the next calculation.

The boundary itself remains immutable, but its displayed summary is a verified
projection rather than a frozen accounting assertion. The service compares the
current event set and P/L with `event_set_hash_at_creation` and
`realized_pnl_at_creation`. A change produces `verification_status = CHANGED`,
an audit warning, and recomputed values. Missing or unmatched late activity
makes the archive P/L unavailable. It must never be silently ignored merely to
keep the number shown at reset time.

An event ordered after the boundary belongs to the current period even if it
was imported in the same later sync. This rule keeps period assignment
deterministic across repeated reads.

After reset:

- the archive appears in the symbol's archive collection;
- the current period has zero events and zero P/L;
- the symbol remains Closed until a new position opens;
- lifetime P/L remains unchanged; and
- the next broker event for the symbol enters the new current period and makes
  the symbol Active when it creates open exposure.

Resetting an active or incomplete symbol is deliberately excluded from version
1. Archiving only selected closed contracts from an otherwise open symbol would
split rolls and related cash flows and reintroduce manual grouping decisions.

## API design

### Public identity

All new brokerage data APIs are brokerage-agnostic. The path identifies a
configured brokerage, not an SDK or aggregation connector:

| Public `brokerage_id` | Institution | Backend adapter | Portfolio role |
|---|---|---|---|
| `tastytrade` | Tastytrade | `TASTYTRADE` | `TRADING` |
| `fidelity` | Fidelity | `SNAPTRADE` | `RETIREMENT` |

SnapTrade is an integration through which Fidelity data is retrieved; it is
not the public brokerage identity. This distinction allows another institution
or configured connection to reuse the SnapTrade adapter without changing API
semantics.

Multiple accounts remain account components beneath the brokerage. A common
optional `account_id` query filter may narrow a response, but account identity
does not select a different response contract.

### Public resources

```text
GET  /api/brokerages
POST /api/brokerages/{brokerage_id}/sync

GET  /api/brokerages/{brokerage_id}/holdings
PATCH /api/brokerages/{brokerage_id}/holdings/{symbol}/metadata
POST /api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots

GET  /api/brokerages/{brokerage_id}/options
GET  /api/brokerages/{brokerage_id}/option-adjusted-basis

GET   /api/brokerages/{brokerage_id}/symbols
GET   /api/brokerages/{brokerage_id}/symbols/{symbol}
PATCH /api/brokerages/{brokerage_id}/symbols/{symbol}
GET   /api/brokerages/{brokerage_id}/symbols/{symbol}/events
GET   /api/brokerages/{brokerage_id}/symbols/{symbol}/archives
GET   /api/brokerages/{brokerage_id}/symbols/{symbol}/archives/{archive_id}
POST  /api/brokerages/{brokerage_id}/symbols/{symbol}/archives
```

The same resource has the same request and response shape for every brokerage.
Holdings and Options have different resource-specific item types, but Fidelity
Holdings and Tastytrade Holdings are one contract, and Fidelity Options and
Tastytrade Options are one contract.

Common query parameters include `account_id`, state/filter parameters, and
cursor pagination. Generic routes never accept SnapTrade- or
Tastytrade-specific inputs.

This API is additive during migration. Existing `/brokerage-ledgers/*`,
`/options/activity`, `/options/groups/*`, and `/retirement/options` contracts
remain compatible until their Angular consumers and any external callers have
migrated.

### Backend layers

```text
FastAPI router
    -> common BrokerageService
        -> BrokerageRegistry resolves brokerage_id
            -> provider adapter reads provider artifacts into canonical facts
                -> common projections compute Holdings, Options,
                   Option-Adjusted Basis, and Symbol Ledger responses
```

Provider adapters normalize facts; they do not independently build final API
responses or duplicate business formulas. Common projection code owns totals,
P/L signs, lifecycle state, completeness, adjusted basis, and warnings once.

A suggested package boundary is:

```text
stock-app/app/brokerages/
  contracts.py
  registry.py
  service.py
  adapters/
    base.py
    snaptrade.py
    tastytrade.py
  projections/
    holdings.py
    options.py
    option_adjusted_basis.py
    symbol_ledger.py
```

The exact filenames may change during implementation, but the dependency
direction may not. Routers and Angular never import an adapter. Adapters never
own UI response variants. `stock-app/` continues not to import `utilities/` or
`studies/`.

### Canonical adapter contract

The registry returns an implementation of a common read interface resembling:

```python
class BrokerageAdapter(Protocol):
    def descriptor(self) -> BrokerageDescriptor: ...
    def capabilities(self) -> BrokerageCapabilities: ...
    def positions(self) -> list[PositionFact]: ...
    def activity(self) -> list[ActivityFact]: ...
    def market_observations(self) -> list[MarketObservation]: ...
```

Canonical position facts include brokerage and account identity, instrument,
underlying, exact option contract terms, signed quantity, multiplier, cost
basis/open price when supplied, current mark/value, timestamps, and provenance.

Canonical activity facts include stable provider event identity, brokerage and
account identity, normalized underlying and contract identity, instrument,
normalized lifecycle action, signed position delta, signed net cash flow,
fees, execution/import timestamps, and provenance.

Provider terms such as `SELL_TO_OPEN`, `Sell to Open`, structured SnapTrade
option symbols, and Tastytrade OCC symbols are converted inside the applicable
adapter. A provider field that cannot be supplied remains `null` with coverage
and a stable reason; adapters never fabricate zero or silently omit a
canonical field.

Read adapters consume materialized artifacts only. Provider calls belong to a
separate sync/command capability selected through the same registry.

### Brokerage discovery and capabilities

```text
GET /api/brokerages
```

Example response:

```json
{
  "schema_name": "smallfish.brokerage-catalog",
  "schema_version": 1,
  "brokerages": [
    {
      "id": "tastytrade",
      "label": "Tastytrade",
      "institution": "TASTYTRADE",
      "portfolio_role": "TRADING",
      "capabilities": {
        "holdings": true,
        "options": true,
        "option_adjusted_basis": true,
        "activity": true,
        "sync": true
      }
    },
    {
      "id": "fidelity",
      "label": "Fidelity",
      "institution": "FIDELITY",
      "portfolio_role": "RETIREMENT",
      "capabilities": {
        "holdings": true,
        "options": true,
        "option_adjusted_basis": true,
        "activity": true,
        "sync": true
      }
    }
  ]
}
```

Angular may use declared capabilities to show supported resources and actions.
It must never branch on brokerage identity to interpret data or choose a
provider-specific component.

### Common response envelope

Every brokerage read resource uses the same envelope vocabulary:

```json
{
  "schema_name": "smallfish.brokerage-holdings",
  "schema_version": 1,
  "brokerage": {
    "id": "fidelity",
    "label": "Fidelity",
    "institution": "FIDELITY",
    "portfolio_role": "RETIREMENT"
  },
  "availability": {
    "status": "AVAILABLE",
    "reasons": []
  },
  "as_of": {
    "positions": "2026-07-28T16:00:00Z",
    "activity": null,
    "market": "2026-07-28"
  },
  "coverage": {
    "status": "COMPLETE",
    "history_start": null,
    "reasons": []
  },
  "summary": {},
  "items": [],
  "warnings": []
}
```

The item schema and summary fields are versioned per resource. Missing,
unsupported, stale, incomplete, and empty are distinct states. A supported but
empty resource returns an empty `items` array; missing required inputs do not
masquerade as an empty brokerage.

Provider exception details remain in server logs. Public failures use stable
machine-readable codes and safe messages. Optional integration state remains a
capability/availability result and must not block navigation.

### Brokerage-neutral sync

```text
POST /api/brokerages/{brokerage_id}/sync
```

The optional request uses only common resource names:

```json
{
  "resources": ["HOLDINGS", "ACTIVITY", "MARKET_DATA"]
}
```

Omitting `resources` requests every configured supported resource. The adapter
decides which provider calls and artifact writes are needed. The response is a
common sync report with resource-level success, timestamps, inserted/updated
counts, coverage changes, and safe warnings. It never exposes provider tokens,
account numbers, or raw provider exception details.

### List symbol ledgers

```text
GET /api/brokerages/{brokerage_id}/symbols
    ?state=active|closed|all
    &exposure=all|options
```

`state` defaults to `active` and `exposure` defaults to `all`. The Options tabs
request `exposure=options`, which includes `OPTIONS` and `EQUITY_AND_OPTIONS`
ledgers but excludes `EQUITY` ledgers already presented under Holdings. The
list is a compact summary suitable for the main table. Search and sorting
remain client-side initially because the local ledger is small.

Example response:

```json
{
  "schema_name": "smallfish.symbol-ledger-list",
  "schema_version": 1,
  "brokerage": {
    "id": "tastytrade",
    "label": "Tastytrade",
    "institution": "TASTYTRADE",
    "portfolio_role": "TRADING"
  },
  "as_of": {
    "positions": "2026-07-28T16:00:00Z",
    "activity": "2026-07-28T16:00:00Z",
    "market": "2026-07-28"
  },
  "coverage": {
    "history_start": "2026-01-01",
    "equity_activity": "UNAVAILABLE",
    "option_activity": "COMPLETE",
    "reasons": ["Closed equity activity is not imported for this brokerage."]
  },
  "summary": {
    "symbol_count": 1,
    "active_count": 1,
    "archived_count": 0,
    "needs_review_count": 0,
    "lifetime_pnl": null
  },
  "items": [
    {
      "symbol": "EXAMPLE",
      "state": "ACTIVE",
      "reconciliation_status": "RECONCILED",
      "pnl_completeness": "INDICATIVE",
      "accounts": ["ACCOUNT LABEL"],
      "exposure": "EQUITY_AND_OPTIONS",
      "current_period": {
        "period_version": "opaque-version",
        "started_at": "2026-01-01",
        "event_count": 6,
        "first_event_at": "2026-01-15T15:30:00Z",
        "last_event_at": "2026-07-28T15:30:00Z",
        "net_cash_flow": -10500.0,
        "open_market_value": 11925.0,
        "total_pnl": 1425.0,
        "realized_pnl": null
      },
      "archived_period_count": 0,
      "archived_pnl": 0.0,
      "lifetime_pnl": null,
      "notes": "",
      "warnings": []
    }
  ]
}
```

Brokerage and symbol totals are `null` whenever an included required value is
unavailable. The response never substitutes zero for missing history.

### Get one symbol ledger

```text
GET /api/brokerages/{brokerage_id}/symbols/{symbol}
```

The detail response contains the same summary plus:

- account-aware current equity and option components;
- current positions and exact option terms;
- current-period and lifetime P/L decomposition;
- annotations and notes;
- reconciliation details;
- archive summaries; and
- links or counts for the paginated event history.

It should reuse the component vocabulary and provenance already established by
`smallfish.brokerage-ledger` version 1 rather than introduce a second set of
cash-flow signs or completeness meanings.

### Read immutable events

```text
GET /api/brokerages/{brokerage_id}/symbols/{symbol}/events
    ?period=current
    &cursor={opaque_cursor}
    &limit=100
```

`period` accepts `current`, `all`, or an `archive_id`. Events are returned
newest first. Pagination uses an opaque cursor based on stable provider event
identity and ordering, not a numeric row offset that can shift after sync.

Every event returns its provider identity, account, underlying symbol,
instrument and contract identity, action, quantity delta, signed net cash,
fees, execution time, source, imported time, and whether it is a manual
reconciliation row. It does not return `group_id` or `group_name`.

### Update symbol metadata

```text
PATCH /api/brokerages/{brokerage_id}/symbols/{symbol}
```

Version 1 accepts only app-owned metadata:

```json
{
  "notes": "Watch assignment history"
}
```

The symbol and lifecycle state are not editable. A patch cannot modify broker
facts, P/L, account membership, or archive boundaries.

### Archive completed history

```text
POST /api/brokerages/{brokerage_id}/symbols/{symbol}/archives
```

Request:

```json
{
  "request_id": "7a25a415-9ae4-4f0d-84b1-16339ad12731",
  "expected_period_version": "opaque-version",
  "note": "Reset after completed strategy"
}
```

Success returns `201 Created` with both the created archive summary and the
refreshed symbol summary. Repeating the same `request_id` returns the original
result without creating another boundary.

An archive summary has this minimum shape:

```json
{
  "archive_id": "archive-id",
  "symbol": "EXAMPLE",
  "period_started_at": "2026-01-01T00:00:00Z",
  "period_ended_at": "2026-09-30T20:00:00Z",
  "event_count": 14,
  "realized_pnl": 640.0,
  "pnl_completeness": "COMPLETE",
  "verification_status": "VERIFIED",
  "created_at": "2026-10-01T16:00:00Z",
  "note": "Reset after completed strategy",
  "warnings": []
}
```

Representative failures:

| Status | Condition |
|---|---|
| `404` | Unknown brokerage, symbol, or archive |
| `409` | Symbol is active, incomplete, unreconciled, empty, or changed since `expected_period_version` |
| `422` | Invalid request shape or identifier |

Provider exception details stay in server logs. API errors expose a stable
machine-readable code and a safe user message, for example:

```json
{
  "detail": {
    "code": "SYMBOL_NOT_FLAT",
    "message": "EXAMPLE still has open exposure and cannot be archived."
  }
}
```

## Persistence design

Retain provider activity and position artifacts as immutable facts. Add
app-owned symbol metadata and reset boundaries, preferably as versioned CSV
artifacts under the applicable data root:

```text
symbol_ledger_metadata.csv
symbol_ledger_archives.csv
```

Suggested metadata key and fields:

```text
brokerage_id,symbol,notes,created_at,updated_at
```

Suggested archive key and fields:

```text
schema_version,archive_id,brokerage_id,symbol,period_started_at,period_ended_at,
first_event_at,last_event_at,event_count_at_creation,realized_pnl_at_creation,
event_set_hash_at_creation,period_version,request_id,note,created_at
```

The exact boundary representation must be deterministic when multiple broker
events share a timestamp. It uses the ordered boundary event identity and an
event-set hash rather than relying on date alone. Writes remain atomic.

Archive summaries are app-owned projections. They never replace the event
ledger as accounting evidence. On read, the service recomputes and verifies an
archive against its chronological boundary and creation-time hash, reports
late or corrected facts, and fails closed if the underlying artifact is
inconsistent.

## Relationship to the combined brokerage view

`GET /brokerage-ledgers/{portfolio}/combined` already supplies a normalized,
account-aware symbol summary for current equity and option economics. The
new adapters and common projections should reuse its component schema,
provenance, cash-flow signs, and completeness rules while removing the central
`trading`/`retirement` branching.

The contracts differ in purpose:

- `/brokerage-ledgers/{portfolio}/combined` remains the current
  Option-Adjusted Basis compatibility view during migration.
- `/api/brokerages/{brokerage_id}/option-adjusted-basis` becomes the canonical
  brokerage-agnostic replacement.
- `/api/brokerages/{brokerage_id}/symbols` becomes the durable symbol-history
  and lifecycle resource.
- removal of `/brokerage-ledgers/*` remains a separate compatibility decision
  after all consumers migrate.

## Migration from groups

Use an additive strangler migration. The grouping design is settled first, but
its behavior is not implemented in the legacy provider-specific group code.
Doing that would create a temporary second implementation and then require the
same lifecycle/reset work to be rewritten on the canonical API.

The safe order is:

1. characterize and freeze current contracts;
2. normalize provider artifacts through registry-selected adapters;
3. build common Holdings, Options, and Option-Adjusted Basis projections and
   additive APIs;
4. implement Symbol Ledger lifecycle/reset once on canonical facts;
5. leave the existing app on legacy endpoints until the new backend is complete
   and parity-tested;
6. switch both brokerage pages to one common Angular client and shared
   components;
7. retain the old UI/backend as a rollback boundary through the Phase 5 owner
   checkpoint; and
8. only then stop legacy group writes and clean up proven-unused routes,
   models, components, and artifacts.

Grouping is therefore first in product design but fourth in implementation. No
old endpoint or component is removed merely because a replacement exists.

1. Inventory existing groups by `(account scope, symbol)` without changing
   artifacts.
2. If a symbol has exactly one group, migrate its notes into symbol metadata.
   The generated name and manual lifecycle value are not authoritative.
3. If duplicate same-symbol groups exist, stop and produce a migration report.
   Combine their immutable events automatically by symbol, but require an
   explicit metadata decision when their notes conflict. Never discard either
   note silently.
4. Materialize and verify symbol-ledger reads while the current group APIs
   remain unchanged.
5. Migrate the Trading and Retirement Angular consumers to the new list,
   detail, event, metadata, and archive endpoints.
6. Stop writing group and membership enrichment after all consumers migrate.
7. Remove `options_group_members.csv`, group-creation routes, assignment routes,
   and compatibility fields only in a separately reviewed cleanup after a
   repository and external-use audit.

Existing group IDs may be returned as deprecated compatibility aliases during
the transition, but new Symbol Ledger routes and persistence must never use
them as identity.

## UI implications

- Angular uses one brokerage-agnostic API service and one TypeScript contract
  per resource. Provider-specific response models do not cross the API layer.
- Trading and Retirement are thin shells that provide `tastytrade` or
  `fidelity` plus page navigation context; business-data components are shared.
- Shared components use returned capabilities, availability, coverage, and
  provenance. They never select behavior by brokerage ID.
- Rename **Trade Groups** to **Symbol Ledger**.
- Show one row per underlying symbol, never one row per year or strategy.
- Default to Active; retain Active, Closed, and All filters.
- Derive the badge from API state; remove editable status and group name.
- Keep notes editable.
- The detail view shows current positions, current-period totals, lifetime
  totals, archived-period summaries, immutable events, provenance, and
  reconciliation warnings.
- Show **Archive completed history** only for a nonempty, complete, reconciled,
  Closed current period.
- Confirm the reset by naming the symbol, event count, date range, and realized
  P/L that will become an archived period.
- After reset, show the archive and an empty current period without implying
  that broker history was deleted.
- Always display the retained-history start date near lifetime P/L.

## Non-goals

- Tax-lot selection or tax reporting
- Editing imported broker facts
- Splitting one symbol into strategies, campaigns, wheels, or calendar years
- Resetting while any symbol exposure remains open
- Moving only selected contracts into an archive
- Automatically joining renamed tickers or corporate actions
- Calling broker providers from read endpoints
- Changing frozen research-study evidence or methodology

## Acceptance criteria

- `GET /api/brokerages` discovers Fidelity and Tastytrade through one contract.
- Holdings, Options, and Option-Adjusted Basis have identical per-resource
  request and response shapes across both brokerages.
- Provider adapters produce canonical facts and no common projection or Angular
  component interprets SnapTrade- or Tastytrade-specific fields.
- Adding another configured brokerage backed by an existing adapter requires a
  registry/configuration entry, not a new router or UI component.
- At most one symbol-ledger row exists per `(brokerage_id, symbol)`.
- Every supported event is associated by normalized underlying without a
  membership record or user action.
- A cross-year option open and close appears in the same symbol and period.
- Closing one of several contracts does not archive the symbol.
- A fully flat, reconciled symbol becomes Closed without a metadata write.
- A new opening event returns the same symbol to Active and preserves its
  current-period and lifetime tally.
- Missing activity, marks, or reconciliation prevents false completion and
  prevents reset.
- Reset creates exactly one idempotent logical archive, leaves broker events
  unchanged, starts an empty current period, and preserves lifetime P/L.
- Multiple accounts remain visible and no coverage or basis crosses an account
  boundary.
- Equity and option values use the existing normalized signs and fail-closed
  semantics.
- Existing APIs remain compatible until their callers are deliberately
  migrated.
- Trading and Retirement become thin page shells over shared brokerage UI
  components and one brokerage-agnostic Angular API client.
- Backend tests use fake providers and no network; UI verification includes
  Trading and Retirement routes, Active/Closed filters, detail history, and
  reset confirmation.

## Settled API decisions

The owner approved the brokerage-agnostic direction on 2026-07-28:

1. New routes are rooted at `/api/brokerages/{brokerage_id}`.
2. Public brokerage IDs are configured institution identities such as
   `fidelity` and `tastytrade`; `snaptrade` is a backend adapter type.
3. A registry selects provider adapters. Provider-specific transformations
   remain in adapter modules and produce common canonical facts.
4. Common projections build Holdings, Options, Option-Adjusted Basis, and
   Symbol Ledger outputs from those facts.
5. The same resource has identical request and response shapes across
   brokerages. Missing values use `null`, coverage, and stable reasons rather
   than provider-specific fields.
6. Symbol lists default to `active`; callers may request `closed` or `all`.
7. Symbol detail returns compact archive summaries inline; event bodies remain
   paginated through the events resource.
8. Version 1 has no reset undo operation.
9. Manual reconciliation endpoints remain at compatibility paths during the
   first migration and drop `group_id` only after new consumers are ready.
10. Existing endpoints remain intact until new backend parity, UI migration,
    and a separate consumer/compatibility audit are complete.

## Implementation starting point

The implementation agent must read these before editing:

1. `AGENTS.md`
2. `Requirements.md`
3. this document in full
4. `docs/BROKERAGE_LEDGER_COMBINED_VIEW.md`
5. `docs/ARCHITECTURE.md`
6. `stock-app/README.md`
7. before Phase 5, `stock-app-ui/AGENTS.md` and
   `stock-app-ui/docs/UX_GUIDANCE.md`

The main current implementation surfaces are:

| Concern | Current files |
|---|---|
| Trading immutable activity, grouping, marks, and P/L | `stock-app/app/options_activity.py` |
| Retirement option activity and group projection | `stock-app/app/retirement_options.py` |
| Existing normalized symbol/component service | `stock-app/app/brokerage_ledger.py` |
| Artifact paths | `stock-app/app/config.py` |
| Broker-neutral routes | `stock-app/app/routers/brokerage_ledgers.py` |
| Legacy Trading routes | `stock-app/app/routers/options.py` |
| Legacy Retirement routes | `stock-app/app/routers/retirement.py` |
| Backend coverage | `stock-app/tests/test_options_activity.py`, `stock-app/tests/test_retirement_options.py`, `stock-app/tests/test_brokerage_ledger.py` |
| Shared group UI | `stock-app-ui/src/app/shared/brokerage-option-groups/` |
| Existing combined-symbol UI | `stock-app-ui/src/app/shared/brokerage-ledger-combined/` |
| Angular API/models | `stock-app-ui/src/app/api/`, `stock-app-ui/src/app/model/` |
| Brokerage page consumers | `stock-app-ui/src/app/options/`, `stock-app-ui/src/app/retirement-portfolio/` |

Prefer the common `brokerages/` package boundary described above over making
`options_activity.py` or `retirement_options.py` own another public contract.
Reuse their current artifact readers during migration and the existing
`brokerage_ledger.py` component vocabulary; do not copy accounting formulas
into routers or Angular.

## Decision gate

The owner has settled the following product direction:

- one symbol ledger per brokerage and symbol;
- multiple groups for the same symbol are not permitted;
- all retained supported trades for the symbol remain visible;
- normal close and reopen preserve a running tally;
- Active/Closed is derived from exposure rather than a user label; and
- the user may explicitly reset completed history into an archived period; and
- the **Settled API decisions** above govern the new backend and UI contracts.

## Phase dashboard

Update this table in the same commit that completes a phase. Valid states are
`NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `COMPLETE`. Evidence should name
tests, commits, or owner confirmation; do not write "verified" without the
corresponding evidence.

Per-phase evidence below records the suite totals **as of that phase**, which is
why they differ. Current totals are in "Resume here" at the top.

| Phase | Scope | Status | Next action / blocker | Evidence |
|---|---|---|---|---|
| 1 | Contract baseline and characterization | COMPLETE | Phase 2 may begin | `stock-app/tests/brokerage_contract_spec.py` + `test_brokerage_adapter_contract.py` (14 tests); new characterization cases in `test_options_activity.py`, `test_retirement_options.py`, `test_brokerage_ledger.py`; full backend suite 328 passed |
| 2 | Brokerage registry, adapters, and canonical facts | COMPLETE | Phase 3 may begin | `stock-app/app/brokerages/` (contracts, registry, SnapTrade + Tastytrade adapters); shared conformance suite `test_brokerage_adapters.py` (32 tests) parametrized over the registry; full backend suite 360 passed |
| 3 | Common projections and additive read APIs | COMPLETE | Phase 4 may begin | `app/brokerages/projections/` + `service.py` + `routers/brokerages.py`; `GET /api/brokerages`, `/holdings`, `/options`, `/option-adjusted-basis`; `test_brokerage_api.py` (35 tests) incl. parity against `/brokerage-ledgers/*/combined`; full backend suite 395 passed |
| 4 | Symbol Ledger lifecycle, archives, and mutation APIs | COMPLETE | Phase 5 may begin | `projections/symbol_ledger.py`, `projections/events.py`, `store.py`, `sync.py`, `migration.py`; all 14 settled routes now served and all 21 frozen legacy routes still served; `test_symbol_ledger_api.py` (56 tests); full backend suite 452 passed |
| 5 | First shared Trading/Retirement UI slice | COMPLETE — owner approved | Owner approved the Phase 5 checkpoint on 2026-07-29 and authorized Phase 6 | `model/brokerage.ts`, `api/brokerage.service.ts`, `shared/symbol-ledger/` mounted on both pages; later corrections preserve option-only scope and option-adjusted-basis semantics |
| 6 | History/reset UX and shared UI consolidation | COMPLETE — automated checks passed; browser verification pending | Phase 7 completed; Phase 8 performs final browser verification | Shared `SymbolLedgerComponent` implements current/all/archive history, compact archive summaries, on-demand archive detail, reset eligibility/confirmation, conflict refresh, and idempotent retry on both pages; focused tests 21 passed, full Angular suite 62 passed, build clean |
| 7 | Compatibility cutover, cleanup, and current-behavior docs | COMPLETE — automated checks passed; browser verification pending | Phase 8 final regression and route verification | Owner confirmed legacy routes have no external consumers. Production sync suppresses legacy group writes; legacy group mutation routes return 410; shared UI no longer imports groups or risk surfaces; old artifacts remain rollback-only. The later adjusted-basis follow-up removed the last legacy combined projection from the Brokerage tab; full automated-gate evidence is in the progress log. |
| 8 | Full regression, browser verification, and handoff closeout | COMPLETE | No further action | Full backend 461, Angular build + 55 tests, docs, secrets, and diff checks passed. Isolated browser checks confirmed both routes, shared tabs, Symbol Ledger lifecycle filters/detail, absence of groups/risk UI, and narrow-width fit. A synthetic-only lifecycle check created an archive, verified a reopen starts one active period while retaining the archive, and surfaced a changed-archive warning for a backdated event. |
| Post-phase cleanup | Consumer-first removal of remaining internal legacy compatibility | IN PROGRESS | Holdings is fully migrated and its legacy surface removed. Next: audit the legacy Retirement holdings routes (`/retirement/holdings/*`, `/retirement/enrichment/{symbol}`) and `snaptrade_service.portfolio`, which have no Angular consumer but are still frozen contracts and still called internally by `retirement_options.py`; then the remaining group-only backend paths | `5aeb6d8` removed the unused legacy Combined client. `b6e719f` completed the shared Symbol Ledger action cleanup. The Holdings contract comparison, all four gaps it found, the consumer cutover, and the removal of `/brokerage-ledgers/{portfolio}/holdings` and its write paths are done and browser-verified. `/brokerage-ledgers/{portfolio}/combined` is the only route left on that prefix and is retained deliberately as the parity-test baseline. |

## Phased implementation plan

### Phase 1 - Contract baseline and characterization

Goal: freeze the accepted API choices and protect current behavior before
refactoring it.

Implementation checklist:

- Inspect `git status` and preserve unrelated user work.
- Capture current route and artifact consumers with focused `rg` searches.
- Add passing contract tests for the settled common envelope, canonical fact
  vocabulary, brokerage catalog, and per-resource response identity.
- Add or tighten passing characterization tests for Trading and Retirement:
  automatic single-group creation, close P/L retention, archived-group
  reactivation, delayed close behavior, account-aware components, and the
  existing normalized `/combined` response.
- Add synthetic fixtures for a cross-year open/close, multiple contracts under
  one underlying, and the same symbol across multiple Retirement accounts.
- Do not inspect, print, or copy real user positions into fixtures or logs.

Exit criteria:

- Settled API decisions are represented by tests or typed contracts.
- The existing behavior that later phases will replace is covered by passing
  tests using fake data.
- No production behavior changes.

Rollback boundary: remove only the new/changed characterization tests and
documentation from this phase. No data migration exists yet.

Automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests/test_options_activity.py \
  stock-app/tests/test_retirement_options.py \
  stock-app/tests/test_brokerage_ledger.py \
  stock-app/tests/test_brokerage_adapter_contract.py
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

`test_brokerage_adapter_contract.py` was added to this gate during
implementation; the `-k 'brokerage_adapter'` selection in Phases 2-3 and the
full-suite runs in Phases 4, 7, and 8 already cover it.

Suggested commit: `test: characterize brokerage API migration baseline`

### Phase 2 - Brokerage registry, adapters, and canonical facts

Goal: isolate provider-specific transformation behind a registry and produce
the same canonical facts for Fidelity/SnapTrade and Tastytrade without changing
routes, UI, grouping, or legacy writes.

Implementation checklist:

- Add common descriptor, capabilities, account, position, activity, market,
  coverage, and provenance contracts.
- Add a brokerage registry keyed by `fidelity` and `tastytrade`.
- Implement a SnapTrade adapter that maps current Fidelity artifacts to the
  canonical contracts.
- Implement a Tastytrade adapter that maps current Tastytrade artifacts to the
  same contracts.
- Normalize provider action names, option identities, quantities, cash signs,
  accounts, timestamps, and missing-field reasons inside adapters.
- Keep all reads materialized and all tests network-free.
- Add one shared adapter-conformance suite and run it against both adapters;
  avoid two unrelated sets of assertions.

Exit criteria:

- Both adapters satisfy the same typed/conformance contract.
- Common code contains no `if fidelity`, `if tastytrade`, `if retirement`, or
  `if trading` transformation branches; registry selection is the only switch.
- Existing routes and files behave exactly as before.

Rollback boundary: remove the additive package/registry and its tests. No new
route, artifact, or consumer exists.

Automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests -k 'brokerage_adapter or brokerage_ledger'
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `refactor: add brokerage adapters and canonical facts`

### Phase 3 - Common projections and additive read APIs

Goal: compute Holdings, Options, and Option-Adjusted Basis once from canonical
facts and expose additive brokerage-agnostic read APIs while the app continues
using legacy endpoints.

Implementation checklist:

- Implement common Holdings, Options, and Option-Adjusted Basis projections.
- Reuse existing sign, account-boundary, provenance, completeness, and
  fail-closed behavior; do not duplicate formulas per adapter.
- Add `/api/brokerages` discovery/capabilities.
- Add brokerage-agnostic GET routes for Holdings, Options, and
  Option-Adjusted Basis.
- Return the common envelope and identical per-resource key sets for both
  brokerages.
- Add parity tests against legacy fixture outputs for fields whose semantics
  are unchanged. Compare accounting values and components rather than legacy
  group names/status that will intentionally change later.
- Keep all legacy routes and Angular consumers untouched.

Exit criteria:

- Both brokerages return the same versioned shapes for each new read resource.
- Common projections contain no provider-specific branching.
- Existing API tests remain green and the running app still uses old routes.

Rollback boundary: remove the additive read routes and projections. Registry,
adapters, old routes, and UI remain safe.

Automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app \
  stock-app/tests -k 'brokerage_adapter or brokerage_api or brokerage_ledger'
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `feat: add brokerage-agnostic read APIs`

### Phase 4 - Symbol Ledger lifecycle, archives, and mutation APIs

Goal: implement the grouping replacement once on canonical facts, finish the
new backend, and leave all legacy consumers operational.

Implementation checklist:

- Add the common Symbol Ledger projection keyed only by
  `(brokerage_id, normalized_symbol)`.
- Derive Active/Closed, current-period/lifetime P/L, coverage, and
  reconciliation without event-to-group membership.
- Add versioned config paths and strict schemas for symbol metadata and archive
  boundaries.
- Implement atomic writes, opaque period versions, idempotent `request_id`,
  deterministic event ordering, late/corrected event verification, and reset
  validation.
- Add a read-only migration report for existing group metadata. Migrate one
  unambiguous note per symbol and stop on conflicting duplicate metadata.
- Add list, detail, metadata, paginated events, archive reads, and archive
  creation routes under `/api/brokerages/{brokerage_id}/symbols`.
- Add the common sync command/response while retaining current provider sync
  endpoints as compatibility commands.
- Return versioned schemas, stable safe error codes, and opaque cursors.
- Ensure read routes consume materialized artifacts only and never call a
  provider.
- Make sync results immediately visible through derived Symbol Ledger state;
  a new event must not require a group-membership write for the new API.
- Keep provider sync commands and manual reconciliation behavior compatible.
- Add route tests for filters, encoding, pagination, idempotent retry,
  concurrent-period conflict, invalid reset states, late events, and both
  brokerages.
- Run a route/reference sweep proving no existing consumer was removed.
- Do not delete or stop writing legacy group files in this phase.

Exit criteria:

- Every proposed brokerage read/mutation endpoint is covered by backend API
  tests and the backend design is complete enough for UI migration.
- Existing endpoint tests remain green.
- No network is used by tests or read endpoints.

Rollback boundary: remove the additive routes and module wiring. Legacy routes
and consumers are still intact.

Automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `feat: add symbol ledger and brokerage mutation APIs`

### Phase 5 - First shared Trading/Retirement UI slice

Goal: cut the existing app over to the complete new backend through one common
Angular brokerage API client and the first shared Symbol Ledger list/detail
experience, leaving archive/reset interactions and final deduplication for
Phase 6.

Implementation checklist:

- Read the UI-specific agent instructions and UX guidance before editing.
- Add one brokerage-agnostic Angular API service and common TypeScript contracts
  for discovery, Holdings, Options, Option-Adjusted Basis, and Symbol Ledger.
- Replace `trading`/`retirement` service parameters with the public brokerage
  IDs `tastytrade`/`fidelity`; retain portfolio role only as returned metadata.
- Reuse shared UI primitives and tokens; do not fork separate Trading and
  Retirement implementations.
- Mount the same Symbol Ledger component on `/options` and `/retirement`.
- Show one row per symbol, Active/Closed/All filters, coverage start,
  current-period/lifetime P/L, completeness, accounts, notes, components,
  immutable event counts, and visible reconciliation warnings.
- Remove editable group name/status and all event reassignment controls from
  the new surface.
- Preserve the legacy backend/API compatibility layer for rollback.
- Ensure no component branches on brokerage identity to interpret a response;
  use common fields and declared capabilities.
- Add component/service tests for both brokerage inputs, empty/error/loading,
  filters, notes, incomplete P/L, and narrow layouts.

Exit criteria:

- Both brokerage routes consume the shared Symbol Ledger API and component.
- Automated Angular tests and production build pass.
- The phase is committed before requesting owner review.

Rollback boundary: switch the two page templates/services back to the retained
legacy presentation; the additive backend remains safe.

Automated gate:

```bash
cd stock-app-ui
npm run build
npm run test:ci
cd ..
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `feat: add shared symbol ledger UI`

Required owner checkpoint after the commit:

1. Pause and ask the owner to inspect `/options` and `/retirement`.
2. Ask whether navigation, Active/Closed filtering, symbol details, notes,
   warnings, and P/L presentation appear broken or materially confusing.
3. Record the owner's response in the progress log.
4. If problems are reported, fix them, rerun Phase 5 automated checks, and
   commit the focused correction before asking again.
5. Begin Phase 6 only after explicit owner approval.

### Phase 6 - History, archive, reset, and UI consolidation

Goal: complete the workflow and remove duplicative brokerage UI implementations
after the owner approves the core UI shape.

Implementation checklist:

- Add paginated current/all/archive event history.
- Show compact archive summaries in symbol detail and archive detail on demand.
- Add **Archive completed history** only when the API declares reset eligible.
- Confirm symbol, event count, period, and realized P/L before reset.
- Handle `409` refresh/conflict, idempotent retry, late-event changes, empty
  current period, and unavailable history clearly.
- Keep imported events read-only and never imply they were deleted or moved.
- Add Angular tests for reset eligibility, confirmation, success, conflict,
  retry, pagination, and changed archive warnings.
- Refactor Holdings, Options, Option-Adjusted Basis, Symbol Ledger, details,
  empty/error/loading, filters, and brokerage sync presentation into shared
  components driven by common contracts and capabilities.
- Reduce the Trading and Retirement page components to thin shells that supply
  the brokerage ID and page-level navigation context.
- Remove provider-specific Angular models, service methods, and duplicate
  components only after both shells use the shared replacements.

Exit criteria:

- The full designed workflow is available on both brokerage pages through
  shared code, and no business-data component branches on brokerage identity.
- Automated Angular tests and build pass.
- Record this phase as automated-only; browser verification remains pending
  until Phase 8.

Rollback boundary: hide archive/reset/history controls and retain Phase 5's
read-only Symbol Ledger UI. Backend archive facts remain preserved.

Automated gate:

```bash
cd stock-app-ui
npm run build
npm run test:ci
cd ..
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `refactor: consolidate brokerage UI and add symbol history`

### Phase 7 - Compatibility cutover, cleanup, and behavior docs

Goal: stop creating ambiguous group state and remove only legacy surfaces that
have proven to be unused.

Implementation checklist:

- Run repository-wide route, model, artifact, and consumer searches.
- Stop new group creation and event-membership writes only after both new
  consumers and migration tests are proven.
- Preserve a compatibility projection when an existing caller still needs an
  old response shape; it must project at most one legacy-looking row per symbol.
- Remove or reject APIs that could create a second same-symbol group.
- Do not delete public routes solely because Angular no longer calls them.
  Confirm external-use policy with the owner at the Phase 5 checkpoint or keep
  a deprecated compatibility shim.
- Preserve old artifacts until migration is verified against synthetic copies.
- Update `stock-app/README.md`, `stock-app-ui/README.md`,
  `docs/ARCHITECTURE.md`, `docs/DATA.md`, `docs/BROKERAGES.md`, and
  `stock-app-ui/docs/UX_GUIDANCE.md` to describe implemented behavior.
- Update this plan's dashboard and progress log with compatibility evidence.

Exit criteria:

- Production paths cannot create multiple symbol groups.
- New UI and APIs no longer depend on membership artifacts or manual status.
- Any retained legacy route is explicitly documented as a compatibility shim.
- Full backend and Angular automated suites pass.

Rollback boundary: restore legacy route/write projections from the previous commit;
do not reverse or delete migrated user metadata or archive artifacts.

Automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
cd stock-app-ui
npm run build
npm run test:ci
cd ..
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `refactor: cut over from trade groups to symbol ledgers`

### Phase 8 - Full regression, browser verification, and closeout

Goal: prove the completed migration as an integrated product and leave a clean
implementation handoff.

Implementation checklist:

- Run the complete automated backend and Angular suites again.
- Check which process owns ports 8000 and 4200 before trusting a response.
- Start services only through documented `commands.sh`/npm commands.
- Load `/options` and `/retirement` with representative synthetic or safely
  materialized data.
- Verify Active, Closed, and All; symbol detail; account components; complete,
  indicative, and unavailable P/L; notes; event pagination; reset eligibility;
  reset confirmation; successful reset; reopen; late-event warning; empty;
  loading; error; and narrow-width behavior.
- Confirm no secret, account identifier, or real position appears in test
  fixtures, screenshots, logs, docs, or commits.
- Fix any defect, rerun the affected targeted gate, then rerun the full final
  gate.
- Update the dashboard and append the final evidence and remaining follow-ups.

Exit criteria:

- All acceptance criteria in this document are satisfied.
- Both affected routes have been visually inspected after the final UI change.
- The worktree contains only intentional changes and every phase has a focused
  commit.

Rollback boundary: Phase 7's compatibility shims remain the recovery path.
Do not delete backups or legacy artifacts as part of final verification.

Final automated gate:

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
cd stock-app-ui
npm run build
npm run test:ci
cd ..
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Suggested commit: `docs: close out symbol ledger migration`

## Stop and escalation rules

Stop the current phase and ask the owner rather than broadening scope when:

- a requested change conflicts with a settled API decision;
- duplicate same-symbol groups contain conflicting notes or other metadata;
- a provider event cannot be mapped safely to a normalized underlying;
- a reset would include an open, incomplete, or unreconciled position;
- the implementation would need to infer tax lots or taxable realized P/L;
- real Fidelity assignment or expiration data contradicts the generic mapping;
- an existing public route appears to have an external consumer;
- a change would import `utilities/` or `studies/` into `stock-app/`;
- a required migration would rewrite or delete immutable broker facts;
- automated tests fail for a reason unrelated to the phase; or
- unrelated user changes overlap a file the phase must edit.

Do not turn these stop rules into repeated permission questions for ordinary
implementation details already settled by this document.

## Verification matrix

| Change | Required automated evidence | Manual evidence |
|---|---|---|
| Backend read model/API/persistence | Targeted tests during development; full `stock-app/tests` at Phases 4, 7, and 8 | None before Phase 5 |
| Angular models/services/components | `npm run build` and `npm run test:ci` for each UI phase | Owner checkpoint after Phase 5; final route inspection in Phase 8 |
| Docs | `python3 tools/check_docs.py` | Read for current versus proposed wording |
| Any phase commit | `python3 tools/scan_secrets.py` and `git diff --check` | Confirm staged files match phase scope |
| Migration/cleanup | Synthetic migration tests plus route/reference sweeps | Owner decision before removing uncertain public compatibility |

## Progress log

Append entries; never rewrite older evidence to make progress look cleaner.

| Date | Phase | Status | Evidence / decision | Next action |
|---|---|---|---|---|
| 2026-07-28 | Planning | COMPLETE | Created the proposed grouping, reset, persistence, and API design; documentation checks and secret scan passed; no production code changed | Review migration order |
| 2026-07-28 | Handoff | COMPLETE | Added eight commit-sized phases, automated gates, a Phase 5 owner checkpoint, final Phase 8 UI verification, rollback/stop rules, and kickoff prompt | Start Phase 1 when implementation is requested |
| 2026-07-28 | API design | COMPLETE | Owner approved public brokerage IDs, registry-selected SnapTrade/Tastytrade adapters, canonical facts, common projections, standardized resource contracts, capabilities, and additive compatibility migration | Implement adapters before Symbol Ledger behavior |
| 2026-07-28 | 1 | COMPLETE | Settled decisions frozen as importable test data in `stock-app/tests/brokerage_contract_spec.py` and asserted by `test_brokerage_adapter_contract.py`: public IDs are institutions not connectors, the catalog matches `brokerage_ledger.PORTFOLIOS`, all 21 legacy brokerage routes are still published, all 14 new routes are additive and provider-agnostic, and the settled P/L identities and completeness vocabulary already hold in the live `/brokerage-ledgers` responses for both brokerages. New synthetic characterization: cross-year open/close (Trading, Retirement, and combined), several contracts under one underlying, and the same symbol across multiple Retirement accounts. Gate green (79 targeted, 328 full backend suite); `check_docs`, `scan_secrets`, `git diff --check` clean. No production code changed. Phase 1 gate command amended to include the new contract file | Begin Phase 2: registry, adapters, canonical facts |
| 2026-07-28 | 2 | COMPLETE | Added `stock-app/app/brokerages/` with canonical facts (`contracts.py`), the read interface and shared normalization (`adapters/base.py`), a registry keyed by `fidelity`/`tastytrade` (`registry.py`), and SnapTrade/Tastytrade read adapters. One conformance suite runs the same assertions against both adapters over the same economic position expressed in each artifact family: identical instruments, signs, contract identity, action spelling, and ordering key. Provider differences are now *declared* rather than branched — SnapTrade carries `UNCONFIRMED_PROVIDER_LIFECYCLE` for assignment/exercise and `INDICATIVE` option coverage; Tastytrade reads lifecycle from the Receive Deliver sub-type. `test_only_the_registry_names_a_brokerage` enforces the exit criterion by AST scan of the common modules. Coverage reports `reached_provider_boundary = None` with a stable reason, so retained history is never presented as complete history. Gate green (54 selected, 360 full backend suite); `scan_secrets` and `git diff --check` clean. No route, artifact, or UI changed | Begin Phase 3: common projections and additive read APIs |
| 2026-07-28 | 3 | COMPLETE | Added `app/brokerages/projections/` (components, envelope, holdings, options, option-adjusted-basis), `service.py`, and `routers/brokerages.py`, serving `GET /api/brokerages` plus Holdings, Options, and Option-Adjusted Basis under `/api/brokerages/{brokerage_id}`. All accounting lives in `projections/components.py` and reuses the established `smallfish.brokerage-ledger` v1 vocabulary; parity tests assert the new option-adjusted basis matches `/brokerage-ledgers/*/combined` field for field. Two deliberate corrections, each covered by a test: a provider expiration with no signed delta now resolves against the running position instead of reading as `POSITION_ACTIVITY_MISMATCH` (needed for Phase 4 flatness), and public provenance now names the institution rather than the connector, so `SNAPTRADE` no longer reaches a response. Editable holdings metadata paths moved onto the registry entry so the projection never branches. `ActivityFact.quantity` added (Phase 2 contract extension) because a lifecycle removal reports size without a signed delta. Gate green (89 selected, 395 full backend suite); docs, secrets, `git diff --check` clean. Legacy routes and artifacts untouched | Begin Phase 4: symbol ledger lifecycle, archives, mutation APIs |
| 2026-07-28 | 4 | COMPLETE | Symbol Ledger implemented once on canonical facts and keyed only by `(brokerage_id, normalized_symbol)`. Lifecycle is derived and fails toward Active: a delayed close, an unreconciled position, or an adapter-declared unconfirmed lifecycle keeps the symbol Active with an unavailable P/L rather than presenting a completed archive. Periods are cut by `(executed_at, provider_event_id)`, so a backdated event joins the period it executed in — including a sealed one, which then reports `verification_status = CHANGED` with recomputed values. Reset requires nonempty + flat + reconciled + complete + unchanged `period_version`; staleness is checked before eligibility so a moved period says "refresh". `request_id` makes a retry return the original archive (200) instead of a second boundary. Added `store.py` (atomic, versioned CSVs under three new `SFP_SYMBOL_LEDGER_*` paths, documented in `docs/CONFIGURATION.md`), `events.py` (identity cursors, proven not to skip a row when a backdated event lands mid-page), `sync.py` (common resource names, per-resource report, provider exception type only), and read-only `migration.py` (migrates one unambiguous note per symbol, reports conflicting duplicates without guessing). One correction found by test: a sealed period's P/L was being invalidated by later unrelated activity, because reconciliation is a question about *now*; it is now judged inside its own boundary. Route sweep: 14/14 new routes served, 21/21 frozen legacy routes still served, every Angular caller still resolves. Gate green (452 backend tests); docs, secrets, `git diff --check` clean. No legacy group file stopped being written | Begin Phase 5: shared Symbol Ledger UI, then pause for the owner checkpoint |
| 2026-07-28 | 5 | COMPLETE — automated checks passed; browser verification pending | Added one brokerage-agnostic Angular client (`api/brokerage.service.ts`) and one set of TypeScript contracts (`model/brokerage.ts`) covering discovery, Holdings, Options, Option-Adjusted Basis, and Symbol Ledger; the brokerage id only ever selects a URL, asserted by service tests. Added `shared/symbol-ledger/` and mounted the same component on `/options` (`tastytrade`) and `/retirement` (`fidelity`), replacing the Trade Groups table. The new surface has no group name, no editable status, and no event-reassignment control; notes stay editable. Rows show derived Active/Archived, reconciliation state, accounts, event counts, current-period and lifetime P/L with completeness, coverage start beside lifetime, and warnings; detail adds account-aware components and provenance. Unproven rows are flagged on the row edge as well as by badge, and unavailable values render `—`. Component tests run the same assertions for both brokerage inputs and cover empty, loading, error, filter, search, notes, incomplete P/L, unsynced availability, and narrow-width scrolling; a test asserts the response never surfaces the connector name. Legacy `brokerage-option-groups` component retained on disk unmounted as the rollback boundary. Gate green: `npm run build` clean, `npm run test:ci` 55 passed; secrets and `git diff --check` clean | **Owner checkpoint: inspect `/options` and `/retirement` and confirm before Phase 6 starts** |
| 2026-07-29 | 5 (fix) | COMPLETE — automated checks passed; browser verification pending | Owner reported a symbol reading Unreconciled despite a manual reconciliation row. Reproduced with synthetic data and found three defects, all mine, none in the manual-reconciliation path: (1) an expiration whose opening trade predates the retained window was treated as an unexplained blank delta instead of a removal that closes zero, producing a mismatch no manual row could ever clear; (2) equity components were reconciled against retained share executions and could take a cash basis from them, though those cover only the fetch window — equity now always uses the broker cost basis, matching the compatibility view; (3) events with no surviving component — a closed share lot, a manual correction — were dropped from the symbol's history entirely, so the user's own correction was invisible. The Symbol Ledger now retains every event for the symbol for counts and history while money still comes from components. Added a `CLOSED_EQUITY_UNSUPPORTED` reason so a symbol whose shares closed reports an unavailable total with a stated cause rather than presenting its option-only figure as complete; such a symbol stays Active and cannot be reset. Six regression tests added. Full backend suite 457 passed; `npm run build` clean, `npm run test:ci` 55 passed; docs, secrets, `git diff --check` clean | Owner checkpoint still open |
| 2026-07-29 | 5 (fix 2) | COMPLETE — automated checks passed; browser verification pending | Owner reported the previous fix's `CLOSED_EQUITY_UNSUPPORTED` message firing on many symbols. It was the wrong rule: Tastytrade *does* import share executions for option-traded underlyings, so "closed equity is not imported" was false for that brokerage, and treating every closed share lot as unknowable also made the manual reconciliation row meaningless in the new ledger. Replaced it with the evidence that actually settles the question — reconciliation. Equity components are built again even with no current position, so retained share executions are compared to the broker like option contracts are: a lot that opened and closed inside retained history reconciles and contributes its real cash; one whose opening trade predates the window reads `UNRECONCILED` with a remedy, and entering the missing trade clears it and releases the cash. Shares still held keep the broker cost basis when the window does not cover their opening lots — incomplete history (`EQUITY_ACTIVITY_HISTORY`), not a disagreement. `CLOSED_EQUITY_UNSUPPORTED` and the now-unused `equity_events_by_symbol` helper removed; portfolio-level closed-equity coverage remains where it always was, in the coverage block. Full backend suite 458 passed; `npm run build` clean, `npm run test:ci` 55 passed; docs, secrets, `git diff --check` clean | Owner checkpoint still open |
| 2026-07-29 | Handoff | COMPLETE | Owner confirmed the corrected symbol and P/L behaviour on the Trading ledger and asked for a handoff-ready document. Added a "Resume here" section (commit map, current suite totals, next action), a "Deviations from the original design" list so a later agent does not revert six deliberate, test-covered choices, and a "Known open questions for the owner" list. Rewrote the new-session kickoff prompt to resume at Phase 6 instead of restarting at Phase 1 — following the old prompt would have redone the whole backend. Dashboard now distinguishes per-phase historical totals from current ones, and records the Phase 5 checkpoint as partially confirmed rather than approved. Coordination mailbox annotated as deliberately empty. Docs check passed; no code changed | Owner to approve the remaining Phase 5 checkpoint items, then Phase 6 |
| 2026-07-29 | 5 (fix 3) | COMPLETE | Owner reported that both Options tabs repeated equity-only holdings already shown under Holdings. Added the brokerage-neutral `exposure=options` Symbol Ledger filter so rows and aggregate counts include `OPTIONS` and `EQUITY_AND_OPTIONS` but exclude `EQUITY`; the shared Angular component requests it for both brokerages and retains the same rule defensively for rendering, search, result counts, empty states, and expanded-row cleanup. Regression coverage runs the backend filter and shared component against Fidelity and Tastytrade. Full backend suite 460 passed; `npm run build` clean; `npm run test:ci` 57 passed. Live verification: Trading showed 16 of 16 option-capable ledgers instead of 21 all-symbol ledgers; Retirement showed 10 of 10; neither rendered an equity-only row and both retained mixed equity-and-option rows | Owner checkpoint still open; approve Phase 5 before Phase 6 |
| 2026-07-29 | 5 (fix 4) | COMPLETE | Owner requested a denser Option-Adjusted Basis table on both ledgers. Removed the redundant `Equity P/L / Share` display column, reduced the Share Position group and expanded-detail spans consistently, and shortened `Option Adjusted Basis / Share` to `Adjusted Basis / Share` without changing its formula or response field. Component coverage asserts the removed and retained headers plus the 12-column detail span. `npm run build` clean; `npm run test:ci` 57 passed. Live verification confirmed the corrected shared headers on both Trading and Retirement | Owner checkpoint still open; approve Phase 5 before Phase 6 |
| 2026-07-29 | 5 (fix 5) | COMPLETE | Owner clarified that Option-Adjusted Basis is exclusively a current-position view: a symbol must have both open long shares and an open option position. The shared component now enforces that from account-aware components, removing rows with only historical/flat option or equity components. Removed the redundant State filter, Open row badge, and Open matched symbols card. Renamed the retained safety card to `Basis unavailable` and corrected it to count only adjusted-basis `UNAVAILABLE`; normal live `INDICATIVE` values no longer inflate the count. Component coverage distinguishes open/open from open/flat combinations and proves one unavailable calculation among two displayed open matched rows. `npm run build` clean; `npm run test:ci` 57 passed. Live verification: Trading currently has no open/open matches and reports zero unavailable; Retirement has six open/open matches and reports all six unavailable under its existing lifecycle limitation; both routes show three cards with no State selector or Open badge | Owner checkpoint still open; approve Phase 5 before Phase 6 |
| 2026-07-29 | 5 (fix 6) | COMPLETE | Corrected the fix 5 scope after live Trading review showed that BTU disappeared: its shares are open and its completed option cycle still changes the adjusted basis, but it has no currently open option contract. Option-Adjusted Basis now requires open long shares plus option history, retaining completed option cycles until the related shares close while still excluding equity-only holdings and symbols without open shares. Updated the shared explanation, empty state, durable UX guidance, and component coverage for an open-equity/flat-option row. Focused component tests 2 passed; `npm run build` clean; `npm run test:ci` 57 passed; docs, secrets, and `git diff --check` clean. Live verification restored BTU in Trading and confirmed the shared Retirement view still loads with the corrected explanation | Owner checkpoint still open; approve Phase 5 before Phase 6 |
| 2026-07-29 | 5 checkpoint | COMPLETE | Owner approved moving to the next phase after reviewing the shared ledger corrections | Phase 6 authorized |
| 2026-07-29 | 6 | COMPLETE — automated checks passed; browser verification pending | Added the complete history/archive workflow to the brokerage-agnostic shared Symbol Ledger used by both Trading and Retirement. Detail loads immutable current-period history by default, supports All history and any archived period on demand, and paginates with the API's opaque cursor. Compact archive summaries show realized P/L, verification state, and late-event changed warnings. Archive completed history appears only when the API marks the loaded period eligible; a shared confirmation modal names the symbol, event count, period, and realized P/L. A `PERIOD_CHANGED` conflict prompts a fact refresh, while any uncertain retry reuses the same request ID so the backend returns the original archive rather than creating another. No imported event is editable, moved, or deleted. Component coverage adds pagination, archive detail, changed archive warnings, reset confirmation/success, conflict refresh, and idempotent retry. Focused suite 21 passed; `npm run build` clean; full `npm run test:ci` 62 passed | Phase 7 requires consumer audit and owner compatibility decision; Phase 8 will perform browser verification |
| 2026-07-29 | 7 | COMPLETE — automated checks passed; browser verification pending | Owner confirmed legacy brokerage routes are not externally consumable. Replaced the remaining per-brokerage pages with one shared brokerage shell: Holdings and Option-Adjusted Basis keep their internal compatibility projections, while Options is solely the brokerage-neutral Symbol Ledger. Common brokerage sync now suppresses legacy group/membership writes. Legacy group-creation, group-update, and event-reassignment routes remain explicit 410 tombstones; non-mutating legacy projections and CSV artifacts remain internal rollback compatibility. Removed the unused Trade Groups and Broker Risk Angular models/components, and updated all required behavior docs. Full Phase 7 gate passed: backend 461, Angular build, Angular 55, docs, secret scan, and diff check. | Phase 8 final regression and browser verification on `/options` and `/retirement` |
| 2026-07-29 | 8 | BLOCKED — non-mutating checks complete | Re-ran the full automated gate: backend 461, Angular build, Angular 55, docs, secret scan, and diff check. After checking owners of ports 8000 and 4200, did not trust their noncanonical development processes. Built the UI with `./commands.sh build-ui`, served an isolated backend through `./commands.sh server --no-reload --port 8001`, and inspected both `/options` and `/retirement`. Both expose the common Holdings, Options, and Option-Adjusted Basis tabs; Options uses Symbol Ledger with Active, Archived, and All filters and an immutable-event detail, without Trade Groups or Broker Risk UI. Retirement also fit a narrow viewport. No archive/reset/reopen was performed against real brokerage data. | Owner: authorize a synthetic-copy lifecycle browser check, or a real-symbol reset/archive and reopen, before Phase 8 is marked COMPLETE |
| 2026-07-29 | Adjusted-basis follow-up | COMPLETE | Replaced the last legacy combined UI projection with the brokerage-neutral Option-Adjusted Basis endpoint. The Retirement data has seven matched rows, but only two are genuinely unavailable, both because option P/L cannot be allocated safely across accounts. The shared table now presents the actual per-symbol basis reason instead of a provider-wide lifecycle warning. Focused test and full Angular suite (55) passed; build was clean. An isolated documented server confirmed the Retirement card reads 2 with the cross-account reason visible, and both Basis tabs request their brokerage-neutral endpoint. Docs, secrets, and diff checks passed. | Phase 8 remains blocked only on the separately authorized archive/reset/reopen lifecycle check |
| 2026-07-29 | 8 completion | COMPLETE | Used a fresh synthetic-only data root and an isolated documented server; no brokerage artifact was read or written. In the browser, archived a flat reconciled symbol through the normal confirmation dialog, added a synthetic reopening event and confirmed exactly one active current period while the completed archive remained, then added a synthetic backdated event and confirmed the changed-archive warning. Removed the temporary fixture by moving it to Trash. Final full automated gate passed: backend 461, Angular build, Angular 55, docs, secret scan, and diff check. | No further action |
| 2026-07-29 | Lifecycle vocabulary follow-up | COMPLETE | Owner clarified that a flat derived symbol is `CLOSED`, reserving archive terminology for explicitly sealed historical periods. The common Symbol Ledger projection, list filter (`state=closed`), summary (`closed_count`), Angular contract, badges, and filters now use Closed; archive-period data remains explicitly archived. Full backend suite 461 passed; Angular suite 61 passed; build, docs, secret, diff, and live Trading/Retirement verification recorded after their final gates. | No further action |
| 2026-07-29 | History empty-state follow-up | COMPLETE | Owner removed the current-period empty message as needless visual space. Empty current event history now renders no card or text; archived-period empty states remain explicit because they appear only after the user selects an archive. Focused Symbol Ledger component suite 27 passed; Angular build, docs, and diff checks clean. | No further action |
| 2026-07-29 | Adjusted-basis account scope follow-up | COMPLETE | Owner clarified that option-adjusted basis is a brokerage-symbol calculation, not an account allocation: same-symbol equity and option components now combine across accounts for both Trading and Retirement. Account labels remain visible as provenance. Removed the cross-account blocker from the common projection and internal compatibility calculation; other missing-cost, history, reconciliation, marks, and lifecycle safeguards remain. Added cross-account Trading and Retirement regressions. Full backend suite 462 and Angular suite 61 passed; build, docs, secrets, and diff checks clean. Live Retirement verification confirmed NFLX and SNOW no longer carry the cross-account blocker. | No further action |
| 2026-07-29 | Adjusted-basis summary follow-up | COMPLETE | Owner removed the zero-value `Basis unavailable` summary as visual noise. The shared Option-Adjusted Basis component now renders that warning card only when one or more displayed rows are genuinely unavailable; a component regression covers the zero-count case. Focused component suite 3 passed; Angular build, docs, secrets, and diff checks clean. | No further action |
| 2026-07-29 | Legacy cleanup start | IN PROGRESS | Consumer audit confirms both pages already use the common shared shell, Symbol Ledger, and Option-Adjusted Basis client. Removed the unused legacy Combined Ledger Angular client method and response models; no template or route called them. Holdings remains the final active `/brokerage-ledgers/*` consumer because its compatibility contract still supplies editable metadata, G/L snapshot columns, and declining-trend state. The common Holdings endpoint must acquire those values before its compatibility router and backend can be safely removed. | Extend the common Holdings contract, then cut over the shared Holdings component |
| 2026-07-29 | Symbol Ledger summary follow-up | COMPLETE | Owner removed the zero-value `Needs review` summary as visual noise. The shared Symbol Ledger now renders it only when one or more option-capable rows need review; component coverage protects the zero-count case. Focused component suite 28 passed; Angular build, docs, and diff checks clean. | Continue legacy Holdings migration |
| 2026-07-29 | Archive control follow-up | COMPLETE | Owner simplified archive affordance: the shared Symbol Ledger displays only an `Archive completed history` action when the API declares the loaded period eligible. Ineligible periods render no card, blocker, or status guidance. Focused component coverage (28 tests), Angular build, docs, secret scan, and diff checks passed. | Continue legacy Holdings migration |
| 2026-07-29 | Handoff refresh | COMPLETE | Refreshed the top-level resume state, commit map, cleanup dashboard, handoff boundary, and new-session kickoff for the next agent. The next task is a consumer-first Holdings migration; Phase 8 and all lifecycle verification are complete and must not be repeated. Docs and diff checks passed. | Extend the common Holdings contract, then migrate its shared UI consumer |
| 2026-07-29 | Holdings contract comparison | COMPLETE | Compared the legacy Holdings contract the UI consumes against the common endpoint, field by field, against the component template rather than the model file. Most of the table already maps cleanly: symbol, account, category/industry/note, qty, cost/share, market price, invested, current, G/L $ and %, and the three portfolio totals all have common equivalents, as do `retrievedAt` (`as_of.positions`) and `source` (`brokerage.institution`). Four genuine gaps remain: per-row `pctOfTotal`; portfolio `totalGainLossPct`; per-row and catalogue gain/loss snapshots (`holdings.read_snapshots` exists but is dead code, never wired into `build`); and `trend`, which has no representation in `brokerages/` at all and is written by two provider-specific writers into two differently-keyed CSVs. Two smaller ones: `enrichmentSymbol`, which the edit modal writes against, and the snapshot-capture response, which legacy returns as `{snapshot, portfolio}` so the UI can swap in refreshed holdings. `byCategory`/`byIndustry`/`byAccountType`/`topPositions` are computed by both legacy implementations but rendered by neither; they do not need porting. The Angular common client has no metadata or snapshot-capture method yet. | Close the four contract gaps before touching the component |
| 2026-07-29 | Holdings gap 1 — flat equity | COMPLETE | The comparison found a defect rather than a missing feature, so it was fixed first. The common Holdings endpoint listed share lots that are no longer held: `build` took every `EQUITY` component, and since the Phase 5 fix 2 those are built for closed lots too so the Symbol Ledger can reconcile their cash. A sold lot therefore appeared as a zero-quantity row whose realized result was reported as `unrealized_pnl`, and — because a closed lot's cost basis is its net proceeds negated — it subtracted that profit from `total_cost_basis`. A worked case: one lot bought at 11000 and sold at 12000, beside a held lot invested 900 and marked 1000, reported invested −100 and unrealized 1100 instead of 900 and 100. `capture_snapshot` shared the fault and would have written a meaningless percentage for the sold lot into the retained comparison columns. Holdings now takes only `state == "OPEN"` equity through one `held_equity` helper used by both paths. Deliberate difference from legacy Trading: a short share position is now shown, matching the SnapTrade reference implementation, where legacy Tastytrade filtered to `signed_quantity > 0`. Four regressions added across both brokerages; the two Tastytrade cases fail without the fix, and the Fidelity cases pin the shared contract because SnapTrade imports no equity activity and drops zeroed positions at the adapter. Full backend suite 466 passed (was 462); docs, secret scan, and `git diff --check` clean. No UI change, so no route inspection was required. | Gap 2: `pct_of_total` and portfolio return percentage |
| 2026-07-29 | Holdings gaps 2-4 and UI cutover | COMPLETE | Closed the remaining contract gaps and migrated the consumer. `pct_of_total` and `total_unrealized_pnl_pct` are computed once in the projection and fail closed: one unmarked holding makes the portfolio total unknown, so every row's share blanks rather than silently rebasing on a partial denominator — legacy divided by whatever it could add up. Captured gain/loss percentages now reach the response as a per-row map plus a retained-date catalogue in `summary`, wiring up `read_snapshots`, which had been dead code since Phase 3; the catalogue lives in `summary` because the envelope key set is frozen by the contract spec. Declining-trend state is read through a new registry-selected `holdings_trend_path`: both brokerages already wrote the same columns under the same `(account, symbol)` key to their own file, so no branch was needed. The two sync-time trend *writers* stay where they are for now — they are provider bookkeeping, and consolidating them belongs with the legacy removal. Pre-cutover captured percentages are carried into the common store by a new idempotent `migration.migrate_gain_loss_snapshots()`, invoked from `run_sync` so it self-heals rather than depending on a one-shot step; legacy snapshot files are read, never rewritten. `BrokerageHoldingsComponent` now consumes only `BrokerageService`, keyed by brokerage id, with new `updateHoldingsMetadata` and `captureGainLossSnapshot` client methods; it takes its heading, institution and timestamp from the response and shows the availability banner, so an unsynced brokerage reads "Nothing imported yet" instead of an empty portfolio. The legacy `portfolio` slug input is gone from the shell and both page shells. Backend 478 passed (was 462); Angular 69 passed (was 62); build clean; docs, secrets, diff checks clean. Browser verification on an isolated port-8001 server over a synthetic-only data root: Trading showed the Return card, % Portfolio column, the declining-row highlight with its badge, and the migrated `G/L % as of Jul 20, 2026` column with a `—` for the holding that had no measurement; Retirement showed the shared component with the Fidelity label and the nothing-imported banner; both routes requested only `/api/brokerages/{id}/holdings`; no console errors; at 375px the page did not scroll horizontally while the table scrolled inside its own container. | Remove the legacy Holdings route, client, and backend projection |
| 2026-07-29 | Legacy Holdings removal | COMPLETE | Removed `/brokerage-ledgers/{portfolio}/holdings` and its enrichment and gain/loss-snapshot write paths, `app/brokerage_holdings.py`, and the Angular `BrokerageLedgerService` with the `brokerage-holdings` and `brokerage-ledger` models — all proven unreferenced by sweep first. The trend writer that lived in the deleted module did not move: the two providers turned out to hold the *same* rule, character for character, differing only in how a percentage is read off a provider row, so the rule now lives once in `brokerages/trend.py` and each provider contributes normalized observations. That also pulled the reader and display block out of the Holdings projection, which should calculate rather than parse. `snaptrade_service._update_trend` is now a thin adapter over the same function. `brokerage_contract_spec` gained `RETIRED_LEGACY_ROUTES` beside the frozen list, and a test asserting the retired routes are really unpublished, so a contract cannot be quietly dropped from one list and left half-served. The Phase 1 characterization that both legacy Holdings views shared one shape was deleted with the route it described; what it protected is asserted against the surviving contract by `test_each_resource_has_one_shape_across_brokerages`. Eight new tests cover the shared trend rule directly, including the cases neither old copy tested: a slow sub-threshold slide still accumulating to an alert, a recovery clearing one, a near-breakeven holding never alerting, and a holding no longer held being dropped rather than alerting against a position that does not exist. Retained deliberately: `/brokerage-ledgers/{portfolio}/combined` as the parity baseline, and `snaptrade_service.portfolio` because `/retirement/holdings/*` is still a frozen contract and `retirement_options.py` still calls it. Backend 481 passed; Angular 69 passed; build, docs, secrets, diff checks clean. Browser verification over a synthetic-only data root confirmed the published route table (`/combined` still JSON, the retired path now falling through to the SPA rather than answering), and both write paths end to end: a captured snapshot and a metadata edit each round-tripped through the common API, with the newly captured date and the migrated pre-cutover date showing side by side as two comparison columns. | Audit the legacy Retirement holdings routes, then group-only backend paths |

## New-session kickoff prompt

Copy the following into a new implementation session to continue the remaining
cleanup. It replaces the old Phase 6/8 handoff; do not resume those completed
phases.

```text
Continue the smallFish brokerage legacy cleanup. Read the "Resume here" and
"Current handoff boundary" sections of docs/BROKERAGE_REFACTOR_PLAN.md first,
then AGENTS.md, Requirements.md, docs/BROKERAGE_LEDGER_COMBINED_VIEW.md,
docs/ARCHITECTURE.md, and stock-app/README.md. Before UI work also read
stock-app-ui/AGENTS.md and stock-app-ui/docs/UX_GUIDANCE.md.

State when you pick this up:
- Phases 1-8 are complete and committed on main. The final synthetic lifecycle
  browser check is complete. Do not restart any phase or repeat Phase 8.
- `/options` and `/retirement` use the shared brokerage shell and Symbol Ledger;
  the Option-Adjusted Basis tab uses the common brokerage client. The Symbol
  Ledger intentionally hides a zero `Needs review` card and renders archive
  controls only for eligible periods.
- All 14 `/api/brokerages` routes remain the target contract. Legacy group
  mutations are 410 tombstones and common sync no longer writes group or
  membership artifacts.
- Holdings is fully migrated. The common endpoint carries editable enrichment,
  gain/loss snapshot columns, and declining-trend state; the legacy route, its
  write paths, `app/brokerage_holdings.py`, and the Angular legacy client and
  models are removed. No Angular code calls `/brokerage-ledgers/*`; `/combined`
  is all that remains there and is kept as the parity-test baseline.
- The peak/adverse-move trend rule lives once in `app/brokerages/trend.py`.
  Providers supply normalized observations; do not reintroduce a per-provider
  copy of the rule.
- Last broad verified baselines are backend 481 passing, Angular 69 passing,
  and a clean Angular build. Re-run the full relevant suite before calling a
  cleanup step complete.

Start with the legacy Retirement holdings surface: `/retirement/holdings/sync`,
`/retirement/holdings/gain-loss-snapshots`, `/retirement/enrichment/{symbol}`,
and `snaptrade_service.portfolio`. It has no Angular consumer but is still a
frozen contract, and `retirement_options.py` still calls `portfolio()` for a
portfolio total, so re-point that caller at the common projection before
removing anything. Apply the same order Holdings used — common contract first,
then consumers, then route/reference sweeps, then removal — and record a
retirement in `RETIRED_LEGACY_ROUTES` rather than silently dropping it from the
frozen list. Audit group-only code separately; retain ingestion/parsing code
that still produces canonical facts.

Continue under this protocol:
1. Keep each cleanup increment focused and independently reversible. Do not
   mix a behavior change with unrelated deletion.
2. Run the targeted backend/UI tests, relevant full suite, Angular build for UI
   changes, `python3 tools/check_docs.py`, `python3 tools/scan_secrets.py`, and
   `git diff --check` before committing.
3. Update the dashboard and append a new progress-log row in every cleanup
   commit. Never rewrite older log evidence.
4. Inspect the affected route with representative/synthetic data for UI work;
   never print real positions, accounts, or provider details.
5. Do not push or open a PR unless asked.

Non-negotiable boundaries (unchanged):
- Never modify or delete imported broker facts.
- Never allow two symbol ledgers for the same brokerage and symbol.
- Never hide missing history, marks, reconciliation, or staleness by showing a
  partial number as complete.
- Remove legacy routes only after the corresponding consumer and compatibility
  audit proves they are unused; the owner has authorized that cleanup but not a
  loss of Holdings behavior.
- stock-app must not import utilities or studies.
- Tests must not contact the network or contain real financial data.
- Do not infer brokerage tax lots, taxable realized P/L, assignment shapes, or
  external API usage.

Cross-agent questions go in docs/BROKERAGE_REFACTOR_COORDINATION.md using its
template and a new sequential ID. Stop the phase for Blocking: yes; for
Blocking: no proceed only with the documented safe default and record it.
```
