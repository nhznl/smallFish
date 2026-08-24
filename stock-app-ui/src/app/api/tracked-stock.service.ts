import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  SymbolLookupResponse,
  TrackedStockListResponse
} from '../model/tracked-stock';
import type { TrackedStockCategory } from '../model/tracked-stock';
import { API_BASE_URL } from './api-base';

@Injectable({ providedIn: 'root' })
export class TrackedStockService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${inject(API_BASE_URL)}/tracked-stocks`;

  list(): Observable<TrackedStockListResponse> {
    return this.http.get<TrackedStockListResponse>(this.baseUrl);
  }

  lookupSymbols(symbols: string): Observable<SymbolLookupResponse> {
    return this.http.get<SymbolLookupResponse>(`${this.baseUrl}/symbols`, {
      params: { symbols }
    });
  }

  add(body: {
    symbols: string[];
    category?: TrackedStockCategory;
    coverage_initiation_date?: string;
    notes?: string;
    target_date?: string | null;
    target_amount?: number | null;
  }): Observable<TrackedStockListResponse> {
    return this.http.post<TrackedStockListResponse>(this.baseUrl, body);
  }

  update(symbol: string, body: {
    category?: TrackedStockCategory;
    coverage_initiation_date?: string;
    notes?: string;
    target_date?: string | null;
    target_amount?: number | null;
  }): Observable<TrackedStockListResponse> {
    return this.http.put<TrackedStockListResponse>(
      `${this.baseUrl}/${encodeURIComponent(symbol)}`, body
    );
  }

  remove(symbol: string): Observable<TrackedStockListResponse> {
    return this.http.delete<TrackedStockListResponse>(
      `${this.baseUrl}/${encodeURIComponent(symbol)}`
    );
  }

  captureCoverageVsSpySnapshot(): Observable<TrackedStockListResponse> {
    return this.http.post<TrackedStockListResponse>(
      `${this.baseUrl}/coverage-vs-spy-snapshots`, {}
    );
  }
}
