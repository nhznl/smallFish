# smallFish UX Guidance

**Audience:** agents and contributors creating or changing the smallFish Angular UI.

This document records the durable UX principles and conventions of the current
application. It is not a redesign backlog or a pixel-by-pixel specification.
Preserve the established visual language, reuse existing primitives, and make
intentional deviations only when a product requirement calls for them.

## 1. Sources of truth

Before changing UI code, inspect the existing implementation that is closest to
the work:

- `src/styles.scss` owns the Material theme, design tokens, base styles, and
  shared CSS primitives, including the Options/Retirement portfolio-risk
  comparison card and beta table.
- `src/app/shared/ui/` owns reusable behavior for drawers and modals.
- `src/app/app.html` and `src/app/app.css` define the application shell and
  primary navigation.
- Momentum is the reference for scan/filter/results workflows. Strategy and
  Wheel show how that language extends to other research tools. Options and
  Retirement are the reference for ledger, warning, and portfolio-risk views.

Code is authoritative when this document and the implementation differ. Fix
the documentation as part of any intentional convention change.

## 2. Product principles

### Keep dense workflows readable

smallFish is a data-heavy research and portfolio application. Favor compact,
well-aligned controls and tables, but create hierarchy with page headers,
panels, stat strips, spacing, and progressive disclosure. Dense should never
mean an undifferentiated wall of fields.

### Put decisions before exhaustive detail

Show the information needed to understand and act on a row in the main view.
Move secondary explanations, score decomposition, formulas, and long-form
reasons into a drawer, modal, tooltip, or explainer page. Do not discard useful
diagnostics merely to make a table smaller.

### Make risk visible

Warnings, stale data, preliminary evidence, missing marks, and scope caveats
must be visible where the user makes a decision. Use a semantic badge, banner,
row edge, icon, or short label in addition to color. Do not bury risk solely in
a score or tooltip.

### Avoid duplicate concepts and controls

Do not add another category, view, filter, or gate when the classifier or score
already expresses the same evidence. Preserve useful row-level inputs to that
decision, but avoid controls that hide data without adding information.

### Reuse before inventing

Use an existing token, primitive, component, or interaction pattern before
creating a local variant. A new shared pattern should solve a recurring need,
not merely rename component-specific CSS.

## 3. Visual language

### Tokens and theme

- Use the tokens in `src/styles.scss` for color, type, radius, elevation, and
  motion. Do not introduce a parallel palette or repeat token hex values in
  component styles.
- Keep Angular Material at the compact density configured globally. Do not
  compensate for default Material spacing with negative-margin hacks.
- Use `--font-ui` for interface text and `--font-mono` for symbols or values
  that benefit from fixed-width alignment.
- Use the shared slate surfaces and restrained card shadow. Elevation should
  communicate containment or overlay depth, not decorate every section.
- Avoid decorative emoji in page titles and actions. Use the established
  inline-SVG style when an icon materially improves recognition.

### Semantic color

- `--pos` / `--pos-soft`: positive returns, bullish state, or success.
- `--neg` / `--neg-soft`: negative returns, bearish state, error, or breach.
- `--warn` / `--warn-soft`: caution, stale or preliminary data, or proximity.
- `--info` / `--info-soft`: neutral explanatory state.
- `--primary` is for navigation, actions, selection, and brand emphasis—not
  for positive financial values.

Color is never the only signal. Pair it with a sign, label, icon, border, or
other textual state.

## 4. Page construction

### Page and header

- The application shell supplies the outer `.page` container. Use `.page-wide`
  or `.page-prose` only when the content justifies it.
- Start product views with `.page-header`: eyebrow, plain-language title,
  concise subtitle, and a `.page-actions` slot.
- Put timestamps and data provenance in `.snapshot-chip`. Keep primary actions
  in the header rather than scattering them across the first section.
- Use in-context “How this works” links for explainers. Documentation should
  not compete with primary tools in the global navigation.
- Use `.how-it-works` with `btn btn-ghost` for a header explainer link; it
  provides the shared, prominent treatment used by Strategy and Wheel.

### Panels, stats, tabs, and buttons

- Use `.panel`, `.panel-heading`, and `.panel-body` for grouped content.
- Use `.stat-strip` and `.stat-card` for compact summaries. Labels are short;
  values carry the visual emphasis.
- Use `.tab-bar` for navigation between sibling views. Segmented or pill tabs
  are appropriate for mutually exclusive result categories such as Momentum
  setups.
- Start with `.btn`, then add `.btn-primary`, `.btn-ghost`, `.btn-danger`, or a
  size modifier. Reserve the primary treatment for the main action in a region.
- Use `.badge` plus a semantic modifier for compact states. Badges are labels,
  not general-purpose buttons.

## 5. Tables and financial data

### Table structure

- Put data tables in `.table-shell` so borders, scrolling, sticky headers,
  hover treatment, and density remain consistent.
- Keep the highest-value columns visible. Put detailed decomposition and long
  text in the shared drawer rather than allowing the main table to become a raw
  model dump.
- Right-align numeric columns, use tabular numerals, and keep symbols easy to
  scan. Make the symbol or other row identity sticky when horizontal scrolling
  would otherwise remove context.
- Use grouped headers when several columns describe one concept, such as put
  versus call probabilities.
- Preserve meaningful diagnostic columns even when their values also feed a
  score. Remove only genuinely redundant views or gates.

### Formatting

- Positive and negative financial values use an explicit `+` or minus sign as
  well as semantic color.
- Percentages normally use one decimal place. Money uses whole dollars unless
  cents matter, such as option prices. Use compact formatting for large totals
  when exact precision is not decision-relevant.
- Render missing values as `—`, not `0`, an empty string, `null`, or `N/A`,
  unless zero has a distinct business meaning.
- Keep dates and units in headers or labels so values remain compact.
- Prefer concise labels such as `YTD`, `Mid point`, and `5 weeks` where the
  context is already established.

## 6. Interaction patterns

### Filters and jobs

- Group related filters in one compact toolbar with aligned control heights.
  Show the result count and provide Reset when more than one control can change
  the result set.
- Do not add a frontend filter that merely repeats score or classifier logic.
- Long-running actions must communicate all states: ready, running, success,
  and failure. Disable duplicate submission, show a spinner with `aria-busy`,
  and include a useful completion message or timestamp.

### Drawers, modals, and tooltips

- Use the shared drawer for row inspection and the shared modal for focused
  editing or confirmation. Do not create another overlay implementation.
- Preserve Escape-to-close, focus containment, focus restoration, backdrop
  behavior, and dialog semantics supplied by the shared components.
- Use `matTooltip` for interactive explanations. Do not use native `title`
  attributes as the primary help mechanism.
- Put tooltips on meaningful header text or a single standalone help control;
  avoid an info glyph on every column.

### Async and exceptional states

- Use `.skeleton` layouts while primary content loads, a semantic `.banner`
  for actionable errors or warnings, and `.empty-state` for valid empty results.
- Empty states should explain what happened and, when useful, offer the next
  action. Do not leave a bare “Loading…” or an unexplained blank panel.
- Keep important caveats adjacent to the result they qualify.

## 7. Accessibility and responsive behavior

- Every action must be keyboard reachable and have a visible focus state.
- Sortable table headers use real buttons and expose `aria-sort`.
- Active navigation exposes `aria-current="page"`.
- Drawers and modals retain dialog roles, labels, keyboard handling, and focus
  management.
- Do not rely on hover, color, or position alone to communicate meaning.
- Keep body-sized text at readable contrast. Very small text is reserved for
  secondary labels, never essential instructions or values.
- At narrow widths, allow intentional horizontal table scrolling and stack
  headers or actions cleanly. Do not compress data until it becomes illegible.
- Verify sticky headers, sticky identity columns, grouped headers, and overlays
  at both desktop and narrow viewport sizes.

## 8. smallFish domain conventions

### Momentum and strategy

- The Momentum scanner has four mutually exclusive setups:
  `BULLISH_CONTINUATION`, `BEARISH_CONTINUATION`, `BULLISH_REVERSAL`, and
  `BEARISH_REVERSAL`.
- Preliminary reversal evidence stays in its source Bullish or Bearish setup.
  Show the warning and its score effect; do not create a separate “Reversal
  Watch” category.
- Setup scores represent strength within a direction. Do not present them as a
  signed cross-direction ranking without an explicit product decision.
- Keep diagnostic horizons and risk evidence visible even when they feed the
  setup or strategy score.

### Ledgers and portfolio risk

- Trading and Retirement expose the same three brokerage tabs in the same
  order: Holdings, Options, and Option-Adjusted Basis. Holdings contain equity
  information only; Options contain option information only.
- Holdings uses one shared, chart-free table on both brokerage pages. Show the
  Category or Account selector only when that field has more than one choice;
  always retain search, Declining only, Snapshot G/L %, Copy Symbols, sortable
  columns, snapshot columns, and deliberate modal editing for classification
  and notes.
- Options uses the shared Symbol Ledger on both brokerage pages. It includes
  only option-capable symbols, derives Active or Archived lifecycle state from
  immutable broker facts, and keeps imported events read-only. Show current,
  all-history, and archived periods on demand; retain account-aware option and
  equity components as reconciliation context. Notes are the only editable
  ledger metadata. Do not expose Trade Groups, manual lifecycle status, event
  reassignment, or broker-risk tables in this surface.
- Option-Adjusted Basis includes only symbols with open long-equity positions
  and option activity that affects their basis. Keep completed option cycles
  while the shares remain open; do not show redundant Exposure or State
  controls.
  Search is its only table filter. Keep all Share Position columns together
  before the Options columns, followed by Net P/L and the live option-adjusted
  basis. Calculate that basis from equity cost less option P/L only; do not
  include equity P/L. A `Basis unavailable` summary counts only genuinely
  unavailable calculations, not the normal indicative state of live marks.
- Symbol Ledger history is immutable broker evidence. Default its paginated
  history to the current period, allow an all-history view and an archived
  period on demand, and surface a changed-archive warning beside the affected
  summary. Offer Archive completed history only when the API marks the period
  eligible; the confirmation must name the symbol, event count, period, and
  realized P/L. A stale-period conflict refreshes facts, while an uncertain
  retry reuses its request identity so it cannot create a second archive.
- Retain warnings for breaches, near-strike positions, missing marks, stale
  data, and incomplete transaction history.
- Editing should be deliberate. Prefer readable ledger rows with focused modal
  editing over dense per-row forms and repeated Save buttons.

## 9. Workflow for UX changes

1. Inspect the closest existing surface and the shared primitives before
   designing a new pattern.
2. Confirm whether the request is visual, behavioral, or both. During a pure
   restyle, preserve data bindings and business logic exactly.
3. Reuse global tokens and shared components. If a pattern will recur, add it
   at the shared layer with a narrow, documented purpose.
4. Exercise the affected UI with representative real data. Tables are the acid
   test: check scrolling, sticky context, sorting, filters, warnings, and row
   inspection.
5. Verify loading, empty, error, stale, and narrow-screen states—not only the
   happy path.
6. Run the Angular build and relevant tests. Treat a clean compile as necessary
   but not sufficient evidence of good UX.
7. Update this document when intentionally changing an app-wide convention.

## 10. Review checklist

- Does the change look like part of the existing smallFish application?
- Is the primary decision or action visually clear?
- Are risk, freshness, provenance, and missing-data states visible?
- Is any new filter, category, or view duplicating existing logic?
- Are useful diagnostics preserved without crowding the main workflow?
- Are tokens, primitives, and shared overlays reused?
- Are financial values aligned, signed, formatted, and labeled consistently?
- Can the workflow be completed with a keyboard and at a narrow viewport?
- Have real data and exceptional states been checked in the browser?
