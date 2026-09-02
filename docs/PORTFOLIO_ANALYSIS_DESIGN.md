# Portfolio Analysis tab — design

**Status:** Implemented; synthetic visual route capture pending
**Date:** 2026-08-29
**Scope:** A shared fourth tab on the existing Trading and Retirement brokerage
pages. The tab assesses the current ledger against account-specific risk limits
and previews a proposed stock or ETF purchase or sale without changing broker
facts or placing an order.

This document settles the product and technical design before implementation.
It does not authorize a trade, define a universally suitable allocation, or
turn smallFish into an adviser. The implementation remains local, read-only at
the broker, and evidence-first.

---

## 1. Product objective

Portfolio Analysis answers four separate questions for the selected brokerage:

1. **Profile fit** — does the account's current exposure fit the limits the
   owner selected for that account role?
2. **Construction** — is the exposure deliberately distributed, or is it
   dominated by an issuer, sector, speculative bucket, or option obligation?
3. **Capital deployment** — is the account above or below its selected
   deployment or allocation range?
4. **Proposed change** — how would a contemplated purchase or sale alter those
   conclusions?

The product must not collapse those answers into one opaque score. A Retirement
account may have an appropriate growth allocation and still be poorly
constructed; a Trading account may be intentionally concentrated and still
breach its maximum loss or assignment budget.

### Non-goals

- Predict returns or identify a security expected to outperform.
- Recommend an exact replacement security.
- Place, stage, transmit, or schedule an order.
- Choose tax lots or estimate tax consequences.
- Infer a complete household financial plan from the connected brokerages.
- Treat the named equal-weighted lists under `/portfolios` as owned positions.
- Recreate the retired options-risk dashboard. Portfolio Analysis is a new
  brokerage projection with explicit profile inputs and current consumers.

## 2. Settled product decisions

| Decision | Choice |
| --- | --- |
| Location | Fourth shared tab: **Holdings**, **Options**, **Combined Adjusted Basis**, **Portfolio Analysis**. No new route or global navigation item. |
| Scope | One requested brokerage at a time. Trading and Retirement are not mixed in version 1. |
| Implementation shape | One shared Angular component and one provider-neutral backend projection, keyed by public `brokerage_id`. |
| Account roles | `TRADING` uses a speculative trading risk budget. `RETIREMENT` uses a long-term aggressive-growth allocation and risk-capacity profile. |
| Verdicts | Separate profile-fit, construction, capital-deployment, and data-confidence results. No 0–100 score. |
| Limits | No hidden universal thresholds. The owner reviews and saves each numeric limit. Missing limits produce `NOT_ASSESSED`, not a guessed answer. |
| Recommendations | Show the amount above or below the owner's selected limit and the math that would restore it. Do not claim that a named security should be bought or will perform better. |
| Preview | Instant, read-only recalculation. A preview never writes a holding, changes profile data, or calls an order API. |
| Price meaning | Every value names its source and observation time. “Instant preview” does not mean “live quote.” |
| Missing data | Partial calculations remain explicitly partial. Missing account capital, marks, classifications, or history never become zero. |

## 3. Account-role semantics

The registry's existing `portfolio_role` selects the vocabulary and applicable
rules. The Angular component must not branch on `fidelity` or `tastytrade`.

### Trading — speculative risk budget

Trading is allowed to be highly aggressive. Its main question is not whether it
resembles a conventional diversified investment portfolio; it is whether the
account remains inside its selected survival and capital-commitment limits.

Applicable rules include:

- gross long/short exposure when the required capital facts are available;
- largest underlying exposure;
- optional sector and correlated-cluster concentration;
- short-put assignment commitment;
- covered versus uncovered short calls;
- long-option premium at risk when defensibly known;
- free-capital or cash buffer;
- stress loss versus the selected loss budget; and
- unknown, unbounded, stale, or incomplete risk inputs.

Trading has no underinvestment warning by default. Cash can be deliberate
optionality. The owner may opt into a `deployment_min_pct`; only then may the
analysis label Trading as below its selected deployment range.

### Retirement — long-term aggressive growth

Retirement permits a high allocation to risk assets while applying long-term
portfolio-construction controls.

Applicable rules include:

- total growth allocation versus the selected range;
- cash and defensive allocation versus selected ranges;
- speculative allocation;
- single-issuer, top-five, sector, and industry concentration;
- short-put and other option commitments;
- current-holdings historical volatility and drawdown;
- transparent equity-shock scenarios versus loss capacity; and
- time to first expected withdrawal as profile context.

“Underinvested” means below the owner's selected growth or deployment range. It
does not mean underfunded for a retirement goal. Goal-funding projections are a
separate future feature and must not imply that taking more risk repairs a
savings gap.

## 4. Profile and limits

Profiles are app-owned metadata stored separately from immutable broker facts.
The registry supplies the role; it is not editable through the profile API.

### Profile status

- `UNCONFIGURED` — no saved profile. Metrics that need no limit may render,
  but fit verdicts are `NOT_ASSESSED`.
- `PARTIAL` — at least one limit is saved. Each configured rule runs; missing
  rules remain `NOT_ASSESSED`. The overall fit cannot be `ALIGNED`.
- `COMPLETE` — every required field for the brokerage role is saved and valid.

This progressive model lets the owner learn and configure one limit at a time
without receiving a false complete verdict.

### Common profile fields

| Field | Required | Meaning |
| --- | --- | --- |
| `objective` | Yes | Role-owned value: `SPECULATIVE_TRADING` or `LONG_TERM_AGGRESSIVE_GROWTH`. |
| `max_single_issuer_pct` | Yes | Maximum current long value in one normalized underlying as a percentage of analyzed capital. |
| `max_speculative_pct` | Yes | Maximum allocation to the owner-classified `SPECULATIVE` bucket. |
| `max_put_assignment_commitment_pct` | Yes | Maximum cash commitment from unspread short puts as a percentage of analyzed capital. |
| `max_stress_loss_pct` | Yes | Maximum loss under the configured severe uniform-equity-shock scenario. |
| `minimum_liquid_pct` | Yes | Minimum provider-supported cash or cash-equivalent percentage. |
| `notes` | No | Plain-language rationale for the selected limits. |
| `reviewed_at` | Server | Timestamp of the latest saved review. |

### Trading fields

| Field | Required | Meaning |
| --- | --- | --- |
| `max_gross_exposure_pct` | Yes | Maximum gross marked exposure divided by analyzed capital. |
| `deployment_min_pct` | No | Enables a below-deployment warning when set. |
| `deployment_max_pct` | No | Optional upper bound for deployed non-cash capital. |
| `max_sector_pct` | No | Optional because concentration can be an intentional trading choice. |

### Retirement fields

| Field | Required | Meaning |
| --- | --- | --- |
| `growth_min_pct` | Yes | Lower edge of acceptable `GROWTH + SPECULATIVE` allocation. |
| `growth_max_pct` | Yes | Upper edge of acceptable `GROWTH + SPECULATIVE` allocation. |
| `cash_min_pct` / `cash_max_pct` | Yes | Selected liquid allocation range. Must be consistent with the other allocation ranges. |
| `max_sector_pct` | Yes | Maximum known sector exposure. |
| `max_top_five_pct` | Yes | Maximum combined current value of the five largest issuers. |
| `first_expected_withdrawal_date` | Yes | Time-horizon context. It does not silently alter numeric limits. |

Percentage fields are finite decimal numbers. Ordinary allocation and
concentration limits are in `[0, 100]`; gross-exposure limits may exceed 100.
Cross-field validation rejects inverted ranges and impossible allocation
combinations. The API never supplies default percentages that the owner did not
review.

## 5. Required data and current gaps

### Existing inputs to reuse

- The brokerage registry and adapters provide provider-neutral positions,
  activity, account identity, marks, and market observations.
- Holdings metadata supplies existing category and industry annotations while
  remaining separate from broker facts.
- The Symbol Ledger already derives account-aware call coverage, open strategy,
  short-put cash commitment, and missing-data reasons.
- `stock-app/app/data_reader.py` reads the adjusted local daily-price cache.
- `data/universe.csv` supplies normalized symbol and known sector context.

The backend must reuse those sources. `stock-app/` must not import
`utilities/` or `studies/`.

### Account-capital prerequisite

Current Trading position artifacts do not prove total account capital, idle
cash, buying power, or margin requirements. Summing visible positions is not a
valid substitute: it can make a leveraged account look fully funded or call
idle cash “missing.” Retirement positions contain cash-equivalent holdings, but
the same explicit capital contract keeps both brokerages comparable.

Before fit percentages are implemented, sync must materialize a
provider-neutral account-capital artifact and the canonical brokerage snapshot
must expose it. One row per provider account contains nullable facts:

```text
brokerage_id, account_id, account, currency,
net_liquidating_value, cash_balance, buying_power,
maintenance_requirement, source, retrieved_at
```

Each missing value carries a stable reason. Raw provider retrieval belongs in
`services/`; normalization, policy, and atomic artifact writes remain in
`stock-app/`. Tests use injected fakes and committed synthetic fixtures only.

`net_liquidating_value` is the preferred analysis denominator. If it is absent,
the response may show absolute position facts but profile fit, allocation
weights, trim math, and capital-deployment verdicts are `UNAVAILABLE`. The
implementation must not silently substitute cost basis, contributions, or the
sum of visible positions.

### Allocation classification

Each non-option holding receives one mutually exclusive bucket:

- `GROWTH` — ordinary long equity exposure;
- `SPECULATIVE` — owner-classified higher-risk growth exposure;
- `DEFENSIVE` — owner-classified fixed-income or defensive exposure;
- `CASH` — provider cash or cash-equivalent facts; or
- `UNKNOWN` — anything not defensibly classified.

Provider `CASH` maps automatically to `CASH`; ordinary long `EQUITY` maps to
`GROWTH`; provider `OTHER`, short equity, and ambiguous plan funds fail to
`UNKNOWN`. The owner may save an account-scoped override in a separate analysis
metadata artifact. A classification never rewrites the provider instrument or
the existing cost-basis metadata.

Version 1 treats an ETF as one issuer. It does not claim look-through
diversification. ETF holdings overlap, geography, market-cap, factor exposure,
and expense ratios are deferred until a batch-owned reference artifact exists.

## 6. Calculation semantics

Every response includes positions, capital, market, and cached-price as-of
timestamps. Percentages use the exact unrounded values; rounding is display
only.

### Capital and allocation

Let `N` be the sum of included accounts' known net liquidating values.

- `bucket_pct = 100 * bucket_long_market_value / N`
- `growth_pct = GROWTH_pct + SPECULATIVE_pct`
- `current_issuer_pct = 100 * long_equity_market_value / N`
- `deployment_pct = 100 - CASH_pct`, only when cash coverage is complete
- `gross_marked_exposure_pct = 100 * sum(abs(marked non-cash position values)) / N`

Options are not represented by their small market value in allocation charts;
that would hide their underlying obligation. They appear in the separate
option-commitment analysis. Short equity and liabilities likewise appear in
gross/net exposure rather than being allowed to cancel a long allocation.

The response includes `reconciliation_gap = N - sum(marked position values)`.
A material gap remains visible and lowers coverage; it is not labeled cash.

### Concentration

Same normalized underlyings combine across accounts within the requested
brokerage for concentration, but coverage and option collateral never cross an
account boundary.

- `top_five_pct` is the sum of the five largest current long issuer values
  divided by `N`.
- sector and industry weights use known classifications only and report
  `classified_pct` alongside the result.
- `effective_position_count = 1 / sum(w_i^2)` is diagnostic only; it never
  overrides an explicit issuer or sector breach.
- short-put assignment commitment is also grouped by underlying so a current
  holding and an obligation to acquire more of the same issuer are adjacent.

### Historical risk

Historical risk is a **current-holdings replay**, not the account's realized
history. It uses current long-equity weights held constant across each session,
cash returns of zero, and adjusted cached closes. The UI must use that exact
label.

For at least 252 aligned sessions:

- daily portfolio return is `sum(current_weight_i * daily_return_i)`;
- annualized volatility is sample standard deviation times `sqrt(252)`;
- beta is covariance with SPY divided by SPY variance over identical sessions;
- correlation uses the same aligned daily returns; and
- maximum drawdown is the minimum of cumulative value divided by its prior
  running maximum minus one.

The result also reports the date range, aligned-session count, analyzed market
value, excluded symbols, and excluded percentage. With fewer than 252 sessions,
volatility and beta are unavailable. Missing symbols may produce an indicative
partial replay, but the response may not call it complete.

No expected return, Sharpe ratio, value-at-risk probability, or forecast is in
version 1. They would add assumption-heavy precision without improving the
account-limit decisions this tab is meant to support.

### Transparent stress scenarios

Version 1 shows uniform, hypothetical shocks rather than pretending to predict
a crisis:

- a `-20%` shock to every marked long equity;
- a `-35%` shock to every marked long equity; and
- an owner-entered custom uniform equity shock.

Cash is unchanged. Defensive and unknown holdings are excluded unless the user
supplies a classification-specific shock in a later version. The response shows
the excluded value and labels each result `HYPOTHETICAL`, not probable.

The severe scenario is compared with `max_stress_loss_pct` only when coverage
is sufficient. Open options lower coverage until their payoff is incorporated;
uncovered calls create a critical finding independently.

### Option commitments

Portfolio Analysis reuses account-aware Symbol Ledger facts:

- unspread short-put commitment is
  `abs(short contracts) * strike * multiplier`;
- put credit and debit spreads contribute zero to **cash-secured-put
  commitment**, matching the existing contract, but must not be described as
  zero risk;
- covered-call coverage is assessed within the account holding the call;
- an uncovered short call always creates a `CRITICAL` finding because its loss
  is not bounded by the displayed premium;
- long-option premium at risk renders only when opening cash flow is complete;
  and
- missing terms, marks, or lifecycle evidence remain named warnings.

Exact multi-leg payoff, spread maximum loss, delta-adjusted exposure, gamma,
and option-aware historical replay are deferred. Until then, open options make
the historical stress result `INDICATIVE`; their known commitments still render
separately.

## 7. Verdict model

Each rule emits a structured finding. The top-level verdict summarizes those
findings but never replaces them.

### Finding shape

```json
{
  "code": "SINGLE_ISSUER_LIMIT",
  "severity": "HIGH",
  "direction": "OVER",
  "scope": "ISSUER",
  "symbol": "SYNTH",
  "title": "SYNTH exceeds the selected issuer limit",
  "actual": 18.4,
  "limit": 12.0,
  "unit": "PERCENT_OF_CAPITAL",
  "excess_amount": 6400.0,
  "explanation": "Current long value is above the profile limit.",
  "remediation": {}
}
```

`symbol` remains the broker identity. When a holdings display name is saved, the
title uses that name instead (`Example Target Date Fund exceeds the selected
issuer limit`); the Analyzed holdings table does the same. What-if and
classification writes still use the broker symbol.

Stable initial finding codes are:

- `PROFILE_NOT_CONFIGURED`, `PROFILE_PARTIAL`;
- `ACCOUNT_CAPITAL_UNAVAILABLE`, `PRICE_STALE`, `HISTORY_INCOMPLETE`,
  `CLASSIFICATION_UNKNOWN`, `RECONCILIATION_GAP`;
- `GROWTH_BELOW_TARGET`, `GROWTH_ABOVE_TARGET`, `DEPLOYMENT_BELOW_TARGET`,
  `DEPLOYMENT_ABOVE_TARGET`, `CASH_BELOW_MINIMUM`, `CASH_ABOVE_MAXIMUM`;
- `SINGLE_ISSUER_LIMIT`, `TOP_FIVE_LIMIT`, `SECTOR_LIMIT`,
  `SPECULATIVE_LIMIT`, `GROSS_EXPOSURE_LIMIT`;
- `PUT_COMMITMENT_LIMIT`, `UNCOVERED_SHORT_CALL`,
  `OPTION_RISK_INCOMPLETE`; and
- `STRESS_LOSS_LIMIT`.

Severities are `INFO`, `CAUTION`, `HIGH`, and `CRITICAL`. Directions are
`UNDER`, `OVER`, and `NEUTRAL`.

### Summary verdicts

`profile_fit` uses this priority:

1. `CRITICAL_RISK` — a critical risk finding exists;
2. `ABOVE_PROFILE` — one or more configured risk limits are exceeded;
3. `MIXED` — both below-target deployment/allocation and above-limit risk
   findings exist;
4. `BELOW_PROFILE` — only below-target findings exist;
5. `ALIGNED` — the profile is complete, coverage is complete, and no configured
   rule is breached;
6. `NEEDS_REVIEW` — profile or material data is incomplete; or
7. `NOT_ASSESSED` — no applicable limits have been saved.

`construction` is independently `WELL_CONSTRUCTED`, `CONCENTRATED`, `FRAGILE`,
or `NEEDS_REVIEW`. `capital_deployment` is `BELOW_RANGE`, `IN_RANGE`,
`ABOVE_RANGE`, `MIXED`, or `NOT_ASSESSED`. `data_confidence` is `COMPLETE`,
`INDICATIVE`, or `UNAVAILABLE` and lists its reasons.

Only `COMPLETE` data may support `ALIGNED` or `WELL_CONSTRUCTED`. Negative
findings supported by known evidence still render when other data is missing;
missing data cannot erase a known breach.

## 8. Trim and reallocation math

Recommendations explain how to return to a selected limit. They are not order
instructions.

For current issuer value `V`, analyzed capital `N`, and selected fraction `T`:

- immediate overage is `max(V - T*N, 0)`;
- approximate shares above the limit are `overage / displayed_price`; and
- new outside capital required to dilute without selling is
  `max(V/T - N, 0)`.

The UI labels shares approximate and shows the price source and timestamp. A
sale assumes proceeds remain as account cash, so `N` is unchanged before taxes,
fees, or slippage. The app does not choose a tax lot.

For a sector breach, the response gives total sector overage and lists the
contributing holdings largest-first. It does not arbitrarily choose which one
to sell. When an allocation bucket is below range, remediation names the
underweight bucket and dollar shortfall, not a security to purchase.

Multiple breaches can interact. Every remediation includes “Preview this
change,” which runs the proposed action through the same full rule engine before
the user treats one restored limit as a complete solution.

## 9. Proposed-investment preview

Version 1 previews long stocks and ETFs already in the universe or already
present in the brokerage ledger. It supports buys and sales that do not create
a short position. Options, short stock, margin borrowing, and multi-leg strategy
previews are deferred until their complete payoff can be represented.

Request fields:

```json
{
  "account_id": "synthetic-account",
  "side": "BUY",
  "symbol": "SYNTH",
  "quantity": 10,
  "notional": null,
  "assumed_price": 125.50,
  "funding_source": "ACCOUNT_CASH",
  "allocation_bucket": null
}
```

Exactly one of `quantity` or `notional` is required. `funding_source` is
`ACCOUNT_CASH` or `NEW_CONTRIBUTION` for a buy; sale proceeds remain account
cash. A new contribution increases `N`; an account-cash purchase does not.
Fees, taxes, slippage, and execution probability are excluded and named.

Price priority is:

1. positive `assumed_price` supplied by the user;
2. a sufficiently timestamped saved provider mark for that symbol; then
3. the latest cached adjusted close.

No network fetch occurs in the request. If no defensible price exists, preview
fails with a caller-fixable error. The response always returns the assumed price,
source, and as-of time.

The response contains compact `before` and `after` summaries, metric deltas,
new findings, worsened findings, improved findings, and resolved findings. It
also returns `persisted: false`. Calling preview never changes a CSV, profile,
metadata file, ledger, or brokerage account.

## 10. API contracts

All routes live under the existing brokerage namespace:

| Method and path | Purpose |
| --- | --- |
| `GET /api/brokerages/{brokerage_id}/portfolio-analysis` | Current analysis from saved artifacts and profile. |
| `GET /api/brokerages/{brokerage_id}/portfolio-analysis/profile` | Saved role-owned profile and completeness. |
| `PATCH /api/brokerages/{brokerage_id}/portfolio-analysis/profile` | Validate and atomically update app-owned limits. |
| `PATCH /api/brokerages/{brokerage_id}/portfolio-analysis/classifications/{symbol}` | Save or clear an account-scoped allocation-bucket override. |
| `POST /api/brokerages/{brokerage_id}/portfolio-analysis/preview` | Non-persistent proposed stock/ETF change. |

The analysis uses the common brokerage envelope:

```json
{
  "schema_name": "smallfish.portfolio-analysis",
  "schema_version": 1,
  "brokerage": {},
  "availability": {},
  "as_of": {},
  "coverage": {},
  "summary": {
    "profile": {},
    "verdicts": {},
    "capital": {},
    "allocation": {},
    "concentration": {},
    "historical_risk": {},
    "stress": {},
    "option_commitments": {},
    "findings": []
  },
  "items": [],
  "warnings": []
}
```

`items` contains one analyzed current issuer/account row with its market value,
weight, bucket, classification source, sector, historical coverage, and related
option commitment. `warnings` contains data-quality and provenance problems;
profile breaches belong in `summary.findings`.

The router only parses and serializes. Brokerage identity resolution, profile
loading, and analysis live in the service/projection layers. Public errors use
stable codes and never include provider exception text.

## 11. Storage and ownership

Proposed runtime artifacts, all under `SFP_DATA_DIR` with config overrides:

- `portfolio_analysis/profiles.json` — app-owned profile limits keyed by public
  brokerage id;
- `portfolio_analysis/classifications.csv` — app-owned account/symbol bucket
  overrides; and
- each ledger namespace's `account_capital.csv` — immutable materialized
  provider facts from the latest sync.

Writes use the repository's atomic replace pattern and an in-process lock.
Profiles and classifications may be edited; capital facts may only be replaced
by brokerage sync. No credential, account number, real position, or profile
amount appears in a committed fixture, screenshot, log, or error.

Deleting or clearing a profile does not delete a position, event, archive,
holding annotation, or captured G/L snapshot.

## 12. UI design

### Shared shell

Extend `BrokerageLedgerPageComponent`'s tab vocabulary with `analysis` and add
the fourth button after Combined Adjusted Basis. The same
`PortfolioAnalysisComponent` receives `brokerageId` and `refreshToken`. Sync and
Refresh use the existing page actions; selecting the tab does not sync a
provider.

The page subtitle changes during implementation to include portfolio analysis.
The current three tabs retain their order and behavior.

### Tab layout

1. **Verdict header** — brokerage label, role-specific objective, profile
   status, positions/capital/prices as-of chips, and `Edit profile`.
2. **Verdict strip** — Profile fit, Construction, Capital deployment, and Data
   confidence. Each card includes text as well as semantic color.
3. **Priority findings** — highest severity first. Each finding states actual,
   selected limit, dollar excess/shortfall, evidence date, and applicable
   remediation paths.
4. **Allocation and concentration** — compact accessible bars plus exact
   percentages; missing classification is visible.
5. **Risk evidence** — current-holdings replay period, volatility, beta,
   drawdown, uniform shocks, exclusions, and benchmark context.
6. **Option commitments** — put commitment, call coverage, known premium at
   risk, and incomplete/unbounded warnings.
7. **What-if** — compact form followed by side-by-side Before, After, and
   Change results. `Clear preview` returns to actual saved holdings.

Profile editing uses the shared modal. Every limit label has a keyboard-focusable
info bubble with its calculation scope, denominator, and important exclusions.
Classification editing uses a focused modal from an analyzed holding row.
Longer formula explanations use the shared drawer or a concise “How this works”
section; they do not crowd the main findings.

### States and accessibility

- Unconfigured profile is a valid empty configuration with a clear next action.
- Loading uses skeleton panels; errors and stale/partial data use semantic
  banners adjacent to the affected result.
- Missing values render `—`, never zero.
- Finding severity always has a label or icon in addition to color.
- Numeric tables use tabular numerals and sortable, keyboard-reachable headers.
- At narrow widths, verdict cards and preview comparison stack; issuer identity
  remains visible during horizontal table scrolling.
- Preview submission exposes `aria-busy`, prevents duplicates, and announces
  success or failure without moving focus unexpectedly.

## 13. Backend boundaries

Suggested modules:

```text
stock-app/app/brokerages/
  account_capital.py                 capital artifact read/write contract
  portfolio_analysis_profile.py      profile/classification validation + store
  projections/portfolio_analysis.py  provider-neutral metrics and findings
  projections/portfolio_preview.py   temporary proposal application
```

Adapters expose canonical facts; projections never read provider-specific
columns or branch on brokerage identity. Common formulas use `Decimal` for
money and retain unrounded values until serialization. Historical-return math
may use the backend runtime's existing pandas/numpy dependencies through
`data_reader`, but it must not be copied from or imported out of a study runner.

The response model is additive. Existing holdings, Symbol Ledger, Combined
Adjusted Basis, compatibility routes, and generated artifacts keep their
shapes.

## 14. Implementation phases

### Phase 1 — characterize and materialize capital

- Characterize saved synthetic provider payloads for account value, cash,
  buying power, and maintenance requirement.
- Add canonical nullable capital facts and per-ledger artifacts.
- Extend sync reporting and adapters without weakening existing availability.
- Prove that missing capital fails closed.

### Phase 2 — profile, classification, and current analysis backend

- Add atomic app-owned profile and classification stores.
- Implement allocation, concentration, remediation, historical replay,
  transparent stress, option commitments, findings, and verdict aggregation.
- Add the read/profile/classification routes.

### Phase 3 — shared Portfolio Analysis tab

- Add the fourth tab and shared Angular API/types/component.
- Implement configured, partial, unconfigured, unavailable, stale, and narrow
  viewport states.
- Load both `/options` and `/retirement` with representative synthetic data.

### Phase 4 — stock/ETF preview

- Add non-persistent buy/sell preview and before/after UI.
- Cover account-cash versus new-contribution funding.
- Confirm through isolated tests that preview writes no artifact.

### Deferred extensions

- exact multi-leg option payoff and option-aware preview;
- delta/gamma and option-aware stress replay;
- ETF look-through, fund overlap, geography, market-cap, factor, and fee data;
- combined cross-brokerage/household concentration; and
- goal-funding and contribution-path analysis.

Each deferred input needs a provider-neutral or batch-materialized artifact.
The FastAPI runtime must not fetch an external reference dataset on page load.

## 15. Verification

### Backend tests

- Role-specific profile validation and partial-profile behavior.
- Capital denominator and reconciliation-gap behavior.
- Missing capital, price, sector, classification, or history fails closed.
- Exact allocation, issuer, top-five, sector, speculative, deployment, and
  commitment calculations with synthetic values.
- Trim and dilution formulas, including zero and boundary cases.
- Same symbol combines for concentration across accounts within one brokerage;
  call coverage never crosses accounts.
- Spread put commitment remains zero without being labeled zero risk.
- Uncovered call produces a critical finding.
- Current-holdings replay, aligned-session floor, volatility, beta, and
  drawdown against deterministic price fixtures.
- Verdict priority, including mixed over/under findings and incomplete data.
- Preview cash versus contribution arithmetic and strict no-write behavior.
- Provider exception details remain server-only.

### Angular tests

- Fourth tab appears in the shared shell for both roles and preserves existing
  tab behavior.
- Role wording and applicable limits come from `portfolio_role`, not brokerage
  identity.
- Findings display actual, limit, direction, excess/shortfall, and data date.
- `—`, incomplete banners, stale timestamps, and profile states render
  correctly.
- Profile and classification modals retain keyboard and focus behavior.
- What-if before/after changes and no-write explanation render correctly.
- Responsive tables preserve issuer context.

### Required commands during implementation

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
cd stock-app-ui && npm run build && npm run test:ci
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

The affected routes must also be loaded in a browser with synthetic Trading and
Retirement data. A browser check must not trigger a live brokerage sync or write
a real profile merely to prove that a control exists.

## 16. Documentation changes when implemented

Behavior documentation continues to describe the current three-tab product
until the feature exists. In the implementation change, update together:

- `README.md` and screenshots;
- `stock-app/README.md` endpoint and artifact tables;
- `stock-app-ui/README.md` routes, tabs, and API model notes;
- `stock-app-ui/docs/UX_GUIDANCE.md` from three shared tabs to four;
- `docs/ARCHITECTURE.md`, `docs/BROKERAGES.md`, `docs/CONFIGURATION.md`, and
  `docs/DATA.md`; and
- `docs/screenshots/README.md` with synthetic Portfolio Analysis captures.

## 17. Acceptance criteria

The design is implemented only when all of the following are true:

1. Trading and Retirement each expose the same fourth Portfolio Analysis tab.
2. Each tab evaluates only the requested brokerage and applies its registry
   role without provider branches in UI or formulas.
3. The app never calls Trading underinvested unless a deployment minimum is
   configured.
4. Retirement separately reports allocation fit and construction quality.
5. Known breaches remain visible when other inputs are incomplete, while no
   incomplete result is called aligned.
6. Every trim or dilution amount is traceable to a saved owner limit, current
   capital, and timestamped price.
7. Preview reports before/after effects and is provably non-persistent.
8. Options market value is never used as a substitute for assignment or
   unbounded exposure.
9. No existing API response, ledger artifact, or brokerage fact is rewritten
   to support the feature.
10. Backend/UI suites, browser checks, documentation checks, secret scan, and
    diff checks pass.

## 18. Method references

The product framing follows public investor-education guidance that allocation
depends on time horizon and risk tolerance, that diversification must be
considered within as well as across asset classes, and that leverage and
uncovered options can create losses disproportionate to displayed premium:

- [Investor.gov — Asset Allocation and Diversification](https://www.investor.gov/introduction-investing/getting-started/asset-allocation)
- [FINRA — Concentrate on Concentration Risk](https://www.finra.org/investors/insights/concentration-risk)
- [Investor.gov — Leveraged Investing Strategies](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/leveraged-investing-strategies-know-risks-using-these-advanced-investment-tools)
- [FINRA — Options](https://www.finra.org/investors/insights/options-z-basics-greeks)

These references justify the categories of evidence, not the owner's numeric
limits. smallFish reports screens, calculations, and selected-limit breaches;
it does not claim suitability certification or provide financial advice.
