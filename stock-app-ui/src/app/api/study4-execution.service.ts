import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from './api-base';
import {
  Study4Audit, Study4CycleDetail, Study4Performance, Study4Status
} from '../model/study4-execution';

@Injectable({ providedIn: 'root' })
export class Study4ExecutionService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(API_BASE_URL)}/api/execution/study4`;

  status(): Observable<Study4Status> {
    return this.http.get<Study4Status>(`${this.base}/status`);
  }

  cycle(week: string): Observable<Study4CycleDetail> {
    return this.http.get<Study4CycleDetail>(`${this.base}/cycles/${encodeURIComponent(week)}`);
  }

  scan(session?: string): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/scan`, { session: session ?? null });
  }

  finalize(session?: string): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/finalize`, { session: session ?? null });
  }

  preflight(): Observable<{ confirmationChallenge: string; planHash: string; dryRuns: unknown[] }> {
    return this.http.post<{ confirmationChallenge: string; planHash: string; dryRuns: unknown[] }>(
      `${this.base}/cycles/current/preflight`, {});
  }

  confirm(token: string, productionAck?: string): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/confirm`, { token, productionAck });
  }

  execute(planId?: number): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/execute`, { planId: planId ?? null });
  }

  reconcile(): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/reconcile`, {});
  }

  finalSync(): Observable<unknown> {
    return this.http.post(`${this.base}/cycles/current/final-sync`, {});
  }

  killSwitch(active: boolean): Observable<unknown> {
    return this.http.post(`${this.base}/kill-switch`, { active });
  }

  capitalReset(targetBucket: number, activeBucket?: number): Observable<unknown> {
    return this.http.post(`${this.base}/capital-resets`, {
      targetBucket,
      activeBucket: activeBucket ?? null
    });
  }

  resetLedger(confirmation: string): Observable<unknown> {
    return this.http.post(`${this.base}/reset`, { confirmation });
  }

  performance(): Observable<Study4Performance> {
    return this.http.get<Study4Performance>(`${this.base}/performance`);
  }

  audit(): Observable<Study4Audit> {
    return this.http.get<Study4Audit>(`${this.base}/audit`);
  }
}
