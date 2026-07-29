# Brokerage ledger normalized views

## Objective

Give the Trading (Tastytrade) and Retirement (Fidelity through SnapTrade)
pages the same portfolio vocabulary and the same brokerage-tab structure, while
leaving provider-specific ingestion behind the API boundary.

Each brokerage page exposes Holdings, Options, and Option-Adjusted Basis.
Provider-specific sync, reconciliation, enrichment, and editing workflows stay
within the applicable asset view.

## Product decisions

1. The UI consumes one versioned response shape for either portfolio. It must
   not branch on Tastytrade versus SnapTrade field names.
2. A symbol summary is the primary row. Expanding it shows its equity position,
   option groups or legs, accounts, lifecycle state, provenance, and notes.
3. Imported broker executions remain immutable. Closing an equity or option
   position changes the computed lifecycle state; it does not rewrite an
   opening event from `STO` to `BTC` or from buy to sell.
4. Broker values and smallFish calculations are labelled separately. Missing,
   stale, unreconciled, or incomplete inputs render as `-` and a visible reason,
   never as zero or a partial total.
5. The user-facing term is **option-adjusted basis**, not "true price". The
   latter sounds authoritative even when transaction history or marks are
   incomplete.
6. Account boundaries remain visible for provenance. For option-adjusted basis,
   all equity and option components for the same symbol are combined within a
   brokerage portfolio, regardless of account.

## Information architecture

The application keeps its brokerage-level navigation. Trading and Retirement
both expose exactly three brokerage-data tabs in the same order:

- **Holdings:** open equity positions only. Both brokerages render one shared
  table using the Retirement holdings vocabulary. Category, industry, note,
  trend state, and user-captured G/L snapshots remain editable local metadata,
  separate for each brokerage and separate from imported broker facts.
- **Options:** option positions, groups, lifecycle activity, and risk. The
  current Trading options vocabulary is the reference for both brokerages.
- **Option-Adjusted Basis:** only symbols with an open long-equity position and
  option activity that affects its basis. A completed option cycle remains
  relevant while the shares remain open.

The Option-Adjusted Basis tabs render the same shared Angular component with a
portfolio identifier (`TRADING` or `RETIREMENT`). Provider-specific sync
actions remain in the page header.

### Holdings view

Holdings uses one shared table and contains no allocation charts. Category and
account filters appear only when the current brokerage has more than one
available value. Search, Declining only, Snapshot G/L %, Copy Symbols, sorting,
dynamic snapshot columns, and the category/industry/note editor behave the same
on Trading and Retirement. Up to three brokerage sync dates are retained for
the user-captured G/L columns.

### Symbol summary table

The Option-Adjusted Basis table keeps share economics together first, option
economics second, and combines them only in the final columns. Exposure and
state are not shown because every included symbol has open long shares and
option history:

| Column | Meaning |
|---|---|
| Symbol | Underlying; sticky when the table scrolls |
| Current Price / Share | Current equity mark; empty when no share position supplies it |
| Share Qty | Current long shares; empty for options-only symbols |
| Cost Price / Share | Positive equity cost divided by shares |
| Cost Price | Positive total equity cost |
| Current Equity | Positive current equity market value |
| Equity P/L | Current Equity minus Cost Price |
| Net Credit | Non-negative option cash received; empty for equity-only symbols |
| Net Debit | Non-positive option cash paid; empty for equity-only symbols |
| Option P/L | Option lifecycle cash plus signed open option value |
| Net P/L | Equity P/L plus Option P/L, using only the applicable blocks |
| Adjusted Basis / Share | `(Cost Price - Option P/L) / Share Qty` |

Summary cards above the table include only the displayed open share positions
with option history.
When one or more rows have adjusted-basis completeness `UNAVAILABLE`, the
`Basis unavailable` card counts them. It is omitted when the count is zero;
the normal `INDICATIVE` state for a live marked position does not count as
unavailable. Search is the only table filter. Warnings and completeness are
not filter gates.

### Expanded symbol detail

The expanded region uses this normalized decomposition:

| Column | Meaning |
|---|---|
| Type | Open/closed equity, short/long call, or short/long put |
| Account | Broker account label; provenance only; basis aggregates same-symbol components across accounts |
| Qty | Signed shares for equity; signed contracts for options |
| Contract | Strike and expiry/DTE for options; `-` for equity |
| Cash in | Non-negative credits, including proceeds and premiums |
| Cash out | Non-positive debits, including purchases and closing costs |
| Mark / unit | Share mark or option mark, with observation timestamp |
| Market value | Signed value of the open position |
| P/L | Net cash plus open market value, or net cash when flat |
| Events | Execution count; distinct from open leg count |
| Notes | Symbol/group annotations without modifying broker facts |

An optional drawer can show the immutable execution history and detailed
provenance without widening the main table.

## Accounting contract

All money fields are account-currency totals, not per-unit prices, unless the
field name ends in `_per_unit`.

### Signs

- `cash_in >= 0`: sale proceeds, option premium received, dividends if later
  included.
- `cash_out <= 0`: share purchases, long-option premiums, buy-to-close costs,
  and fees.
- `net_cash_flow = cash_in + cash_out`.
- `open_market_value` is signed: long equity/options are positive and short
  equity/options are negative.
- An open lifecycle has `total_pnl = net_cash_flow + open_market_value`.
- A flat lifecycle has `open_market_value = 0` and
  `realized_pnl = total_pnl = net_cash_flow`.

The API, not Angular, owns these formulas.

### Equity

For a current equity position backed only by broker position facts:

- `cash_out = -broker_cost_basis`
- `open_market_value = signed_quantity * mark_per_share`
- `total_pnl = open_market_value + cash_out`
- `cash_flow_basis = POSITION_COST_BASIS`

A closed equity lifecycle requires imported equity executions. It must not be
reconstructed from a positions snapshot. Until those executions exist for a
provider, closed equity is absent and the response advertises that limitation.

### Options

- Sell to open is cash in; buy to close is cash out.
- Buy to open is cash out; sell to close is cash in.
- Option market value uses the **option mark**, contract multiplier, and signed
  contract quantity. Underlying spot is context for strike distance and risk;
  it is not the option's market value.
- Expiration is a zero-cash lifecycle event unless the broker reports a
  separate settlement cash flow.
- Assignment/exercise remains an explicit event and may create equity activity;
  it is never silently treated as an ordinary option close.
- Fees are included exactly once through broker net cash flow.

### Legs versus events

`open_leg_count` counts distinct current positions in a strategy or group.
`event_count` counts immutable executions/lifecycle events. A share purchase is
not a two-leg position merely because an eventual sale would be its second
event.

### Option Adjusted Basis / Share

The main row adjusts the current equity cost only for option economics:

```text
option_adjusted_basis_per_share =
  (current_equity_cost_basis - option_pnl) / current_shares
```

`option_pnl` includes realized option results and the signed market value of
open option positions, so this is a live economic estimate rather than broker
or tax cost basis. It changes with open option marks, but it does not include
the equity's own marked gain or loss. It is zero for an options-only symbol,
and unavailable rather than partially calculated when a positive share
quantity has missing cost, option history, or reconciliation inputs.

## Normalized API

Add, without changing existing endpoints:

```text
GET /brokerage-ledgers/{portfolio}/combined
portfolio = trading | retirement
```

The response is `smallfish.brokerage-ledger` version 1:

```json
{
  "schema_name": "smallfish.brokerage-ledger",
  "schema_version": 1,
  "portfolio": {
    "id": "TRADING",
    "label": "Trading",
    "brokerage": "TASTYTRADE"
  },
  "as_of": {
    "positions": "2026-07-28T16:00:00Z",
    "activity": "2026-07-28T16:00:00Z",
    "market": "2026-07-28"
  },
  "coverage": {
    "open_equity": "COMPLETE",
    "closed_equity": "UNAVAILABLE",
    "options": "COMPLETE",
    "history_start": "2026-01-01",
    "reasons": ["Closed equity activity is not imported for this portfolio."]
  },
  "summary": {
    "symbol_count": 1,
    "incomplete_symbol_count": 0,
    "equity_market_value": 12000.0,
    "option_market_value": -75.0,
    "total_market_value": 11925.0,
    "total_pnl": 1425.0
  },
  "symbols": [
    {
      "symbol": "EXAMPLE",
      "exposure": "EQUITY_AND_OPTIONS",
      "state": "OPEN",
      "accounts": ["ACCOUNT LABEL"],
      "shares": 100.0,
      "current_price_per_share": 120.0,
      "share_quantity": 100.0,
      "equity_cost_per_share": 111.0,
      "equity_cost": 11100.0,
      "current_equity": 12000.0,
      "equity_pnl": 900.0,
      "equity_pnl_per_share": 9.0,
      "net_credit": 600.0,
      "net_debit": 0.0,
      "option_pnl": 525.0,
      "net_pnl": 1425.0,
      "option_adjusted_basis_per_share": 105.75,
      "cash_in": 600.0,
      "cash_out": -11100.0,
      "net_cash_flow": -10500.0,
      "equity_market_value": 12000.0,
      "option_market_value": -75.0,
      "open_market_value": 11925.0,
      "total_pnl": 1425.0,
      "pnl_completeness": "INDICATIVE",
      "adjusted_basis": {
        "realized_per_share": 109.0,
        "marked_per_share": 105.75,
        "history_start": "2026-01-01",
        "completeness": "INDICATIVE",
        "reason": null
      },
      "components": [],
      "annotations": []
    }
  ],
  "warnings": []
}
```

Every component carries stable identity, `instrument`, `side`, `option_type`,
`state`, quantities, contract terms, cash fields, signed market value, P/L,
`cash_flow_basis`, `open_leg_count`, `event_count`, marks with timestamps,
annotations, and provenance. Display labels such as `Short Call` are derived
from structured fields; they are not serialized as the accounting contract.

## Source adapters

The shared service has one adapter per existing artifact family:

- **Trading:** current Tastytrade positions, immutable options/equity activity,
  group metadata, reconciliation state, and option marks.
- **Retirement:** SnapTrade holdings, enrichment metadata, option-event ledger,
  group metadata, and option marks/risk inputs.

The FastAPI layer reads materialized artifacts only. It does not call providers
and does not import `utilities/` or `studies/`.

Tastytrade sync must materialize all current equity and option positions for
the combined adapter. The existing options-only artifacts and endpoints remain
compatible. Retirement already materializes all current positions.

## Delivery phases

### Phase 1 - Contract and adapters

- Add typed backend normalization helpers and the versioned endpoint.
- Materialize all Tastytrade positions during its existing manual sync without
  changing the existing options endpoint.
- Build symbol/component rows for both portfolios.
- Fail closed for missing history, marks, or reconciliation.
- Add backend tests using fake provider data only.

### Phase 2 - Shared option-adjusted UI

- Add one shared Angular model, service method, and component.
- Mount it as `Option-Adjusted Basis` on both brokerage pages.
- Include only Equity + Options symbols and omit the redundant Exposure column.
- Use the shared table, panel, badge, drawer/modal, and formatting patterns.
- Show loading, empty, error, stale, incomplete, and narrow-width states.
- Preserve provider-specific sync actions and all existing views.

### Phase 3 - Equity lifecycle completeness

- Normalize immutable equity executions from both providers.
- Add closed equity lifecycle rows and assignment/exercise links.
- Confirm real Fidelity expiration and assignment shapes before claiming them
  complete; the generic path remains visibly unconfirmed until then.

### Phase 4 - Asset-view separation and cleanup

- Compare the normalized views against both existing ledgers with representative
  data.
- Expose Holdings, Options, and Option-Adjusted Basis in the same order on both
  brokerage pages.
- Move shared holdings/options tables and calculations behind the normalized
  contract incrementally.
- Remove duplicate UI or old endpoints only in separate, compatibility-checked
  changes.

## Acceptance criteria for the first release

- The same Angular component and TypeScript interfaces render both portfolios.
- The UI makes no provider-specific field mapping or accounting calculation.
- A symbol with only equity, only options, or both has correct summary totals.
- Long and short option examples prove signed market value and P/L formulas.
- BTO/BTC and STO/BTC tests prove cash signs, multipliers, and fees.
- Missing marks or unreconciled positions make the affected symbol and
  portfolio total incomplete; no partial total masquerades as complete.
- Notes and provenance are visible without modifying imported broker facts.
- Account identity remains visible and short-call coverage never crosses an
  account boundary.
- Existing `/options`, `/options/activity`, `/retirement/portfolio/live`, and
  `/retirement/options` response shapes do not change.
- Backend tests, Angular build/tests, route inspection with representative
  data, docs checks, secret scan, and `git diff --check` pass.
