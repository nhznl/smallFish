import { MomentumSetup } from './stock';
import { SymbolChip, SymbolLookupEntry } from './portfolio';

export const TRACKED_STOCK_CATEGORY_SOLD = 'Sold Stock' as const;
export const TRACKED_STOCK_CATEGORY_TRACKING = 'Tracking' as const;
export const TRACKED_STOCK_CATEGORY_READY = 'Ready to Trade' as const;
export const TRACKED_STOCK_CATEGORIES = [
  TRACKED_STOCK_CATEGORY_SOLD,
  TRACKED_STOCK_CATEGORY_TRACKING,
  TRACKED_STOCK_CATEGORY_READY
] as const;
export type TrackedStockCategory = typeof TRACKED_STOCK_CATEGORIES[number];
export type TrackedStockCategoryFilter = 'ALL' | TrackedStockCategory;

/** Price provenance shared by tracked-stock responses. */
export interface TrackedStockSnapshot {
  as_of: string;
  last_expected_session: string;
  prices_stale: boolean;
  spy_ytd_return: number | null;
  spy_week_return: number | null;
  spy_price: number | null;
}

export interface TrackedStockRow {
  symbol: string;
  name: string;
  category: TrackedStockCategory;
  notes: string;
  target_date: string | null;
  target_amount: number | null;
  coverage_initiation_date: string;
  created_at: string;
  setup: MomentumSetup;
  setup_score: number | null;
  fifty_two_week_low: number | null;
  fifty_two_week_high: number | null;
  range_position: number | null;
  has_data: boolean;
  partial_history: boolean;
  price: number | null;
  price_date: string | null;
  coverage_return: number | null;
  spy_coverage_return: number | null;
  coverage_vs_spy: number | null;
  ytd_return: number | null;
  ytd_vs_spy: number | null;
}

export interface TrackedStockListResponse extends TrackedStockSnapshot {
  stocks: TrackedStockRow[];
}

export interface SymbolLookupResponse {
  as_of: string;
  known: SymbolLookupEntry[];
  unknown: string[];
}

export type { SymbolChip };
