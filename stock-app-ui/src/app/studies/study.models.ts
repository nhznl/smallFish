/** Mirrors the versioned materialized JSON contract in models/study.py. */

import { StrategyStock } from '../model/stock';

export type StudyVerdict = 'PASSED' | 'FAILED' | 'INCONCLUSIVE' | 'NO_VERDICT' | 'NOT_RUN';
export type StudyEvidenceLevel = 'CONFIRMATORY' | 'EXPLORATORY' | 'DESCRIPTIVE' | 'PLANNED';
export type StudyStatisticFormat = 'NUMBER' | 'INTEGER' | 'PERCENT' | 'CURRENCY' | 'RATIO' | 'TEXT';
export type StudyStatisticPriority = 'PRIMARY' | 'SECONDARY';

export interface StudyCatalog {
  schemaName: 'smallfish.research-study';
  schemaVersion: 1;
  studies: StudyCatalogItem[];
}

export interface StudyCatalogItem {
  id: string;
  name: string;
  summary: string;
  defaultVariationId: string;
  variationCount: number;
  verdict: StudyVerdict;
  evidenceLevel: StudyEvidenceLevel;
  scanAvailable: boolean;
  updatedAt: string;
}

export interface StudyDetail {
  schemaName: 'smallfish.research-study';
  schemaVersion: 1;
  id: string;
  name: string;
  summary: string;
  updatedAt: string;
  defaultVariationId: string;
  variations: StudyVariation[];
}

export interface StudyVariation {
  id: string;
  name: string;
  thesis: string;
  methodology: StudyMethodology;
  outcome: StudyOutcome;
  stats: StudyStatistic[];
  scan: StudyScan | null;
  provenance: StudyProvenance;
  caveats: string[];
}

export interface StudyMethodology {
  summary: string;
  population: string;
  inclusionCriteria: string[];
  exclusionCriteria: string[];
  features: string[];
  endpoint: string;
  controls: string[];
  period: string;
  inference: string;
  limitations: string[];
}

export interface StudyOutcome {
  verdict: StudyVerdict;
  evidenceLevel: StudyEvidenceLevel;
  summary: string;
  whatWorked: string[];
  whatDidNotWork: string[];
  nextSteps: string[];
  moreData: { assessment: boolean; rationale: string };
}

export interface StudyStatistic {
  id: string;
  label: string;
  value: string | number | null;
  format: StudyStatisticFormat;
  precision: number;
  scope: string;
  confidenceInterval?: { level: number; low: number | null; high: number | null } | null;
  interpretation: string;
  priority: StudyStatisticPriority;
}

export interface StudyScan {
  executionSupported: boolean;
  scanType: string;
  resultSchema: string;
  eligibilityExplanation: string;
  warning: string;
  latestSnapshot: { path: string; generatedAt: string } | null;
}

export interface StudyProvenance {
  specificationPath: string;
  artifactPath: string;
  runId: string;
  sourceCommit: string | null;
  generatedAt: string;
  dataCutoff: string | null;
  verificationState: string;
}

/** Latest materialized candidate snapshot for scan-capable studies. */
export interface StudyScanSnapshot {
  schemaName: 'pre-earnings-candidates-v1';
  generatedAt: string;
  candidates: StrategyStock[];
}
