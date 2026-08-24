export interface OptionQuoteRow {
  contractId: string | null;
  providerContractSymbol: string | null;
  symbol: string | null;
  contractQuality: string | null;
  asOf: string | null;
  requestedDte: number | null;
  expiry: string | null;
  actualDte: number | null;
  dteDeviation: number | null;
  side: string | null;
  strike: number | null;
  moneyness: string | null;
  analysisView: string | null;
  strategyRole: string | null;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  openInterest: number | null;
  volume: number | null;
  spreadPct: number | null;
  quoteSource: string | null;
  quoteProviderStatus: string | null;
  bidTimestamp: string | null;
  askTimestamp: string | null;
  quoteEventTimestamp: string | null;
  retrievedAt: string | null;
  marketSession: string | null;
  quoteAgeSeconds: number | null;
  quoteQuality: string | null;
  quoteQualityReasons: string | null;
  liquidityOk: boolean | null;
  gateReason: string | null;
  entryEligible: boolean | null;
  entryReason: string | null;
}

export interface OptionQuoteSummary {
  contracts: number;
  symbols: number;
  entryViewContracts: number;
  rollExitViewContracts: number;
  entryEligibleContracts: number;
  quoteQualityCounts: Record<string, number>;
  quoteSourceCounts: Record<string, number>;
  providerStatusCounts: Record<string, number>;
}

/** The Wheel view's explicit collection scope. It can only narrow the configured policy. */
export interface CollectionScopeRequest {
  horizonDte?: number;
  symbols?: string[];
  minOtmPct?: number;
}

/** The scope a run actually applied, as recorded in its immutable manifest. */
export interface CollectionScope {
  scoped: boolean;
  configuredDtes?: number[];
  requestedDtes?: number[];
  symbols?: string[] | null;
  symbolCount?: number | null;
  minOtmPct?: number | null;
  minOtmAppliesTo?: string | null;
  limit?: number | null;
  symbolsNotInPool?: string[];
  symbolsWithoutEntryStrikes?: string[];
}

export interface OptionQuoteSnapshot {
  available: boolean;
  reason?: string;
  runId?: string;
  schemaName?: string;
  schemaVersion?: number;
  asOf?: string;
  generatedAtUtc?: string;
  quoteProvider?: Record<string, unknown>;
  /** Absent on archives written before scoped collection existed. */
  collectionScope?: CollectionScope | null;
  summary?: OptionQuoteSummary;
  rows?: OptionQuoteRow[];
}
