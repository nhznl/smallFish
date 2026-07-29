# Brokerage API and ledger refactor plan

**Status:** Proposed implementation handoff; no production behavior implements
this contract yet. This document is the source of truth for implementation,
phase status, decisions, verification evidence, and the next action.

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
open exposure, and becomes Archived when the symbol is confidently flat.

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
   Archived. A new open position makes the symbol Active. A symbol becomes
   Archived only after all of its account-level equity and option positions are
   flat and the activity reconciles with the broker snapshot.
6. **Uncertainty fails toward Active.** If smallFish cannot prove that a symbol
   is flat because a close is delayed, activity is missing, or reconciliation
   fails, the symbol remains Active with a visible warning and unavailable P/L.
   It must not be presented as a completed archive.
7. **Normal reopening preserves the tally.** A new trade in an Archived symbol
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
| Every component is flat and activity reconciles | `ARCHIVED` | Current-period P/L can be `COMPLETE` |
| Flatness cannot be established safely | `ACTIVE` | P/L is `UNAVAILABLE` and warnings explain why |

This preserves simple Active/Archived filtering without hiding incomplete
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
- the symbol is `ARCHIVED` because every component is flat;
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
- the symbol remains Archived until a new position opens;
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
GET /api/brokerages/{brokerage_id}/symbols?state=active|archived|all
```

`state` defaults to `active`. The list is a compact summary suitable for the
main table. Search and sorting remain client-side initially because the local
ledger is small.

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
   The generated name and manual Active/Archived value are not authoritative.
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
- Default to Active; retain Active, Archived, and All filters.
- Derive the badge from API state; remove editable status and group name.
- Keep notes editable.
- The detail view shows current positions, current-period totals, lifetime
  totals, archived-period summaries, immutable events, provenance, and
  reconciliation warnings.
- Show **Archive completed history** only for a nonempty, complete, reconciled,
  Archived current period.
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
- A fully flat, reconciled symbol becomes Archived without a metadata write.
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
  Trading and Retirement routes, Active/Archived filters, detail history, and
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
6. Symbol lists default to `active`; callers may request `archived` or `all`.
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
- Active/Archived is derived from exposure rather than a user label; and
- the user may explicitly reset completed history into an archived period; and
- the **Settled API decisions** above govern the new backend and UI contracts.

## Phase dashboard

Update this table in the same commit that completes a phase. Valid states are
`NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `COMPLETE`. Evidence should name
tests, commits, or owner confirmation; do not write "verified" without the
corresponding evidence.

| Phase | Scope | Status | Next action / blocker | Evidence |
|---|---|---|---|---|
| 1 | Contract baseline and characterization | COMPLETE | Phase 2 may begin | `stock-app/tests/brokerage_contract_spec.py` + `test_brokerage_adapter_contract.py` (14 tests); new characterization cases in `test_options_activity.py`, `test_retirement_options.py`, `test_brokerage_ledger.py`; full backend suite 328 passed |
| 2 | Brokerage registry, adapters, and canonical facts | COMPLETE | Phase 3 may begin | `stock-app/app/brokerages/` (contracts, registry, SnapTrade + Tastytrade adapters); shared conformance suite `test_brokerage_adapters.py` (32 tests) parametrized over the registry; full backend suite 360 passed |
| 3 | Common projections and additive read APIs | COMPLETE | Phase 4 may begin | `app/brokerages/projections/` + `service.py` + `routers/brokerages.py`; `GET /api/brokerages`, `/holdings`, `/options`, `/option-adjusted-basis`; `test_brokerage_api.py` (35 tests) incl. parity against `/brokerage-ledgers/*/combined`; full backend suite 395 passed |
| 4 | Symbol Ledger lifecycle, archives, and mutation APIs | COMPLETE | Phase 5 may begin | `projections/symbol_ledger.py`, `projections/events.py`, `store.py`, `sync.py`, `migration.py`; all 14 settled routes now served and all 21 frozen legacy routes still served; `test_symbol_ledger_api.py` (56 tests); full backend suite 452 passed |
| 5 | First shared Trading/Retirement UI slice | COMPLETE (awaiting owner checkpoint) | **Owner: inspect `/options` and `/retirement`.** Phase 6 does not start until you approve | `model/brokerage.ts`, `api/brokerage.service.ts`, `shared/symbol-ledger/`; mounted on both pages; `npm run build` clean, `npm run test:ci` 55 passed (19 new). Automated checks passed; browser verification pending |
| 6 | History/reset UX and shared UI consolidation | BLOCKED | Blocked until owner approves Phase 5 | - |
| 7 | Compatibility cutover, cleanup, and current-behavior docs | NOT STARTED | Depends on Phase 6 and consumer audit | - |
| 8 | Full regression, browser verification, and handoff closeout | NOT STARTED | Depends on Phase 7 | - |

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
- Derive Active/Archived, current-period/lifetime P/L, coverage, and
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
- Show one row per symbol, Active/Archived/All filters, coverage start,
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
2. Ask whether navigation, Active/Archived filtering, symbol details, notes,
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
- Verify Active, Archived, and All; symbol detail; account components; complete,
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

## Opus new-session kickoff prompt

Copy the following into a new implementation session when implementation is
requested:

```text
Implement the smallFish Symbol Ledger migration using
docs/BROKERAGE_REFACTOR_PLAN.md as the source of truth.

Repository context:
- Work in the existing smallFish checkout.
- Read AGENTS.md, Requirements.md, docs/BROKERAGE_REFACTOR_PLAN.md,
  docs/BROKERAGE_LEDGER_COMBINED_VIEW.md, docs/ARCHITECTURE.md, and
  stock-app/README.md before editing.
- Before Phase 5 UI work, also read stock-app-ui/AGENTS.md and
  stock-app-ui/docs/UX_GUIDANCE.md.
- Inspect git status first. Preserve unrelated user changes and remain on the
  current branch unless the owner asks for a branch.

Product outcome:
- Add brokerage-agnostic APIs rooted at /api/brokerages/{brokerage_id} for
  discovery, sync, Holdings, Options, Option-Adjusted Basis, and Symbol Ledger.
- Use public brokerage IDs fidelity and tastytrade. Resolve them through a
  backend registry to SnapTrade and Tastytrade adapters.
- Make provider adapters emit canonical facts; compute final resource responses
  once in common projections. Angular must never interpret provider fields.
- Replace user-managed option trade groups with exactly one durable symbol
  ledger per (brokerage_id, normalized symbol).
- Associate immutable events through their normalized underlying, not a group
  membership table.
- Derive Active/Archived from reconciled exposure.
- Preserve running current-period and retained-history lifetime P/L across
  ordinary close/reopen cycles.
- Allow an explicit reset only for a nonempty, flat, reconciled, complete, and
  unchanged current period. Reset creates a logical archive boundary and never
  rewrites or moves broker facts.
- Keep account identity and coverage account-aware. Do not implement tax-lot
  accounting or call a provider from a read endpoint.

Execution protocol:
1. Follow the eight phases in order. Update the dashboard and append the
   progress log in every phase commit.
2. Each phase is a focused commit. Run its automated gate before committing and
   stage only that phase's files.
3. Do not run browser checks after every phase. After the Phase 5 automated
   checks and commit, pause and ask the owner to inspect /options and
   /retirement. Do not start Phase 6 until the owner explicitly approves.
4. After approval, continue through Phases 6-7 without additional browser
   pauses when automated gates pass. Mark intermediate UI work as browser
   verification pending.
5. In Phase 8, run the full automated regression and final browser verification
   on both routes. Fix and reverify any issue before closeout.
6. Do not push or open a PR unless asked.

Cross-agent questions:
- Use docs/BROKERAGE_REFACTOR_COORDINATION.md as the shared mailbox with the
  Codex architecture/coordination task.
- Append questions using its exact template and a new sequential question ID.
- For Blocking: yes, stop the current phase after leaving the checkout in a
  safe state. Do not commit an unresolved interpretation.
- For Blocking: no, continue only with the safe default already documented in
  the plan and record that default in Provisional action.
- Poll the file for a matching Response to Q-NNN section. An ANSWERED response
  permits work within the stated boundary. OWNER_INPUT_REQUIRED remains blocked
  until the owner decides and the plan is amended if necessary.
- Include resolved question/response entries in the related phase commit; do
  not mark or commit a blocked phase as complete while its decision is open.
- Do not ask the monitor to edit code, make commits, relax tests, or override a
  settled product decision.

Non-negotiable boundaries:
- Never modify or delete imported broker facts.
- Never allow two symbol ledgers for the same brokerage and symbol.
- Never hide missing history, marks, reconciliation, or staleness by showing a
  partial number as complete.
- Preserve existing API shapes and routes until their consumers are migrated
  and the Phase 7 compatibility audit permits cleanup.
- stock-app must not import utilities or studies.
- Tests must not contact the network or contain real financial data.
- Do not infer brokerage tax lots, taxable realized P/L, assignment shapes, or
  external API usage.

Start with Phase 1 only. Confirm the owner-approved API decisions are recorded,
run the baseline tests, make the focused Phase 1 commit when green, update the
plan, and then continue phase by phase under the protocol above.
```
