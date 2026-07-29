export type OptionsAccount = string;
export type OptionsTradeType = 'SHORT_PUT' | 'COVERED_CALL' | 'SHORT_CALL' | 'LONG_PUT' | 'LONG_CALL' | 'STOCK' | 'OTHER';
export type OptionsStatus = 'OPEN' | 'CLOSED' | 'EXPIRED' | 'ASSIGNED';
/** Share coverage of a short call. It ignores offsetting option legs. */
export type OptionsCoverage = 'COVERED' | 'PARTIAL' | 'UNCOVERED';

export interface OptionsLedgerRow {
  id: string;
  account: OptionsAccount;
  wheel_id: string;
  symbol: string;
  trade_type: OptionsTradeType;
  qty: number;
  strike: number | null;
  expiry: string;
  open_date: string;
  underlying_price_at_open: number;
  mark_price: number | null;
  mark_retrieved_at: string | null;
  credit: number | null;
  debit: number | null;
  close_date: string;
  status: OptionsStatus;
  non_standard: boolean;
  notes: string;
  profit?: number | null;
  dte_remaining?: number | null;
  current_underlying_price?: number | null;
  percent_to_strike?: number | null;
  needs_settlement?: boolean;
  /** Short calls only: whether long shares in the same account back the call. */
  coverage?: OptionsCoverage;
  covered_contracts?: number;
  wheel_combined_pnl?: number | null;
  wheel_pending_open_credit?: number | null;
  wheel_running_break_even?: number | null;
}

export interface OptionsTotals {
  open_broker_positions: number;
  open_short_puts: number;
  open_short_calls: number;
  gross_assignment_obligation: number;
}

export interface OptionsWheelGroup {
  wheel_id: string;
  account: OptionsAccount;
  combined_pnl: number;
  realized_pnl: number;
  pending_open_credit: number;
  running_break_even: number | null;
}

export interface OptionsRiskAccount {
  cash_limit: number | null;
  cash_limit_status: 'APPROVED' | 'PLACEHOLDER' | 'EXPIRED';
  completeness: 'COMPLETE' | 'PARTIAL' | 'UNAVAILABLE';
  included_position_count: number;
  excluded_position_count: number;
  gross_cash_commitment: {
    stock_cost: number;
    short_put_assignment_cash: number;
    total: number;
    ratio: number | null;
    warn: boolean | null;
    caveat: string;
  };
  stock_market_value: number;
  beta_weighted_delta_dollars: number | null;
  computed_beta_weighted_delta_dollars: number | null;
  diagnostic_partial_beta_delta_dollars: number | null;
  spy_equivalent_shares: number | null;
  computed_spy_equivalent_shares: number | null;
  band: OptionsRiskBand;
  computed_band: OptionsRiskBand;
  excluded_positions: { id: string; symbol: string; reasons: string[] }[];
  delta_contributions: { id: string; symbol: string; trade_type: string; bwd_dollars: number }[];
  largest_assignment_obligations: { id: string; symbol: string; obligation: number }[];
}

export interface OptionsRiskBand {
  band_min: number;
  band_max: number;
  normalized_beta_delta: number | null;
  in_band: boolean | null;
  gap_normalized: number | null;
  gap_dollars: number | null;
  gap_spy_shares: number | null;
}

export interface OptionsRiskSnapshot {
  as_of: string;
  spy_spot: number | null;
  spy_as_of: string | null;
  risk_free_rate: number;
  rate_as_of: string;
  accounts: Record<string, OptionsRiskAccount>;
  combined: {
    cash_limit: number | null;
    cash_limit_status: 'APPROVED' | 'PLACEHOLDER' | 'EXPIRED';
    completeness: 'COMPLETE' | 'PARTIAL' | 'UNAVAILABLE';
    included_position_count: number;
    excluded_position_count: number;
    gross_cash_commitment_total: number;
    commitment_ratio: number | null;
    beta_weighted_delta_dollars: number | null;
    computed_beta_weighted_delta_dollars: number | null;
    diagnostic_partial_beta_delta_dollars: number | null;
    spy_equivalent_shares: number | null;
    computed_spy_equivalent_shares: number | null;
    band: OptionsRiskBand;
    computed_band: OptionsRiskBand;
  };
  positions: OptionsPositionRisk[];
  warnings: {
    short_gamma: { id: string; symbol: string; trade_type: string }[];
    needs_settlement: { id: string; symbol: string }[];
  };
  caveat: string;
}

export interface OptionsPositionRisk {
  row_id: string;
  account: OptionsAccount;
  symbol: string;
  trade_type: OptionsTradeType;
  qty: number;
  dte: number | null;
  spot: number | null;
  price_as_of: string | null;
  delta_shares: number | null;
  delta_source: string | null;
  vol_annual: number | null;
  vol_as_of: string | null;
  vol_stale_sessions: number | null;
  beta_weighted_delta_dollars: number | null;
  computed_beta_weighted_delta_dollars: number | null;
  beta: number | null;
  beta_as_of: string | null;
  computed_beta: number | null;
  computed_beta_as_of: string | null;
  computed_beta_r_squared: number | null;
  computed_beta_sample_count: number | null;
  computed_beta_fallback: boolean;
  tasty_beta: number | null;
  tasty_beta_as_of: string | null;
  beta_source: string | null;
  beta_stale_sessions: number | null;
  unavailable_reasons: string[];
  needs_settlement: boolean;
  short_gamma_warning: boolean;
}

export interface OptionsSnapshot {
  as_of: string;
  account_filter: string;
  configured_accounts: OptionsAccount[];
  rows: OptionsLedgerRow[];
  wheel_groups: Record<string, OptionsWheelGroup>;
  totals: { accounts: Record<string, OptionsTotals>; combined: OptionsTotals };
  risk: OptionsRiskSnapshot;
  warnings: {
    break_even: any[];
    ex_dividend: any[];
    event_concentration: any[];
  };
}

export interface OptionsActivityEvent {
  id: string;
  source: string;
  source_transaction_id: string;
  account: OptionsAccount;
  executed_at: string;
  transaction_date: string;
  transaction_type: string;
  transaction_sub_type: string;
  instrument_type: string;
  contract_symbol: string;
  contract_key: string;
  underlying_symbol: string;
  action: string;
  quantity: number | null;
  position_delta: number | null;
  price: number | null;
  value: number | null;
  net_value: number | null;
  fee_effect: number | null;
  option_type: 'PUT' | 'CALL' | '';
  expiry: string;
  strike: number | null;
  description: string;
  group_id: string | null;
  group_name: string | null;
}

export interface OptionsGroupPosition {
  contract_key: string;
  quantity: number;
  option_type: 'PUT' | 'CALL' | null;
  expiry: string | null;
  strike: number | null;
  mark_price: number | null;
  market_value: number | null;
}

export interface OptionsTradeGroup {
  group_id: string;
  account: OptionsAccount;
  symbol: string;
  name: string;
  status: 'ACTIVE' | 'ARCHIVED';
  notes: string;
  auto_created: string;
  event_count: number;
  first_execution: string | null;
  last_execution: string | null;
  net_cash_flow: number;
  fee_effect: number;
  open_market_value: number | null;
  total_pnl: number | null;
  realized_pnl: number | null;
  position_status: 'OPEN' | 'FLAT';
  pnl_completeness: 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
  missing_marks: string[];
  open_positions: OptionsGroupPosition[];
  mark_retrieved_at: string | null;
}

export interface OptionsReconciliationIssue {
  contract_key: string;
  underlying_symbol: string;
  account: OptionsAccount | null;
  instrument_type: string | null;
  activity_quantity: number;
  broker_quantity: number;
  difference: number;
  event_count: number;
  first_execution: string | null;
  last_execution: string | null;
  last_event_summary: string | null;
  group_id: string | null;
  group_name: string | null;
}

export interface OptionsActivitySnapshot {
  schema_name: string;
  schema_version: number;
  account_filter: string;
  events: OptionsActivityEvent[];
  groups: OptionsTradeGroup[];
  ungrouped_event_count: number;
  reconciliation_issues: OptionsReconciliationIssue[];
  /** User-entered ledger corrections; preserved across Tastytrade syncs. */
  manual_events: OptionsActivityEvent[];
  last_sync_at: string | null;
  pnl_definition: string;
}

export interface OptionsActivitySyncReport {
  source: string;
  environment: string;
  account: OptionsAccount;
  start_date: string;
  end_date: string;
  broker_transactions_read: number;
  option_events_selected: number;
  events_inserted: number;
  events_updated: number;
  position_marks: number;
  greeks_observed: number;
  greeks_retained: number;
  greeks_missing: number;
  greeks_error: string | null;
  betas_observed: number;
  betas_retained: number;
  betas_missing: number;
  betas_error: string | null;
  groups_created: number;
  events_auto_grouped: number;
  groups_reactivated: number;
  retrieved_at: string;
}
