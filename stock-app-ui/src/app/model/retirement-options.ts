// Retirement options: trade groups + broker risk positions, sourced from
// SnapTrade legs and the broker-agnostic options risk engine.

import { OptionsCoverage, OptionsRiskAccount } from './options-ledger';

export interface RetirementOptionRow {
  id: string;
  account: string;
  wheel_id: string;
  symbol: string;              // underlying
  trade_type: string;         // SHORT_PUT | SHORT_CALL | COVERED_CALL | LONG_PUT | LONG_CALL
  qty: number;
  strike: number | null;
  expiry: string;
  contract_symbol: string;
  mark_price: number | null;
  mark_retrieved_at: string | null;
  status: string;
  non_standard: boolean;
  dte_remaining?: number | null;
  current_underlying_price?: number | null;
  /** Session the spot closed on, from the price cache. */
  price_as_of?: string | null;
  percent_to_strike?: number | null;
  needs_settlement?: boolean;
  /** Short calls only: whether long shares in the same account back the call. */
  coverage?: OptionsCoverage;
  covered_contracts?: number;
}

export interface RetirementOptionRisk {
  row_id: string;
  symbol: string;
  trade_type: string;
  qty: number;
  dte: number | null;
  spot: number | null;
  delta_shares: number | null;
  delta_source: string | null;
  vol_annual: number | null;
  vol_as_of: string | null;
  beta_weighted_delta_dollars: number | null;
  computed_beta_weighted_delta_dollars: number | null;
  computed_beta: number | null;
  computed_beta_fallback: boolean;
  computed_beta_as_of: string | null;
  tasty_beta: number | null;
  tasty_beta_as_of: string | null;
  unavailable_reasons: string[];
  needs_settlement: boolean;
  short_gamma_warning: boolean;
}

export interface RetirementOptionGroup {
  symbol: string;
  account: string;
  name: string;
  status: 'ACTIVE' | 'ARCHIVED';
  net_cash_flow: number;
  // Null when P/L cannot be valued (an open contract whose mark is unavailable —
  // e.g. a close that has left the positions feed but whose closing event has
  // not yet posted to the activity ledger).
  open_market_value: number | null;
  total_pnl: number | null;
  // Realized P/L for a fully-closed (FLAT) group; null while the group is open.
  realized_pnl: number | null;
  position_status: 'OPEN' | 'FLAT';
  pnl_completeness: 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
  event_count: number;
  notes: string;
  /**
   * Equity lots held in this underlying. Context only: shares are not option
   * events and are excluded from every total on the group.
   */
  equity_holding?: RetirementEquityHolding;
}

export interface RetirementShareLot {
  account: string;
  quantity: number;
  average_price: number;
  price: number;
  cost_basis: number;
  market_value: number;
  open_pnl: number;
  retrieved_at: string;
}

export interface RetirementEquityHolding {
  lots: RetirementShareLot[];
  total_shares: number;
  /** Zero when no call is written against these shares. */
  short_call_contracts: number;
  covered_contracts: number;
}

export interface RetirementOptionEvent {
  id: string;
  trade_date: string;
  underlying_symbol: string;
  occ_symbol: string;      // OCC contract symbol
  option_type: string;    // PUT | CALL
  strike: number | null;
  expiry: string;
  action: string;         // SELL_TO_OPEN | BUY_TO_CLOSE | …
  activity_type: string;  // SELL | BUY
  units: number | null;
  price: number | null;
  net_value: number | null;  // signed net cash flow incl. fees
  fee: number | null;
  description: string;
}

export interface RetirementOptionsData {
  as_of: string;
  rows: RetirementOptionRow[];
  groups: RetirementOptionGroup[];
  events: RetirementOptionEvent[];
  risk: {
    accounts: Record<string, OptionsRiskAccount>;
    combined: {
      cash_limit: number | null;
      cash_limit_status: string;
      completeness: string;
      included_position_count: number;
      excluded_position_count: number;
      beta_weighted_delta_dollars: number | null;
      computed_beta_weighted_delta_dollars: number | null;
      spy_equivalent_shares: number | null;
      computed_spy_equivalent_shares: number | null;
    };
    positions: RetirementOptionRisk[];
    spy_spot: number | null;
    spy_as_of: string | null;
    caveat: string;
    warnings: {
      short_gamma: { symbol: string }[];
      needs_settlement: { symbol: string }[];
    };
  };
  summary: { position_count: number; group_count: number };
}
