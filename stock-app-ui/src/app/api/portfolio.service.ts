import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  PortfolioDetailResponse,
  PortfolioListResponse,
  SymbolLookupResponse
} from '../model/portfolio';
import { API_BASE_URL } from './api-base';

/**
 * Portfolio tracking API.
 *
 * Errors deliberately propagate to the caller: a failed request must render as
 * an error banner, never as an empty portfolio list or a portfolio whose
 * returns silently disappeared.
 */
@Injectable({ providedIn: 'root' })
export class PortfolioService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${inject(API_BASE_URL)}/portfolios`;

  /** All portfolios with the list-table columns, SPY context, and cache date. */
  list(): Observable<PortfolioListResponse> {
    return this.http.get<PortfolioListResponse>(this.baseUrl);
  }

  captureInceptionVsSpySnapshot(): Observable<PortfolioListResponse> {
    return this.http.post<PortfolioListResponse>(
      `${this.baseUrl}/inception-vs-spy-snapshots`, {}
    );
  }

  /** One portfolio with its member rows. */
  detail(id: string): Observable<PortfolioDetailResponse> {
    return this.http.get<PortfolioDetailResponse>(`${this.baseUrl}/${encodeURIComponent(id)}`);
  }

  /** Distinct universe sectors, offered as create-modal suggestions. */
  sectors(): Observable<{ sectors: string[] }> {
    return this.http.get<{ sectors: string[] }>(`${this.baseUrl}/sectors`);
  }

  /** Validate free-form symbol text against the universe and price the hits. */
  lookupSymbols(symbols: string): Observable<SymbolLookupResponse> {
    return this.http.get<SymbolLookupResponse>(`${this.baseUrl}/symbols`, {
      params: { symbols }
    });
  }

  create(body: {
    name: string; description?: string; sector?: string; industry?: string; symbols: string[];
  }): Observable<PortfolioDetailResponse> {
    return this.http.post<PortfolioDetailResponse>(this.baseUrl, body);
  }

  /** Edit display metadata; creation date and members are unaffected. */
  update(id: string, body: {
    name?: string; description?: string; sector?: string; industry?: string;
  }): Observable<PortfolioDetailResponse> {
    return this.http.put<PortfolioDetailResponse>(
      `${this.baseUrl}/${encodeURIComponent(id)}`, body
    );
  }

  remove(id: string): Observable<{ deleted: string; name: string }> {
    return this.http.delete<{ deleted: string; name: string }>(
      `${this.baseUrl}/${encodeURIComponent(id)}`
    );
  }

  addSymbols(id: string, symbols: string[]): Observable<PortfolioDetailResponse> {
    return this.http.post<PortfolioDetailResponse>(
      `${this.baseUrl}/${encodeURIComponent(id)}/symbols`, { symbols }
    );
  }

  removeSymbol(id: string, symbol: string): Observable<PortfolioDetailResponse> {
    return this.http.delete<PortfolioDetailResponse>(
      `${this.baseUrl}/${encodeURIComponent(id)}/symbols/${encodeURIComponent(symbol)}`
    );
  }
}
