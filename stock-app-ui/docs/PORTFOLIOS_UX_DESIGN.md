# Portfolios — UX Design

**Status:** Implemented (2026-07-25)
**Date:** 2026-07-25
**Scope:** New top-level Portfolios view for creating named stock lists and
tracking their equal-weighted returns against SPY.

This design follows `UX_GUIDANCE.md`. Momentum is the reference for the
scan/table language; Options and Retirement are the reference for ledger
editing and the drawer/modal discipline.

---

## 1. Product decisions (settled)

These were confirmed with the product owner before design:

| Decision | Choice |
| --- | --- |
| Return weighting | **Equal-weighted**: portfolio return = mean of each member's % return. The "avg price" columns are shown literally (mean of member prices) as reference values, but no % column is derived from them. |
| Return baselines | **Both**: "Since inception vs SPY" (primary, default sort) and "YTD vs SPY" (secondary). Both sortable. |
| Symbol scope | **Universe only** (`data/universe.csv`, ~1,772 symbols). All prices and history come from the local OHLCV cache (`data/{year}/{SYMBOL}.txt`). No live yfinance calls on this page. |
| Late adds | **Backfill to creation date**: a stock added after creation is treated as held since the portfolio's creation date, using its cached close on that date. |

Consequences worth stating plainly:

- "Current market price" on this page means **the latest cached close**, not a
  live intraday quote. The page must therefore show price provenance (the
  cache as-of date) and a stale warning when the cache lags the last trading
  session. This matches the Momentum scanner, which reads the same cache.
- Backfill-to-creation means every number on the page is **deterministically
  recomputable** from `(creation date, member symbols, price cache)`. Recorded
  creation-time prices are kept for audit display, but they are provenance,
  not an input the math depends on.

## 2. Definitions (computation semantics)

All closes are from the shared OHLCV cache; "latest" = the most recent cached
session.

- **Avg price** — mean of member latest closes.
- **Avg price last week** — mean of member closes 5 trading sessions before
  the latest session. Header label: `Avg price · 1 wk ago`.
- **1-wk %** — equal-weighted mean of each member's 5-session % return.
  (Not the % change of the two average-price columns.)
- **Since-inception return** — equal-weighted mean of member returns from the
  portfolio's creation-date close to the latest close.
- **YTD return** — equal-weighted mean of member returns from the final close
  of the prior calendar year to the latest close.
- **vs SPY** — portfolio return minus SPY's return over the identical window,
  expressed in **percentage points** (e.g. `+3.2 pp`). SPY's own return is
  shown alongside so the comparison is legible, never implied.
- **52-wk low / high** — min/max close over the trailing 365 calendar days.
- **Baseline fallback** — if a member has no close on the baseline date
  (listed later, halted), use its **first available close after** the baseline
  and badge the member `Partial history` in the drawer. If a member has no
  cached data at all, render its values as `—`, exclude it from portfolio
  averages, and badge it — never silently average around missing data at the
  portfolio level without a visible cue (a `warn` dot on the summary row).

## 3. Navigation

Add a third nav group to the shell, after Research and Ledgers:

```
Research: Momentum · Strategy · Wheel   |   Ledgers: Options · Retirement   |   Tracking: Portfolios
```

Portfolios is neither a research scan nor a broker ledger — it is
user-authored tracking — so it gets its own small group rather than diluting
Ledgers. Route: `/portfolios`, title `Portfolios · smallFish`, standard
`routerLinkActive` + `aria-current="page"` treatment.

## 4. Page: Portfolios list

### Header

Standard `.page-header`:

- Eyebrow: `Portfolio tracking`
- Title: `Portfolios`
- Subtitle: `Named stock lists tracked equal-weighted against SPY.`
- `.snapshot-chip`: `Prices as of {cache latest session date}`. When that date
  is older than the last expected trading session, the chip takes the `warn`
  treatment and reads `Prices stale · {date}`.
- `.page-actions`: `New portfolio` (`btn btn-primary`) — the single primary
  action on the page.

### Stat strip

One `.stat-strip` under the header (skip if zero portfolios):

- `Portfolios` — count.
- `Best vs SPY` — best since-inception vs-SPY spread and which portfolio owns
  it (`+8.4 pp · AI Basket`).
- `SPY YTD` — SPY's own YTD return, signed and colored. Anchors every "vs
  SPY" number on the page.

### Table

One `.table-shell` table, one row per portfolio. Row click opens the detail
drawer (whole row is the target, as in Momentum). Columns:

| Column | Notes |
| --- | --- |
| Name | Sticky identity column. `--font-mono` not required (names are prose); bold. Secondary line: created date plus, when set, Sector/Industry as compact muted `.badge` chips (`Created 2026-07-25 · [Technology] [Semiconductors]`) — tags stay scannable without adding columns. When a description exists, a `matTooltip` on the name shows it — the full text lives in the drawer, not the table. |
| Stocks | Count, right-aligned. |
| Avg price | Right-aligned, `$` whole dollars, tabular numerals. |
| Avg price · 1 wk ago | Same formatting. |
| 1 wk % | **Sortable.** Signed, 1 decimal, `--pos`/`--neg` + sign (color never alone). |
| Incep vs SPY | **Sortable, default sort (desc).** Signed `pp` value, colored. Secondary muted line: the portfolio's own since-inception % so the spread has context (`+12.1% · SPY +8.9%` condensed as tooltip or subline — see below). |
| YTD vs SPY | **Sortable.** Same treatment. |

- Sortable headers are real `<button>`s exposing `aria-sort`, matching the
  established pattern.
- The two "vs SPY" cells lead with the spread (`+3.2 pp`); a `matTooltip` on
  the value gives the decomposition (`Portfolio +12.1% vs SPY +8.9%,
  inception 2026-07-25`). Keep the cell itself compact.
- A portfolio containing members with missing data shows a small `warn` badge
  after its name (`1 no data`) so the averaged numbers are never silently
  partial.
- No filters/toolbar in v1 — the expected count is tens of portfolios, not
  hundreds. Sorting covers the ranking need. (Guidance: don't add controls
  that duplicate what sorting already expresses.)

### States

- **Loading:** `.skeleton` rows in the table shell.
- **Empty:** `.empty-state` — "No portfolios yet. Create one to start
  tracking returns against SPY." with a `New portfolio` button.
- **Error:** semantic error `.banner` above the table; table area shows
  nothing else.

## 5. Create portfolio (modal)

`app-modal`, title `New portfolio`, width `min(560px, 94vw)`. Modal (not
drawer) because this is focused editing, per guidance.

Fields:

1. **Name** — required text input. Duplicate names rejected server-side with
   an inline error.
2. **Description** — optional multi-line textarea, placeholder "How was this
   portfolio constructed? (thesis, screen, source)". Free prose; no length
   gimmicks, but cap at ~1,000 chars server-side.
3. **Sector / Industry** — two optional single-line inputs on one compact
   row. Free text, but Sector offers datalist suggestions from the distinct
   `sector` values in `universe.csv` so tags stay consistent with the rest
   of the app; Industry is unconstrained. These describe the portfolio's
   theme — they are user-authored tags, not derived from the members.
4. **Symbols** — a single textarea accepting free-form entry (spaces, commas,
   or newlines). On blur/parse: tokens are upper-cased, de-duplicated, and
   rendered as a chip row under the field. Chips are removable (×, keyboard
   reachable).
   - Each chip validates against the universe: unknown symbols render with
     the `neg` treatment and a summary line — `2 symbols not in universe:
     BADX, FOO` — and block submission. Universe-only is a hard rule, so the
     error must name the offenders, not just disable the button.
   - Chip subtext shows the symbol's latest cached close so the user sees
     immediately what price will be recorded.

Footer: `Cancel` (`btn`) and `Create portfolio` (`btn btn-primary`). Creation
date is today, captured server-side; the modal states this in muted text
("Created today · baseline prices recorded from the latest session") rather
than offering a date picker — backdating is out of scope.

Async states per guidance: submit disables + spinner + `aria-busy`; success
closes the modal and the new row appears in the table (optimistic focus on
it); failure keeps the modal open with an error banner inside it.

## 6. Portfolio detail (drawer)

`app-drawer` (the shared drawer — row inspection), `ariaLabel` =
`"{name} portfolio detail"`, default width. Contents top to bottom:

### Drawer header

- Portfolio name (editable via a small `Edit` ghost button → swaps name,
  description, sector, and industry to inline inputs with Save/Cancel;
  deliberate editing, no always-on form).
- Muted meta line: `Created 2026-07-25 · 8 stocks · prices as of 2026-07-24`,
  followed by the Sector/Industry badges when set (same `.badge` treatment
  as the list table).
- **Description** — the construction rationale, rendered as plain prose under
  the meta line (readable body size, muted, wraps; not a tooltip). Empty →
  a quiet `Add description` ghost link instead of blank space. The
  description is the "why" of the portfolio, so it lives at the top of the
  drawer where returns are judged, per the guidance that caveats sit next to
  the result they qualify.
- `Delete portfolio` as `btn btn-danger btn-sm`, right-aligned. Opens a
  confirm `app-modal` ("Delete 'AI Basket'? This removes the portfolio and
  its member list. Price history is unaffected."). Hard delete — these are
  user-authored lists, not broker facts.

### Summary stats

A compact `.stat-strip`: `Incep vs SPY`, `YTD vs SPY`, `1 wk`, each signed
and colored, with the portfolio's own return as the small sublabel
(`+12.1% vs +8.9%`).

### Members table

`.table-shell` table, one row per member symbol:

| Column | Notes |
| --- | --- |
| Symbol | `--font-mono`, sticky, links to `/stockDetail/{symbol}` (existing route) so deep research stays one click away. |
| Price | Latest close, cents shown (per-share prices, cents matter). |
| 1 wk % | Signed, colored. |
| 52-wk low · high | Two values plus a slim inline range bar with a marker at the current price — the marker gives instant "near high / near low" reading; the printed numbers keep it accessible without color or position. |
| YTD % | Signed, colored. |
| Incep % | Return since portfolio creation (backfilled). Signed, colored. Members with a fallback baseline show the `Partial history` badge here. |
| *(row action)* | `Remove` — small ghost icon button, `aria-label="Remove {symbol}"`. |

- Removing a stock asks for no modal — it is reversible by re-adding — but
  shows an inline undo affordance in a success banner for one action
  (`Removed NVDA · Undo`), keeping editing deliberate without heavy friction.
- Rows with no cached data render `—` across values with a `warn` badge and
  a tooltip explaining exclusion from portfolio averages.

### Add symbols

Below the table, a single compact row: text input (`Add symbols…`, accepts
the same comma/space-separated parsing as the create modal) + `Add` button.
Same universe validation with named offenders. Added members appear with the
backfill rule applied; if a freshly added symbol's creation-date baseline
required fallback, the `Partial history` badge appears immediately —
the user sees the consequence of backfilling at the moment they add.

### Drawer states

Loading uses `.skeleton` inside the drawer; member add/remove/rename show
per-action spinners and error banners local to the drawer, never a full-page
error.

## 7. Formatting rules (applied throughout)

- All returns: explicit `+`/`−` sign, one decimal, `--pos`/`--neg` color —
  sign and color always together.
- vs-SPY spreads: `pp` unit stated (`+3.2 pp`) so they are never mistaken for
  raw returns.
- Prices: `$` with cents in member rows (option-style precision is not
  needed, but per-share cents are decision-relevant); whole dollars in the
  portfolio-level average columns.
- Missing values: `—`, never `0` or blank.
- Dates and units live in headers/labels (`Avg price · 1 wk ago`), values
  stay bare.
- Symbols in `--font-mono`; numerics right-aligned with tabular numerals.

## 8. Accessibility checklist

- Sortable headers: real buttons + `aria-sort`.
- Row → drawer: rows are keyboard-activatable (existing table row pattern);
  drawer retains Escape-to-close, focus containment, focus restoration.
- Chips and remove buttons: focusable, labeled, visible focus ring.
- Range bar in 52-wk column is decorative-plus: printed low/high/current
  values carry the information; the bar has `aria-hidden="true"`.
- Nav link exposes `aria-current="page"`.
- Narrow viewport: table scrolls horizontally with the Name/Symbol column
  sticky; header stacks eyebrow/title/actions per the existing shell.

## 9. Backend and data model (implementation notes)

New CSVs under `data/portfolios/`, following the ledger config-accessor
pattern in `stock-app/app/config.py`:

- `portfolios.csv` — `id, name, description, sector, industry, created_date, created_at`
- `portfolio_members.csv` — `portfolio_id, symbol, added_date, price_at_add`

`price_at_add` is the cached close recorded when the member is added
(provenance only; computation always re-derives from the cache per the
backfill rule).

New FastAPI router `routers/portfolios.py`:

| Endpoint | Purpose |
| --- | --- |
| `GET /portfolios` | Summary rows: all computed columns for the list table + SPY YTD + cache as-of date. |
| `POST /portfolios` | `{name, description?, sector?, industry?, symbols[]}` — validates name uniqueness and universe membership; returns the created summary row. Invalid symbols → 422 naming them. |
| `GET /portfolios/{id}` | Detail: members with price, 1wk, 52wk low/high, YTD, inception return, baseline-fallback flags. |
| `PUT /portfolios/{id}` | Edit metadata (`{name?, description?, sector?, industry?}`). |
| `DELETE /portfolios/{id}` | Hard delete (portfolio + members). |
| `POST /portfolios/{id}/symbols` | `{symbols[]}` — add members, same validation. |
| `DELETE /portfolios/{id}/symbols/{symbol}` | Remove member. |

All computation is server-side (single source of truth for return math); the
Angular component sorts client-side on the returned numbers. Price reads use
the existing `data_reader.read_prices` flat-file path — no network calls.

Frontend: new `portfolios/` standalone component + route; API methods added
to the existing `StockService` (or a sibling `portfolio.service.ts` in
`src/app/api/` if `stock.service.ts` is judged too large — decide at
implementation).

## 10. Acceptance criteria and handoff notes

Implementation ground rules (repo conventions):

- Work directly on `main`; implement, test, and commit there.
- Read `stock-app-ui/docs/UX_GUIDANCE.md` and `stock-app-ui/AGENTS.md` before
  writing UI code. Reuse tokens and shared primitives; do not invent a
  parallel overlay, table shell, or palette. Do not golf CSS — favor good UI
  over a minimal diff.
- Portfolio ids: opaque short ids (e.g. `uuid4().hex[:12]`); names are
  display-only and renameable, so never key on them.
- New config accessors follow the existing `_under(...)` pattern with
  `SFP_PORTFOLIOS_*` env overrides so tests can point at fixtures.
- Angular API surface: add a sibling `src/app/api/portfolio.service.ts`
  rather than growing `stock.service.ts`.
- Sortable-header implementation follows the Momentum scanner's existing
  pattern (real buttons, `aria-sort`, client-side sort).

Done means:

1. Backend: router + storage + computation with pytest coverage for the
   return math (equal-weight, backfill, baseline fallback, YTD boundary,
   vs-SPY spread), validation (universe membership, duplicate names,
   unknown portfolio id), and CRUD round-trips against tmp-path CSVs.
   Existing test suite still passes.
2. Frontend: `/portfolios` route, nav group, list table with the three
   sortable columns, create modal, detail drawer with member management and
   metadata editing (name, description, sector, industry) — all states
   (loading skeleton, empty, error, stale-price chip, missing-data badges)
   implemented, not just the happy path.
3. `ng build` clean; UI exercised in the browser against the real backend
   with at least two portfolios (one containing a partial-history or
   missing-data member) — verify sorting, drawer, add/remove, rename,
   delete confirm, narrow viewport, and keyboard traversal.
4. Committed on `main` with a descriptive message.

## 11. Out of scope (v1)

- Share counts / dollar weighting, dividends, and total-return math.
- Charts (equity curve vs SPY over time) — natural v2 once daily snapshots
  exist.
- Auto-refresh jobs or live intraday quotes.
- Archiving/soft-delete of portfolios.
- Benchmark choice other than SPY.
- Filtering the list by Sector/Industry tags — revisit when the portfolio
  count makes sorting insufficient.
