/** Job command responses from `/run*` endpoints (Phase 18). Wire shapes unchanged. */

export type JobStatus = 'ok' | 'error' | 'timeout';

export interface JobCommandResult {
  status: JobStatus;
  exitCode?: number;
  durationMs?: number;
  output?: string;
  message?: string;
}

/** Nested prerequisite result on wheel / earnings-dependent jobs. */
export interface EarningsRefreshSummary {
  status: JobStatus;
  exitCode?: number;
  durationMs?: number;
  output?: string;
  message?: string;
}

export interface EarningsScanResult extends JobCommandResult {
  symbolsWithUpcomingEarnings?: number;
  scannerSymbols?: number;
  eventsFetchedAsOf?: string | null;
  eventsCoverageEnd?: string | null;
}

export interface WheelJobResult extends JobCommandResult {
  earningsRefresh?: EarningsRefreshSummary;
  warning?: string;
}

export type SectorRotationJobResult = JobCommandResult;

/** `runChains` may surface a 400 scope message in the JSON body. */
export type ChainsJobResult = JobCommandResult;
