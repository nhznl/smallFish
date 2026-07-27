/**
 * Sector price-leadership snapshot. Every field is a relative-strength or
 * rotation measurement derived from adjusted price and volume — never a
 * measured fund flow.
 */
export interface SectorLeadershipRow {
  schemaVersion: number | null;
  asOf: string | null;
  symbol: string | null;
  sector: string | null;
  windowSessions: number | null;
  windowStart: string | null;
  windowEnd: string | null;
  totalReturn: number | null;
  benchmarkReturn: number | null;
  excessReturn: number | null;
  rank: number | null;
  rankOf: number | null;
  percentile: number | null;
  priorExcessReturn: number | null;
  priorRank: number | null;
  /** Positive means the sector moved toward rank 1. */
  rankChange: number | null;
  rsChange: number | null;
  leadershipState: string | null;
  rsTrend: string | null;
  volumeWindowAvg: number | null;
  volumeBaselineAvg: number | null;
  volumeRatio: number | null;
  /** Confirmation only — turnover, not net subscriptions. */
  volumeConfirms: boolean | null;
}

export interface SectorPairRow {
  numerator: string | null;
  denominator: string | null;
  windowSessions: number | null;
  ratioNow: number | null;
  ratioPrior: number | null;
  ratioChangePct: number | null;
  numeratorOutperforming: boolean | null;
}

export interface RotationEvidence {
  windowSessions: number;
  agrees: boolean;
  targetExcessReturn: number | null;
  targetRsChange: number | null;
  targetRank: number | null;
  targetRankChange: number | null;
  sourceExcessReturn: number | null;
  sourceRsChange: number | null;
  sourceRank: number | null;
  sourceRankChange: number | null;
  targetVolumeRatio: number | null;
  sourceVolumeRatio: number | null;
}

export interface RotationCandidate {
  source: string;
  sourceSector: string;
  target: string;
  targetSector: string;
  windowsConfirmed: number;
  windowsEvaluated: number;
  strength: number;
  evidence: RotationEvidence[];
}

export interface SectorExclusion {
  symbol: string;
  reason: string;
  detail?: string;
}

export interface SectorRotationSnapshot {
  available: boolean;
  reason?: string;
  asOf?: string;
  benchmark?: string;
  sessionEnd?: string;
  /** Newest session in the price cache; compared against sessionEnd. */
  cacheSessionEnd?: string | null;
  /** True when the cache holds sessions this snapshot did not use. */
  stale?: boolean;
  sessionsUsed?: number;
  sessionsRequired?: number;
  generatedAtUtc?: string;
  includedSymbols?: string[];
  exclusions?: SectorExclusion[];
  rotationCandidates?: RotationCandidate[];
  /** Carried verbatim from the archive; the UI must not overstate these. */
  measurementBasis?: string;
  notValidated?: string;
  windows?: number[];
  sectors?: SectorLeadershipRow[];
  pairs?: SectorPairRow[];
}
