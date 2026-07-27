// User-authored portfolios tracked equal-weighted against SPY.
//
// Every number here is computed by the backend from the shared price cache, so
// the component only sorts and formats — it never derives a return itself.

/** Price provenance shared by the list and detail payloads. */
export interface PortfolioSnapshot {
  /** Latest cached session (SPY's), the date every price on the page is from. */
  as_of: string;
  /** Most recent weekday, used to decide whether the cache has fallen behind. */
  last_expected_session: string;
  prices_stale: boolean;
  spy_ytd_return: number | null;
  spy_week_return: number | null;
  spy_price: number | null;
}

export interface PortfolioSummary {
  id: string;
  name: string;
  description: string;
  sector: string;
  industry: string;
  created_date: string;
  created_at: string;
  symbol_count: number;
  symbols: string[];
  /** Mean of member closes — a reference value, deliberately not a return base. */
  avg_price: number | null;
  avg_price_prior_week: number | null;
  week_return: number | null;
  inception_return: number | null;
  spy_inception_return: number | null;
  /** Percentage points of out/under-performance since the creation date. */
  inception_vs_spy: number | null;
  ytd_return: number | null;
  spy_ytd_return: number | null;
  ytd_vs_spy: number | null;
  /** Members with no cached data; excluded from every average above. */
  missing_data_symbols: string[];
  /** Members listed after the creation date, backfilled to their first close. */
  partial_history_symbols: string[];
}

export interface PortfolioMember {
  symbol: string;
  has_data: boolean;
  price: number | null;
  price_date: string | null;
  week_return: number | null;
  fifty_two_week_low: number | null;
  fifty_two_week_high: number | null;
  /** 0–100 position of the latest close within the 52-week range. */
  range_position: number | null;
  ytd_return: number | null;
  inception_return: number | null;
  inception_baseline_date: string | null;
  inception_baseline_close: number | null;
  partial_history: boolean;
  added_date: string;
  price_at_add: number | null;
}

export interface PortfolioListResponse extends PortfolioSnapshot {
  portfolios: PortfolioSummary[];
}

export interface PortfolioDetailResponse extends PortfolioSnapshot {
  portfolio: PortfolioSummary;
  members: PortfolioMember[];
}

export interface SymbolLookupEntry {
  symbol: string;
  name: string;
  sector: string;
  price: number | null;
  has_data: boolean;
}

export interface SymbolLookupResponse {
  as_of: string;
  known: SymbolLookupEntry[];
  unknown: string[];
}

/** One parsed chip under a symbol textarea. */
export interface SymbolChip {
  symbol: string;
  known: boolean;
  price: number | null;
  name: string;
}
