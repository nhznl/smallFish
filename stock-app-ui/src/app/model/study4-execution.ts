export interface Study4Status {
  configured: boolean;
  environment: 'sandbox' | 'production' | null;
  productionEnabled: boolean;
  killSwitch: boolean;
  accountAlias: string;
  accountFingerprint: string | null;
  protocolId: string | null;
  operationalId: string;
  selectedArm: 'stocks-spy-control' | 'stocks-spxl-spy-control' | null;
  strategyVersionId: number | null;
  availableArms: Array<'stocks-spy-control' | 'stocks-spxl-spy-control'>;
  canChangeArm: boolean;
  exploratoryWarning: string;
  setupRequirements: Study4SetupRequirement[];
  capital: Study4Capital | null;
  configuredTargetBucket: number | null;
  recommendedPilotCap: number | null;
  cycle: Study4Cycle | null;
  nextAction: string;
  submissionsAllowed: boolean;
}

export interface Study4SetupRequirement {
  id: string;
  label: string;
  ready: boolean;
  action: string;
}

export interface Study4Capital {
  id: number;
  target_bucket: number;
  active_bucket: number;
  production_cap: number | null;
  cash_flow_status: string;
}

export interface Study4Cycle {
  id: number;
  iso_week: string;
  cutoff_session: string | null;
  execution_session: string | null;
  state: string;
  environment: string;
  plan_hash: string | null;
  strategy_version_id?: number | null;
}

export interface Study4ScanRow {
  ticker: string;
  state: string;
  setup_score?: number | null;
  decision_close?: number | null;
  sector?: string | null;
  predicted_event_date?: string | null;
  provisional_shares?: number | null;
  shares?: number | null;
  limit_price?: number | null;
  rank?: number | null;
  rejection_reasons?: string | null;
}

export interface Study4PlanItem {
  priority: number;
  kind: string;
  symbol: string;
  side: string;
  currentShares?: number | null;
  targetShares?: number | null;
  deltaShares?: number | null;
  limitPrice?: number | null;
  reservationCash?: number | null;
  reason?: string | null;
  rank?: number | null;
  decisionClose?: number | null;
  setupScore?: number | null;
  sector?: string | null;
  predictedEventDate?: string | null;
  entryDecisionDate?: string | null;
  entryExecutionDate?: string | null;
  allowedDrawdown?: number | null;
  timeInForce?: string | null;
  formula?: string | null;
}

export interface Study4CycleDetail {
  cycle: Study4Cycle | null;
  snapshots: Array<{ session: string; payload: { scanRows?: Study4ScanRow[]; positionDecisions?: unknown[]; planItems?: Study4PlanItem[] } }>;
  plan: { plan_hash: string; payload: { planItems?: Study4PlanItem[] }; items: Array<{ payload: Study4PlanItem }> } | null;
  intents: Array<{ external_identifier: string; state: string; broker_order_id: string | null }>;
  reconciliations: Array<{ ok: number; discrepancy_codes: string; kind: string }>;
}

export interface Study4Performance {
  series: Array<{
    session: string;
    strategyEquity: number | null;
    benchmarkEquity: number | null;
    dailyReturn: number | null;
    drawdown: number | null;
    complete: boolean;
    strategyArm?: string | null;
    strategyVersionId?: number | null;
    startingEquity?: number | null;
    stockEquity?: number | null;
    stagingEquity?: number | null;
    cashAvailable?: number | null;
  }>;
  unavailableDates: string[];
  modeledCostNote: string;
}

export interface Study4Audit {
  events: Array<{
    id: number;
    created_at: string;
    actor: string;
    action: string;
    result: string;
    diagnostics: string;
  }>;
}
