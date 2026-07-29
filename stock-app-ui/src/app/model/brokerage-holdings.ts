import { BrokerageLedgerPortfolioSlug } from './brokerage-ledger';

export interface BrokerageHoldingTrend {
  alert: boolean;
  peakPct: number | null;
  peakAt: string;
  dropPct: number | null;
  fromPct: number | null;
  toPct: number | null;
  alertAt: string | null;
  direction: 'GAIN' | 'LOSS';
}

export interface BrokerageHolding {
  enrichmentSymbol: string;
  category: string;
  accountType: string;
  industry: string;
  symbol: string;
  costPrice: number | null;
  qty: number;
  initialInvestment: number | null;
  marketPrice: number | null;
  currentValue: number | null;
  pctOfTotal: number | null;
  gainLossPct: number | null;
  gainLoss: number | null;
  gainLossSnapshots: Record<string, number>;
  note: string;
  trend: BrokerageHoldingTrend;
}

export interface BrokerageHoldingGroupSummary {
  initialValue: number | null;
  currentValue: number;
  pctOfPortfolio: number | null;
  gainLossPct: number | null;
  holdingCount: number;
}

export interface BrokerageGainLossSnapshot {
  syncDate: string;
  retrievedAt: string;
  capturedAt: string;
}

export interface BrokerageHoldingsSnapshot {
  holdings: BrokerageHolding[];
  totalInitial: number | null;
  totalCurrent: number | null;
  totalGainLoss: number | null;
  totalGainLossPct: number | null;
  byCategory: Record<string, BrokerageHoldingGroupSummary>;
  byIndustry: Record<string, BrokerageHoldingGroupSummary>;
  byAccountType: Record<string, BrokerageHoldingGroupSummary>;
  topPositions: BrokerageHolding[];
  gainLossSnapshots: BrokerageGainLossSnapshot[];
  retrievedAt?: string;
  source?: string;
}

export interface BrokerageGainLossSnapshotResponse {
  snapshot: BrokerageGainLossSnapshot & { replaced: boolean; snapshotCount: number };
  portfolio: BrokerageHoldingsSnapshot;
}

export interface BrokerageHoldingContext {
  portfolio: BrokerageLedgerPortfolioSlug;
}
