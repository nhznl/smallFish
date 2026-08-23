import { StockType } from './stock';

// Wheel candidate types for the Phase 1 options-wheel scan
// (`models/wheel.py` — `WHEEL_COLUMNS` / `report_columns()`).
//
// SOURCE OF TRUTH for column names/order: the Python `WHEEL_COLUMNS` constant in
// `models/wheel.py` (`report_columns()`) and FastAPI's wheel reader/router.
// These interfaces MUST stay in sync with the report CSV, backend JSON, table,
// and explainer.
//
// Units (per wheel.py): RV fields are DAILY sigma; every
// `*Pct` / `*ExpiryItm*` / `*Touch*` frequency column is a fraction (0.05 = 5%),
// displayed as a percentage; dollar columns are dollars. `minCushion20pctItm` is
// a display STRING ("5%" or ">10%"). `earningsWindowState` is a string enum.
//
// Nullability: `scoreTotal`, `signalBand`, `sector` are "swing scan context" --
// a null/absent value means NOT EVALUATED, never a failure or zero. Render "-".

/** One row of the wheel report: one symbol x one horizon (long format). */
export interface WheelReportRow {
  // identity / horizon
  schemaVersion: number;
  runMode: string;
  symbol: string;
  asOf: string;
  horizonDte: number;
  horizonSessions: number;
  priceAsOf: string;

  // per-symbol context (repeated on every horizon row for the symbol)
  lastClose?: number;
  range5dHigh?: number;
  range5dLow?: number;
  range5dWidthPct?: number;
  range5dClosePos?: number;
  range31dHigh?: number;
  range31dLow?: number;
  range31dWidthPct?: number;
  range31dClosePos?: number;
  rv7Cc?: number;
  rv7Park?: number;
  rv7Used?: number;
  rv21Cc?: number;
  rv21Park?: number;
  rv21Used?: number;
  rv37Cc?: number;
  rv37Park?: number;
  rv37Used?: number;
  rvPercentile252?: number;   // nullable
  atr14Pct?: number;
  avgDollarVolume20?: number;
  swingLow20?: number;
  distSma50Pct?: number;
  bbLower?: number;
  daysToEvent?: number;       // nullable
  scoreTotal?: number;        // swing scan context -- nullable
  signalBand?: string;        // swing scan context -- nullable
  sector?: string;            // swing scan context -- nullable
  eventsFetchedAsOf?: string; // nullable
  dataQuality?: 'OK' | 'STALE' | 'UNKNOWN' | 'INVALID';
  qualityReasons?: string;
  expectedPriceAsOf?: string;
  priceAgeSessions?: number;
  historyStart?: string;

  // per-horizon metrics
  rvWindowSessions: number;
  rvUsedDaily?: number;
  sigmaMoveDollars?: number;
  sigmaMovePct?: number;

  // cushion grid 2.5% / 5% / 7.5% / 10% (put/call expiry-ITM + touch)
  putExpiryItm25?: number;
  callExpiryItm25?: number;
  putTouch25?: number;
  callTouch25?: number;
  putExpiryItmNonoverlap25?: number;
  callExpiryItmNonoverlap25?: number;
  putTouchNonoverlap25?: number;
  callTouchNonoverlap25?: number;
  putExpiryItm5?: number;
  callExpiryItm5?: number;
  putTouch5?: number;
  callTouch5?: number;
  putExpiryItmNonoverlap5?: number;
  callExpiryItmNonoverlap5?: number;
  putTouchNonoverlap5?: number;
  callTouchNonoverlap5?: number;
  putExpiryItm75?: number;
  callExpiryItm75?: number;
  putTouch75?: number;
  callTouch75?: number;
  putExpiryItmNonoverlap75?: number;
  callExpiryItmNonoverlap75?: number;
  putTouchNonoverlap75?: number;
  callTouchNonoverlap75?: number;
  putExpiryItm10?: number;
  callExpiryItm10?: number;
  putTouch10?: number;
  callTouch10?: number;
  putExpiryItmNonoverlap10?: number;
  callExpiryItmNonoverlap10?: number;
  putTouchNonoverlap10?: number;
  callTouchNonoverlap10?: number;

  minCushion20pctItm?: string; // "5%" or ">10%"
  sampleCount: number;
  nonoverlapSampleCount?: number;
  worstMinClosePct?: number;
  p10MinClosePct?: number;
  earningsWindowState?: string; // KNOWN_EVENT | NO_EVENT_IN_FETCHED_RANGE | UNKNOWN_STALE
}

/**
 * A wheel report row joined to cached trend/structure data for the same symbol.
 * Symbols without sufficient cached history have `trendAvailable === false` and
 * Symbols without enough cached history have `trendAvailable === false` and a
 * null direction -- render "-", not an error.
 *
 * The section 5 hide predicate keys on `trendDirection`: hide a row ONLY when
 * `trendDirection === 'BEARISH'`. A null/absent trendDirection is NEVER hidden.
 */
export interface WheelCandidate {
  wheel: WheelReportRow;
  type: StockType;
  trendAvailable: boolean;
  trendDirection?: string;   // BULLISH | BEARISH | SIDEWAYS | NEUTRAL, or null
}

/** The exact dated inputs used to calculate a Wheel row's RV percentile. */
export interface RvPercentileDetail {
  rv_window_sessions: number;
  lookback_sessions: number;
  current_rv: number;
  percentile: number;
  price_as_of: string;
  observations: Array<{ date: string; rv: number }>;
}
