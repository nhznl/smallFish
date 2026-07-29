export type BrokerageLedgerPortfolioId = 'TRADING' | 'RETIREMENT';
export type BrokerageLedgerPortfolioSlug = 'trading' | 'retirement';
export type BrokerageLedgerCompleteness = 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
export type BrokerageLedgerExposure = 'EQUITY' | 'OPTIONS' | 'EQUITY_AND_OPTIONS';
export type BrokerageLedgerState = 'OPEN' | 'FLAT';

export interface BrokerageLedgerPortfolio {
  id: BrokerageLedgerPortfolioId;
  label: string;
  brokerage: string;
}

export interface BrokerageLedgerAsOf {
  positions: string | null;
  activity: string | null;
  market: string | null;
}

export interface BrokerageLedgerCoverage {
  open_equity: BrokerageLedgerCompleteness;
  closed_equity: BrokerageLedgerCompleteness;
  options: BrokerageLedgerCompleteness;
  history_start: string | null;
  reasons: string[];
}

export interface BrokerageLedgerSummary {
  symbol_count: number;
  incomplete_symbol_count: number;
  equity_market_value: number | null;
  option_market_value: number | null;
  total_market_value: number | null;
  total_pnl: number | null;
}

export interface BrokerageLedgerAdjustedBasis {
  realized_per_share: number | null;
  marked_per_share: number | null;
  history_start: string | null;
  completeness: BrokerageLedgerCompleteness;
  reason: string | null;
}

export interface BrokerageLedgerAnnotation {
  scope: 'SYMBOL' | 'GROUP';
  kind: 'NOTE';
  text: string;
  source: 'USER';
  updated_at: string | null;
}

export interface BrokerageLedgerProvenance {
  position_source: string | null;
  activity_source: string | null;
  market_source: string | null;
  position_retrieved_at: string | null;
  activity_retrieved_at: string | null;
  mark_observed_at: string | null;
  mark_retrieved_at: string | null;
}

export interface BrokerageLedgerComponent {
  id: string;
  account_id: string;
  account: string;
  instrument: 'EQUITY' | 'OPTION';
  side: 'LONG' | 'SHORT';
  option_type: 'CALL' | 'PUT' | null;
  state: BrokerageLedgerState;
  quantity: number;
  strike: number | null;
  expiry: string | null;
  cash_in: number | null;
  cash_out: number | null;
  net_cash_flow: number | null;
  mark_per_unit: number | null;
  mark_observed_at: string | null;
  open_market_value: number | null;
  realized_pnl: number | null;
  total_pnl: number | null;
  pnl_completeness: BrokerageLedgerCompleteness;
  cash_flow_basis: 'BROKER_ACTIVITY' | 'POSITION_COST_BASIS' | 'UNAVAILABLE';
  open_leg_count: number;
  event_count: number;
  annotations: BrokerageLedgerAnnotation[];
  provenance: BrokerageLedgerProvenance;
  missing: string[];
}

export interface BrokerageLedgerSymbol {
  symbol: string;
  exposure: BrokerageLedgerExposure;
  state: BrokerageLedgerState;
  accounts: string[];
  current_price_per_share: number | null;
  share_quantity: number | null;
  equity_cost_per_share: number | null;
  equity_cost: number | null;
  current_equity: number | null;
  equity_pnl: number | null;
  equity_pnl_per_share: number | null;
  net_credit: number | null;
  net_debit: number | null;
  option_pnl: number | null;
  net_pnl: number | null;
  option_adjusted_basis_per_share: number | null;
  shares: number | null;
  cash_in: number | null;
  cash_out: number | null;
  net_cash_flow: number | null;
  equity_market_value: number | null;
  option_market_value: number | null;
  open_market_value: number | null;
  total_pnl: number | null;
  pnl_completeness: BrokerageLedgerCompleteness;
  adjusted_basis: BrokerageLedgerAdjustedBasis;
  components: BrokerageLedgerComponent[];
  annotations: BrokerageLedgerAnnotation[];
}

export interface BrokerageLedgerWarning {
  code: string;
  scope: 'PORTFOLIO' | 'SYMBOL' | 'COMPONENT';
  symbol: string | null;
  component_id: string | null;
  message: string;
}

export interface BrokerageLedgerSnapshot {
  schema_name: 'smallfish.brokerage-ledger';
  schema_version: 1;
  portfolio: BrokerageLedgerPortfolio;
  as_of: BrokerageLedgerAsOf;
  coverage: BrokerageLedgerCoverage;
  summary: BrokerageLedgerSummary;
  symbols: BrokerageLedgerSymbol[];
  warnings: BrokerageLedgerWarning[];
}
