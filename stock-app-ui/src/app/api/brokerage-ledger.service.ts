import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  BrokerageLedgerPortfolioSlug,
} from '../model/brokerage-ledger';
import {
  BrokerageGainLossSnapshotResponse,
  BrokerageHoldingsSnapshot,
} from '../model/brokerage-holdings';

@Injectable({ providedIn: 'root' })
export class BrokerageLedgerService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = window.location.port === '4200'
    ? 'http://localhost:8000'
    : window.location.origin;

  getHoldings(portfolio: BrokerageLedgerPortfolioSlug): Observable<BrokerageHoldingsSnapshot> {
    return this.http.get<BrokerageHoldingsSnapshot>(
      `${this.apiBaseUrl}/brokerage-ledgers/${portfolio}/holdings`
    );
  }

  updateHoldingEnrichment(
    portfolio: BrokerageLedgerPortfolioSlug,
    symbol: string,
    body: { category?: string; industry?: string; note?: string }
  ): Observable<Record<string, string>> {
    return this.http.put<Record<string, string>>(
      `${this.apiBaseUrl}/brokerage-ledgers/${portfolio}/holdings/${encodeURIComponent(symbol)}/enrichment`,
      body
    );
  }

  captureHoldingSnapshot(
    portfolio: BrokerageLedgerPortfolioSlug
  ): Observable<BrokerageGainLossSnapshotResponse> {
    return this.http.post<BrokerageGainLossSnapshotResponse>(
      `${this.apiBaseUrl}/brokerage-ledgers/${portfolio}/holdings/gain-loss-snapshots`, {}
    );
  }
}
