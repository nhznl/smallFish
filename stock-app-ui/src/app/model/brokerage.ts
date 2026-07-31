/**
 * Contracts for the brokerage-agnostic `/api/brokerages` surface.
 *
 * One interface per resource, shared by every brokerage. There is deliberately
 * no provider-specific model here: a component that needed one would be about
 * to branch on which brokerage it is rendering, which is the thing this API
 * exists to prevent. Use the declared `capabilities` and the returned
 * `availability` / `coverage` / `warnings` instead.
 */

/** A configured brokerage, not a connector. `snaptrade` is never one of these. */
export type BrokerageId = 'tastytrade' | 'fidelity';

export type PnlCompleteness = 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
export type LedgerState = 'ACTIVE' | 'CLOSED';
export type ReconciliationStatus = 'RECONCILED' | 'UNRECONCILED';
export type Exposure = 'EQUITY' | 'OPTIONS' | 'EQUITY_AND_OPTIONS';
export type AvailabilityStatus = 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE';

export interface BrokerageDescriptor {
  id: BrokerageId;
  label: string;
  institution: string;
  portfolio_role: string;
}

export interface BrokerageCoverage {
  status: PnlCompleteness;
  history_start: string | null;
  equity_activity: PnlCompleteness;
  option_activity: PnlCompleteness;
  /** `null` when the provider's earliest available date is unknown — which is
   *  not the same as knowing the retained history is complete. */
  reached_provider_boundary: boolean | null;
  reasons: string[];
}

export interface BrokerageWarning {
  code: string;
  scope: string;
  symbol: string | null;
  component_id: string | null;
  message: string;
}

/** Every read resource shares this shape; only `summary` and `items` differ. */
export interface BrokerageEnvelope<TItem, TSummary> {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  availability: { status: AvailabilityStatus; reasons: string[] };
  as_of: { positions: string | null; activity: string | null; market: string | null };
  coverage: BrokerageCoverage;
  summary: TSummary;
  items: TItem[];
  warnings: BrokerageWarning[];
}

export interface ComponentProvenance {
  position_source: string | null;
  activity_source: string | null;
  market_source: string | null;
  position_retrieved_at: string | null;
  activity_retrieved_at: string | null;
  mark_observed_at: string | null;
  mark_retrieved_at: string | null;
}

/** One account-scoped position: an equity lot, or one exact option contract. */
export interface BrokerageComponent {
  id: string;
  account_id: string;
  account: string;
  instrument: 'EQUITY' | 'OPTION';
  symbol: string;
  side: 'LONG' | 'SHORT';
  option_type: 'CALL' | 'PUT' | null;
  state: 'OPEN' | 'FLAT';
  quantity: number;
  strike: number | null;
  expiry: string | null;
  contract_key: string | null;
  cash_in: number | null;
  cash_out: number | null;
  net_cash_flow: number | null;
  mark_per_unit: number | null;
  mark_observed_at: string | null;
  open_price_per_unit: number | null;
  multiplier: number | null;
  open_market_value: number | null;
  realized_pnl: number | null;
  total_pnl: number | null;
  pnl_completeness: PnlCompleteness;
  cash_flow_basis: string;
  open_leg_count: number;
  event_count: number;
  provenance: ComponentProvenance;
  missing: string[];
}

// --------------------------------------------------------------- holdings ---

/**
 * Adverse-move state recorded at sync time. `alert` is sticky: set on the last
 * move past the configured threshold, cleared by a favorable one. The
 * from/to/drop values describe that move and are null while none stands.
 */
export interface HoldingTrend {
  alert: boolean;
  peak_pct: number | null;
  peak_at: string;
  drop_pct: number | null;
  from_pct: number | null;
  to_pct: number | null;
  alert_at: string | null;
  direction: 'GAIN' | 'LOSS';
}

/** One retained capture date: one comparison column. */
export interface GainLossSnapshotDate {
  sync_date: string;
  retrieved_at: string;
  captured_at: string;
}

export interface HoldingItem extends BrokerageComponent {
  category: string;
  industry: string;
  note: string;
  metadata_updated_at: string | null;
  cost_basis: number | null;
  cost_per_unit: number | null;
  cost_basis_source: 'BROKER' | 'USER_OVERRIDE' | null;
  cost_basis_override_mode: 'TOTAL' | 'PER_UNIT' | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  pct_of_total: number | null;
  trend: HoldingTrend;
  /** Captured percentage by sync date; a date with no measurement is absent. */
  gain_loss_snapshots: Record<string, number>;
}

export interface HoldingsPerformanceBaselines {
  total_contributions: number | null;
  year_beginning_balance: number | null;
  baseline_year: number | null;
  contributions_gain_loss: number | null;
  contributions_return_pct: number | null;
  ytd_gain_loss: number | null;
  ytd_return_pct: number | null;
  updated_at: string | null;
}

export interface HoldingsSummary {
  holding_count: number;
  account_count: number;
  total_cost_basis: number | null;
  total_market_value: number | null;
  total_unrealized_pnl: number | null;
  total_unrealized_pnl_pct: number | null;
  performance_baselines: HoldingsPerformanceBaselines;
  gain_loss_snapshots: GainLossSnapshotDate[];
  pnl_completeness: PnlCompleteness;
}

export type HoldingsResponse = BrokerageEnvelope<HoldingItem, HoldingsSummary>;

export interface HoldingsMetadataResponse {
  schema_name: string;
  schema_version: number;
  brokerage_id: BrokerageId;
  metadata: Record<string, string>;
}

export interface HoldingsSettingsResponse {
  schema_name: string;
  schema_version: number;
  brokerage_id: BrokerageId;
  settings: HoldingsPerformanceBaselines;
}

export interface GainLossSnapshotResponse {
  schema_name: string;
  schema_version: number;
  brokerage_id: BrokerageId;
  sync_date: string;
  retrieved_at: string;
  captured_at: string;
  replaced: boolean;
  holding_count: number;
  retained_dates: string[];
}

// ---------------------------------------------------------------- options ---

export interface OptionItem extends BrokerageComponent {
  implied_volatility: number | null;
  implied_volatility_observed_at: string | null;
}

export interface OptionsSummary {
  contract_count: number;
  open_contract_count: number;
  symbol_count: number;
  account_count: number;
  open_market_value: number | null;
  total_pnl: number | null;
  pnl_completeness: PnlCompleteness;
}

export type OptionsResponse = BrokerageEnvelope<OptionItem, OptionsSummary>;

// --------------------------------------------------- option-adjusted basis ---

export interface AdjustedBasis {
  realized_per_share: number | null;
  marked_per_share: number | null;
  completeness: PnlCompleteness;
  reason: string | null;
}

export interface AdjustedBasisItem {
  symbol: string;
  accounts: string[];
  share_quantity: number;
  equity_cost: number | null;
  equity_cost_per_share: number | null;
  current_equity: number | null;
  equity_pnl: number | null;
  option_market_value: number | null;
  option_pnl: number | null;
  net_pnl: number | null;
  pnl_completeness: PnlCompleteness;
  adjusted_basis: AdjustedBasis;
  components: BrokerageComponent[];
}

export interface AdjustedBasisSummary {
  symbol_count: number;
  incomplete_symbol_count: number;
  net_pnl: number | null;
  pnl_completeness: PnlCompleteness;
}

export type AdjustedBasisResponse = BrokerageEnvelope<AdjustedBasisItem, AdjustedBasisSummary>;

// ---------------------------------------------------------- symbol ledger ---

export interface LedgerPeriod {
  period_version: string;
  started_at: string | null;
  event_count: number;
  first_event_at: string | null;
  last_event_at: string | null;
  net_cash_flow: number | null;
  open_market_value: number | null;
  total_pnl: number | null;
  realized_pnl: number | null;
}

export type UnderlyingPriceSource = 'EQUITY_MARK' | 'CACHED_CLOSE';
export type StrikeRisk = 'ITM' | 'NEAR_STRIKE' | 'NONE' | 'UNKNOWN';
export type BreakevenKind = 'SHORT_CALL' | 'SHORT_PUT' | 'SHORT_STRANGLE';
export type BreakevenPointRole = 'SPOT' | 'STRIKE' | 'BREAKEVEN';

export interface BreakevenPoint {
  role: BreakevenPointRole;
  value: number;
}

export interface BreakevenBand {
  kind: BreakevenKind;
  points: BreakevenPoint[];
}

export interface SymbolLedgerSummary {
  symbol: string;
  state: LedgerState;
  reconciliation_status: ReconciliationStatus;
  pnl_completeness: PnlCompleteness;
  accounts: string[];
  exposure: Exposure;
  current_period: LedgerPeriod;
  archived_period_count: number;
  archived_pnl: number | null;
  lifetime_pnl: number | null;
  notes: string;
  warnings: string[];
  underlying_price: number | null;
  underlying_price_source: UnderlyingPriceSource | null;
  dte: number | null;
  nearest_expiry: string | null;
  breakeven: BreakevenBand | null;
  strike_risk: StrikeRisk;
  strategy: string | null;
}

export interface ArchiveSummary {
  archive_id: string;
  symbol: string;
  period_started_at: string | null;
  period_ended_at: string;
  event_count: number;
  realized_pnl: number | null;
  pnl_completeness: PnlCompleteness;
  verification_status: 'VERIFIED' | 'CHANGED';
  created_at: string;
  note: string;
  warnings: string[];
}

export interface SymbolLedgerDetail extends SymbolLedgerSummary {
  reset_eligible: boolean;
  reset_blockers: string[];
  components: BrokerageComponent[];
  archives: ArchiveSummary[];
  event_count_total: number;
}

export interface SymbolLedgerListSummary {
  symbol_count: number;
  active_count: number;
  closed_count: number;
  needs_review_count: number;
  lifetime_pnl: number | null;
}

export type SymbolLedgerListResponse =
  BrokerageEnvelope<SymbolLedgerSummary, SymbolLedgerListSummary>;

export interface SymbolLedgerDetailResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  availability: { status: AvailabilityStatus; reasons: string[] };
  as_of: { positions: string | null; activity: string | null; market: string | null };
  coverage: BrokerageCoverage;
  symbol: SymbolLedgerDetail;
  warnings: BrokerageWarning[];
}

export interface LedgerEvent {
  provider_event_id: string;
  account_id: string;
  account: string;
  symbol: string;
  instrument: string;
  contract_key: string | null;
  option_type: 'CALL' | 'PUT' | null;
  strike: number | null;
  expiry: string | null;
  action: string;
  quantity_delta: number | null;
  net_cash_flow: number | null;
  fees: number | null;
  executed_at: string;
  imported_at: string | null;
  source: string;
  is_manual_reconciliation: boolean;
  missing: string[];
}

export interface LedgerEventsResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  symbol: string;
  period: string;
  total_event_count: number;
  items: LedgerEvent[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ArchiveCreatedResponse {
  schema_name: string;
  schema_version: number;
  archive: ArchiveSummary;
  symbol: SymbolLedgerDetail;
}

export interface BrokerageSyncResult {
  resource: 'HOLDINGS' | 'ACTIVITY' | 'MARKET_DATA';
  status: 'OK' | 'FAILED' | 'UNSUPPORTED';
  detail: Record<string, string | number | boolean | string[]> | null;
  warnings: string[];
}

export interface BrokerageSyncResponse {
  schema_name: string;
  schema_version: number;
  brokerage_id: BrokerageId;
  results: BrokerageSyncResult[];
}

/** A public failure: a stable code plus a message safe to show a user. */
export interface BrokerageErrorDetail {
  code: string;
  message: string;
}
