import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from './api-base';
import { MarketEventCollection } from '../model/market-event';
import { MarketCalendarJobResult } from '../model/job-results';

@Injectable({ providedIn: 'root' })
export class MarketCalendarService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = inject(API_BASE_URL);
  private readonly url = `${this.apiBaseUrl}/api/market-events`;

  /** Read the published scan. This does not contact a provider. */
  getCollection(range?: { from?: string; to?: string }): Observable<MarketEventCollection> {
    let params = new HttpParams();
    if (range?.from) {
      params = params.set('from', range.from);
    }
    if (range?.to) {
      params = params.set('to', range.to);
    }
    return this.http.get<MarketEventCollection>(this.url, { params });
  }

  /** Generate today's calendar once; the command reuses an already completed run. */
  runMarketCalendar(): Observable<MarketCalendarJobResult> {
    return this.http.post<MarketCalendarJobResult>(
      `${this.apiBaseUrl}/runMarketCalendar`, null,
    );
  }
}
