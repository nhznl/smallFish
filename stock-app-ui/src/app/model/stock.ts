import { Daily } from "./daily";
import { GainLossFromDate } from "./gainLossFromDate";

export type StockType = 'STOCK' | 'ETF' | 'MF';

export interface MomentumTrend {
  direction: string;
  strength: string;
  rsi: number;
  currentTrendDays: number;
}

export type MomentumSetup =
  | 'BULLISH_CONTINUATION'
  | 'BEARISH_CONTINUATION'
  | 'BULLISH_REVERSAL'
  | 'BEARISH_REVERSAL'
  | 'WATCH'
  | 'NOT_EVALUATED';

export interface YearlySlope {
  year: number;
  weeklySlopes: Record<string, number>;
}

export interface StrategyReport {
  date: string;
  smaTwenty: number;
  smaFifty: number;
  rsi14: number;
  macd: number;
  macdSignal: number;
  macdHist: number;
  bbMid: number;
  bbUpper: number;
  bbLower: number;
  avgVol20: number;
  avgDollarVol20: number;
  volSpike: boolean;
  atr14: number;
  atrPct: number;
  eventDate?: string;
  eventType?: string;
  daysToEvent?: number;
  sector?: string;
  relStrengthSpy?: number;
  daysInBand: number;
  marketRegime?: string;
  regimeSizeFactor?: number;
  higherLow: boolean;
  scoreTrend: number;
  scoreMomentum: number;
  scoreExtension: number;
  scoreEvent: number;
  scoreTradability: number;
  scorePersistence: number;
  scoreTotal: number;
  scorePct: number;
  scoreShiftRaw: number;
  scoreShift: number;
  daysSinceMacdCross?: number;
  shiftLabel?: string;
  signalBand: string;
  bucket: string;
  reasonSummary: string;
}

export interface MomentumStock {
  code: string;
  type: StockType;
  lastTradeStats: { tradeDate: string; close: number } | null;
  /** Rolling 252-session high/low from the same cached bars as lastTradeStats. */
  fiftyTwoWeekLow?: number | null;
  fiftyTwoWeekHigh?: number | null;
  /** Latest cached close as a 0–100 position between the cached 52-week low/high. */
  fiftyTwoWeekPosition?: number | null;
  recentWeeks: Array<{
    endDate: string | null;
    avgClose: number;
    avgChange: number;
    avgVolume: number;
    /** Sessions behind the week's statistics; fewer than 5 means a short week. */
    sessionCount: number;
    relativeMomentum: number;
    relativeMomentumStd: number;
  }>;
  yearToDate: GainLossFromDate | null;
  midPointToDate: GainLossFromDate | null;
  fiveWeeksToDate: GainLossFromDate | null;
  fiveDaysToDate: GainLossFromDate | null;
  atrPct?: number | null;
  realizedVolatilityExpansion?: number | null;
  volumeRatio?: number | null;
  averageDollarVolume20?: number | null;
  distanceSma20Pct?: number | null;
  rsiChangeFiveDay?: number | null;
  macdHistogramChange?: number | null;
  daysSinceMacdCross?: number | null;
  relativeStrengthSpyOneMonth?: number | null;
  freshnessStatus?: 'FRESH' | 'STALE' | 'DATE_MISMATCH' | 'INCOMPLETE' | 'UNKNOWN';
  evidenceQuality?: 'COMPLETE' | 'PARTIAL' | 'STALE_OR_INCOMPLETE';
  setup?: MomentumSetup;
  setupScore?: number;
  setupScoreVersion?: string;
  setupScoreComponents?: Record<string, number>;
  setupReason?: string;
  triggerLabel?: string;
  /** Next cached Finnhub earnings date; null when none is known for the symbol. */
  nextEarningsDate?: string | null;
  /** Calendar days from today to that report; 0 means it reports today. */
  daysToEarnings?: number | null;
  preliminaryReversal?: boolean;
  preliminaryReversalLabel?: string | null;
  advancedTrendWithVolume?: MomentumTrend | null;
}

export interface StockAnalysisWeek {
  startDate: string | null;
  endDate: string | null;
  avgClose: number;
}

/** One cached daily close. Daily bars only — the cache holds no intraday data. */
export interface StockDailyBar {
  tradeDate: string;
  close: number;
  volume: number;
}

export interface StockAnalysis {
  code: string;
  type: StockType;
  lastTradeStats: Daily | null;
  recentWeeks: StockAnalysisWeek[];
  dailyBars?: StockDailyBar[];
  yearToDate: GainLossFromDate | null;
  midPointToDate: GainLossFromDate | null;
  fiveWeeksToDate: GainLossFromDate | null;
  fiveDaysToDate: GainLossFromDate | null;
  yearlySlopes: Record<string, YearlySlope>;
  atrPct?: number | null;
  volumeRatio?: number | null;
  relativeStrengthSpyOneMonth?: number | null;
  setup?: MomentumSetup;
  setupScore?: number;
  trendRsi?: number | null;
}

export interface StrategyStock {
  code: string;
  type: StockType;
  lastTradeStats: { close: number } | null;
  fiftyTwoWeekLow?: number | null;
  fiftyTwoWeekHigh?: number | null;
  fiftyTwoWeekPosition?: number | null;
  signal?: string;
  strategyReport?: StrategyReport;
}
