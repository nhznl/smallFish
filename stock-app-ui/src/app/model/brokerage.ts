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
  put_cash_required: number;
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
  resource: 'HOLDINGS' | 'ACTIVITY' | 'MARKET_DATA' | 'ACCOUNT_CAPITAL';
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

// --------------------------------------------------- portfolio analysis ---

export type PortfolioRole = 'TRADING' | 'RETIREMENT';
export type PortfolioObjective = 'SPECULATIVE_TRADING' | 'LONG_TERM_AGGRESSIVE_GROWTH';
export type PortfolioProfileStatus = 'UNCONFIGURED' | 'PARTIAL' | 'COMPLETE';
export type PortfolioProfileFit =
  | 'CRITICAL_RISK' | 'ABOVE_PROFILE' | 'MIXED' | 'BELOW_PROFILE'
  | 'ALIGNED' | 'NEEDS_REVIEW' | 'NOT_ASSESSED';
export type PortfolioConstruction =
  | 'WELL_CONSTRUCTED' | 'CONCENTRATED' | 'FRAGILE' | 'NEEDS_REVIEW';
export type PortfolioCapitalDeployment =
  | 'BELOW_RANGE' | 'IN_RANGE' | 'ABOVE_RANGE' | 'MIXED' | 'NOT_ASSESSED';
export type PortfolioDataConfidence = 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
export type PortfolioRiskCalculationStatus = PortfolioDataConfidence | 'NOT_CALCULATED';
export type PortfolioFindingSeverity = 'INFO' | 'CAUTION' | 'HIGH' | 'CRITICAL';
export type PortfolioFindingDirection = 'UNDER' | 'OVER' | 'NEUTRAL';
export type PortfolioAllocationBucket = 'GROWTH' | 'SPECULATIVE' | 'DEFENSIVE' | 'CASH' | 'UNKNOWN';

/** Owner-selected limits. Nullable fields are deliberately not defaulted by the UI. */
export interface PortfolioAnalysisProfile {
  objective: PortfolioObjective;
  status: PortfolioProfileStatus;
  max_single_issuer_pct?: number | null;
  max_speculative_pct?: number | null;
  max_put_assignment_commitment_pct?: number | null;
  max_stress_loss_pct?: number | null;
  minimum_liquid_pct?: number | null;
  notes?: string;
  reviewed_at?: string | null;
  max_gross_exposure_pct?: number | null;
  deployment_min_pct?: number | null;
  deployment_max_pct?: number | null;
  max_sector_pct?: number | null;
  growth_min_pct?: number | null;
  growth_max_pct?: number | null;
  cash_min_pct?: number | null;
  cash_max_pct?: number | null;
  max_top_five_pct?: number | null;
  first_expected_withdrawal_date?: string | null;
}

export type PortfolioAnalysisProfileUpdate = Partial<
  Omit<PortfolioAnalysisProfile, 'objective' | 'reviewed_at' | 'status'>
>;

export interface PortfolioVerdicts {
  profile_fit: PortfolioProfileFit;
  construction: PortfolioConstruction;
  capital_deployment: PortfolioCapitalDeployment;
  data_confidence: PortfolioDataConfidence;
}

export interface PortfolioCapitalAccount {
  account_id: string;
  account: string;
  currency: string;
  net_liquidating_value: number | null;
  cash_balance: number | null;
  buying_power: number | null;
  maintenance_requirement: number | null;
  source: string;
  retrieved_at: string | null;
  missing: string[];
}

export interface PortfolioCapitalSummary {
  analyzed_capital: number | null;
  liquid_value: number | null;
  accounts: PortfolioCapitalAccount[];
  reconciliation_gap: number | null;
}

export interface PortfolioAllocationValue {
  market_value: number | null;
  pct_of_capital: number | null;
}

export interface PortfolioAllocationSummary {
  buckets: Record<PortfolioAllocationBucket, PortfolioAllocationValue>;
  growth_pct: number | null;
  liquid_pct: number | null;
  deployment_pct: number | null;
  gross_marked_exposure_pct: number | null;
}

export interface PortfolioIssuerConcentration {
  symbol: string;
  market_value: number | null;
  pct_of_capital: number | null;
}

export interface PortfolioSectorConcentration {
  sector: string;
  market_value: number | null;
  pct_of_capital: number | null;
}

export interface PortfolioConcentrationSummary {
  largest_issuer_pct: number | null;
  top_five_pct: number | null;
  effective_position_count: number | null;
  issuers: PortfolioIssuerConcentration[];
  sectors: PortfolioSectorConcentration[];
  sector_classified_pct: number | null;
}

export interface PortfolioHistoricalRisk {
  status: PortfolioRiskCalculationStatus;
  label: string;
  reason?: string | null;
  date_start?: string | null;
  date_end?: string | null;
  aligned_sessions: number;
  analyzed_market_value: number | null;
  excluded_symbols: string[];
  excluded_pct: number | null;
  annualized_volatility_pct: number | null;
  beta_vs_spy?: number | null;
  correlation_vs_spy?: number | null;
  maximum_drawdown_pct?: number | null;
}

export interface PortfolioStressScenario {
  shock_pct: number;
  estimated_loss: number | null;
  estimated_loss_pct: number | null;
}

export interface PortfolioStressSummary {
  status: PortfolioDataConfidence;
  classification: 'HYPOTHETICAL';
  scenarios: PortfolioStressScenario[];
  severe_loss_pct: number | null;
  excluded_value: number | null;
}

export interface PortfolioUncoveredCall {
  account_id: string;
  symbol: string;
  contract_key: string | null;
  uncovered_units: number;
}

export interface PortfolioOptionCommitments {
  open_contract_count: number;
  put_assignment_commitment: number | null;
  put_assignment_commitment_pct: number | null;
  long_option_premium_at_risk: number | null;
  long_option_premium_at_risk_pct: number | null;
  by_underlying: Array<{ symbol: string; amount: number; pct_of_capital: number | null }>;
  uncovered_short_calls: PortfolioUncoveredCall[];
  risk_completeness: PortfolioDataConfidence;
  missing: string[];
  note: string;
}

export interface PortfolioRemediation {
  immediate_trim_amount: number | null;
  approximate_units: number | null;
  new_outside_capital_to_dilute: number | null;
  price: number | null;
  price_source: string | null;
  price_as_of: string | null;
}

export interface PortfolioFinding {
  code: string;
  severity: PortfolioFindingSeverity;
  direction: PortfolioFindingDirection;
  scope: string;
  symbol: string | null;
  title: string;
  actual: number | null;
  limit: number | null;
  unit: string;
  excess_amount: number | null;
  explanation: string;
  remediation: Partial<PortfolioRemediation>;
}

export interface PortfolioAnalysisItem {
  account_id: string;
  account: string;
  symbol: string;
  instrument: string;
  quantity: number;
  market_value: number | null;
  weight_pct: number | null;
  allocation_bucket: PortfolioAllocationBucket;
  classification_source: string;
  sector: string | null;
  security_type: string | null;
  mark_per_unit: number | null;
  price_source: string | null;
  price_as_of: string | null;
}

export interface PortfolioAnalysisSummary {
  profile: PortfolioAnalysisProfile;
  verdicts: PortfolioVerdicts;
  capital: PortfolioCapitalSummary;
  allocation: PortfolioAllocationSummary;
  concentration: PortfolioConcentrationSummary;
  historical_risk: PortfolioHistoricalRisk;
  stress: PortfolioStressSummary;
  option_commitments: PortfolioOptionCommitments;
  findings: PortfolioFinding[];
}

export interface PortfolioAnalysisResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  availability: { status: AvailabilityStatus; reasons: string[] };
  as_of: {
    positions: string | null;
    activity: string | null;
    market: string | null;
    capital: string | null;
    cached_prices: string | null;
  };
  coverage: BrokerageCoverage;
  summary: PortfolioAnalysisSummary;
  items: PortfolioAnalysisItem[];
  warnings: BrokerageWarning[];
}

export interface PortfolioProfileResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  profile: PortfolioAnalysisProfile;
}

export interface PortfolioClassificationResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  classification: {
    brokerage_id: BrokerageId;
    account_id: string;
    symbol: string;
    allocation_bucket: PortfolioAllocationBucket;
    updated_at: string;
  } | null;
  cleared: boolean;
}

export interface PortfolioPreviewRequest {
  account_id: string;
  side: 'BUY' | 'SELL';
  symbol: string;
  quantity: number | null;
  notional: number | null;
  assumed_price: number | null;
  funding_source: 'ACCOUNT_CASH' | 'NEW_CONTRIBUTION';
  allocation_bucket: PortfolioAllocationBucket | null;
}

export interface PortfolioPreviewMetric {
  metric: string;
  before: number | null;
  after: number | null;
  change: number | null;
}

export interface PortfolioPreviewProposal {
  account_id: string;
  side: 'BUY' | 'SELL';
  symbol: string;
  quantity: number;
  notional: number;
  assumed_price: number;
  price_source: string;
  price_as_of: string | null;
  funding_source: string;
  fees_taxes_slippage_included: false;
}

export interface PortfolioPreviewResponse {
  schema_name: string;
  schema_version: number;
  brokerage: BrokerageDescriptor;
  proposal: PortfolioPreviewProposal;
  before: PortfolioAnalysisSummary;
  after: PortfolioAnalysisSummary;
  metric_deltas: PortfolioPreviewMetric[];
  new_findings: PortfolioFinding[];
  worsened_findings: PortfolioFinding[];
  improved_findings: PortfolioFinding[];
  resolved_findings: PortfolioFinding[];
  persisted: false;
}
