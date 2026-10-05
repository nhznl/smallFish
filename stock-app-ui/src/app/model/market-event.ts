/** Published CPI event-risk scan. Values describe exposure, not a price forecast. */

export interface StrategyEventAssessment {
  strategyId: string;
  assessment: 'avoid' | 'high_risk' | 'mixed_opportunity' | 'conditional' | 'low_direct_relevance' | string;
  timingRelationship: string;
  potentialBenefits: string[];
  potentialRisks: string[];
  ruleIds: string[];
  policyVersion: string;
}

export interface EventMeasurement {
  metric: string;
  label: string;
  value: string | null;
  numericValue: string | null;
  unit: string | null;
  scale: string | null;
  seasonalAdjustment: string | null;
  valueKind: 'previous' | 'revised_previous' | 'consensus' | 'forecast' | 'actual' | string;
  provider: string | null;
  observedAtUtc: string | null;
}

export interface EventEtfExposure {
  symbol: string;
  relationship: string;
  relevance: string;
  impactChannel: string;
  priceDataState: 'current' | 'stale' | 'missing' | string;
  latestPriceSession: string | null;
}

export interface MarketEvent {
  id: string;
  canonicalKey: string;
  title: string;
  eventType: string;
  category: string;
  referencePeriod: string | null;
  referencePeriodLabel: string | null;
  scheduledAtUtc: string | null;
  easternTime: string | null;
  pacificTime: string | null;
  easternDate: string | null;
  pacificDate: string | null;
  civilDate: string | null;
  timePrecision: string;
  scheduleStatus: string;
  lifecycleStatus: string;
  reviewRequired: boolean;
  reviewReason: string | null;
  portfolioRelevance: string;
  priority: 'primary' | 'secondary' | string;
  importance: { score: number; label: string; ruleIds: string[]; policyVersion: string };
  affectedAssetClasses: string[];
  affectedInstruments: string[];
  etfExposures: EventEtfExposure[];
  measurements: EventMeasurement[];
  strategyAssessments: StrategyEventAssessment[];
  sources: Array<{
    provider: string;
    sourceRecordId: string;
    sourceUrl: string;
    fetchedAtUtc: string;
    parserVersion: string;
  }>;
  relatedEventIds: string[];
}

export interface RiskDay {
  date: string;
  sessionState: 'regular' | 'early_close' | 'closed' | string;
  riskLabel: 'EXTREME' | 'HIGH' | 'MODERATE' | 'ROUTINE' | string;
  eventCount: number;
  clusterWarning: string | null;
  sessionAssessment: string;
  coverage: 'covered' | 'covered_empty' | 'stale' | 'unknown' | string;
  primaryEvents: MarketEvent[];
  secondaryEvents: MarketEvent[];
}

export interface CalendarSource {
  provider: string;
  status: string;
  required: boolean;
  configured: boolean;
  scope: string;
  detail: string;
  coverageStart: string | null;
  coverageEnd: string | null;
  lastAttemptUtc: string | null;
  lastSuccessUtc: string | null;
  resultCount: number | null;
  errorCategory: string | null;
  parserVersion: string;
}

export interface MarketEventSummary {
  horizonDays: number | null;
  primaryRiskDays: number | null;
  highOrExtremeEvents: number | null;
  secondaryEvents: number | null;
}

export interface MarketEventCollection {
  available: boolean;
  schemaVersion: number;
  generatedAtUtc: string | null;
  asOfDate: string | null;
  horizon: { from: string; to: string } | null;
  timezone: string;
  coverageStatus: string;
  caveat: string;
  sources: CalendarSource[];
  summary: MarketEventSummary;
  days: RiskDay[];
  scannedAtUtc: string;
}
