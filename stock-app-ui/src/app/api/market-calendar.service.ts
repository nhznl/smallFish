import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from './api-base';
import { MarketEventCollection } from '../model/market-event';

@Injectable({ providedIn: 'root' })
export class MarketCalendarService {
  private readonly http = inject(HttpClient);
  private readonly url = `${inject(API_BASE_URL)}/api/market-events`;

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
}
