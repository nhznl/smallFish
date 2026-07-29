/** Gain/loss trend across syncs: a peak high-water mark plus a sticky alert
 *  set when the gain shrinks (or the loss deepens) by the relative threshold. */
export interface RetirementHoldingTrend {
  alert: boolean;
  peakPct: number | null;
  peakAt: string;
  dropPct: number | null;   // relative worsening that tripped the alert, %
  fromPct: number | null;   // peak % at the time of the alert
  toPct: number | null;     // current % at the time of the alert
  alertAt: string | null;
  direction: 'GAIN' | 'LOSS';
}

export interface RetirementHolding {
  /** Symbol whose enrichment row (category/industry/note) this holding shows;
   *  options point at their underlying. */
  enrichmentSymbol: string;
  category: string;
  accountType: string;
  industry: string;
  symbol: string;
  costPrice: number;
  qty: number;
  initialInvestment: number;
  marketPrice: number;
  currentValue: number;
  pctOfTotal: number;
  gainLossPct: number;
  gainLoss: number;
  /** User-captured G/L percentages keyed by Fidelity sync date (YYYY-MM-DD). */
  gainLossSnapshots: Record<string, number>;
  note: string;
  trend: RetirementHoldingTrend;
}

export interface RetirementGainLossSnapshot {
  syncDate: string;
  retrievedAt: string;
  capturedAt: string;
}

export interface RetirementGainLossSnapshotResult extends RetirementGainLossSnapshot {
  replaced: boolean;
  snapshotCount: number;
}

export interface RetirementGainLossSnapshotResponse {
  snapshot: RetirementGainLossSnapshotResult;
  portfolio: RetirementPortfolioData;
}

export interface RetirementGroupSummary {
  initialValue: number;
  currentValue: number;
  pctOfPortfolio: number;
  gainLossPct: number;
  holdingCount: number;
}

export interface RetirementPortfolioData {
  holdings: RetirementHolding[];
  totalInitial: number;
  totalCurrent: number;
  totalGainLoss: number;
  totalGainLossPct: number;
  byCategory: Record<string, RetirementGroupSummary>;
  byIndustry: Record<string, RetirementGroupSummary>;
  byAccountType: Record<string, RetirementGroupSummary>;
  topPositions: RetirementHolding[];
  /** Newest-first metadata for the three retained snapshot columns. */
  gainLossSnapshots: RetirementGainLossSnapshot[];
  /** Broker observation time of the SnapTrade ledger backing this view. */
  retrievedAt?: string;
  source?: string;
}

/** Outcome of replacing the local holdings ledger with a Fidelity snapshot. */
export interface RetirementHoldingsSyncReport {
  totalValue: number;
  sync: {
    accounts_synced: number;
    positions_synced: number;
    added: number;
    changed: number;
    unchanged: number;
    removed: number;
    groups_reactivated: number;
  };
}
