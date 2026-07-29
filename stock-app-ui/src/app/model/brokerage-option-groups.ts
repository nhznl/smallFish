export interface BrokerageOptionGroup {
  symbol: string;
  account: string;
  name: string;
  status: 'ACTIVE' | 'ARCHIVED';
  net_cash_flow: number;
  open_market_value: number | null;
  total_pnl: number | null;
  position_status: 'OPEN' | 'FLAT';
  pnl_completeness: 'COMPLETE' | 'INDICATIVE' | 'UNAVAILABLE';
  event_count: number;
  notes: string;
}
