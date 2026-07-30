import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AdjustedBasisResponse,
  ArchiveCreatedResponse,
  BrokerageId,
  BrokerageSyncResponse,
  GainLossSnapshotResponse,
  HoldingsMetadataResponse,
  HoldingsResponse,
  LedgerEventsResponse,
  OptionsResponse,
  SymbolLedgerDetailResponse,
  SymbolLedgerListResponse,
} from '../model/brokerage';

/**
 * The one client for every brokerage resource.
 *
 * `brokerageId` only ever selects a URL. It never selects a response type, a
 * parser, or a code path — that is what makes adding an institution a backend
 * registry entry rather than an Angular change.
 */
@Injectable({ providedIn: 'root' })
export class BrokerageService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = window.location.port === '4200'
    ? 'http://localhost:8000'
    : window.location.origin;

  private base(brokerageId: BrokerageId): string {
    return `${this.apiBaseUrl}/api/brokerages/${encodeURIComponent(brokerageId)}`;
  }

  getHoldings(brokerageId: BrokerageId, accountId?: string): Observable<HoldingsResponse> {
    return this.http.get<HoldingsResponse>(`${this.base(brokerageId)}/holdings`, {
      params: this.params({ account_id: accountId }),
    });
  }

  /** Edit one holding's classification. Broker facts stay immutable. */
  updateHoldingsMetadata(
    brokerageId: BrokerageId,
    symbol: string,
    body: { category?: string; industry?: string; note?: string }
  ): Observable<HoldingsMetadataResponse> {
    return this.http.patch<HoldingsMetadataResponse>(
      `${this.base(brokerageId)}/holdings/${encodeURIComponent(symbol)}/metadata`,
      body
    );
  }

  /** Record every held position's gain/loss percentage under its sync date. */
  captureGainLossSnapshot(
    brokerageId: BrokerageId
  ): Observable<GainLossSnapshotResponse> {
    return this.http.post<GainLossSnapshotResponse>(
      `${this.base(brokerageId)}/holdings/gain-loss-snapshots`, {}
    );
  }

  getOptions(
    brokerageId: BrokerageId,
    options: { state?: 'open' | 'flat' | 'all'; accountId?: string } = {}
  ): Observable<OptionsResponse> {
    return this.http.get<OptionsResponse>(`${this.base(brokerageId)}/options`, {
      params: this.params({ state: options.state, account_id: options.accountId }),
    });
  }

  getOptionAdjustedBasis(
    brokerageId: BrokerageId, accountId?: string
  ): Observable<AdjustedBasisResponse> {
    return this.http.get<AdjustedBasisResponse>(
      `${this.base(brokerageId)}/option-adjusted-basis`,
      { params: this.params({ account_id: accountId }) }
    );
  }

  // ------------------------------------------------------- symbol ledger ---

  listSymbols(
    brokerageId: BrokerageId,
    options: {
      state?: 'active' | 'closed' | 'all';
      exposure?: 'options' | 'all';
      accountId?: string;
    } = {}
  ): Observable<SymbolLedgerListResponse> {
    return this.http.get<SymbolLedgerListResponse>(`${this.base(brokerageId)}/symbols`, {
      params: this.params({
        state: options.state, exposure: options.exposure, account_id: options.accountId,
      }),
    });
  }

  getSymbol(brokerageId: BrokerageId, symbol: string): Observable<SymbolLedgerDetailResponse> {
    return this.http.get<SymbolLedgerDetailResponse>(this.symbolUrl(brokerageId, symbol));
  }

  updateSymbolNotes(
    brokerageId: BrokerageId, symbol: string, notes: string
  ): Observable<SymbolLedgerDetailResponse> {
    return this.http.patch<SymbolLedgerDetailResponse>(
      this.symbolUrl(brokerageId, symbol), { notes }
    );
  }

  getSymbolEvents(
    brokerageId: BrokerageId,
    symbol: string,
    options: { period?: string; cursor?: string | null; limit?: number } = {}
  ): Observable<LedgerEventsResponse> {
    return this.http.get<LedgerEventsResponse>(
      `${this.symbolUrl(brokerageId, symbol)}/events`,
      {
        params: this.params({
          period: options.period,
          cursor: options.cursor ?? undefined,
          limit: options.limit,
        }),
      }
    );
  }

  createArchive(
    brokerageId: BrokerageId,
    symbol: string,
    body: { request_id: string; expected_period_version: string; note?: string }
  ): Observable<ArchiveCreatedResponse> {
    return this.http.post<ArchiveCreatedResponse>(
      `${this.symbolUrl(brokerageId, symbol)}/archives`, body
    );
  }

  runSync(brokerageId: BrokerageId): Observable<BrokerageSyncResponse> {
    return this.http.post<BrokerageSyncResponse>(`${this.base(brokerageId)}/sync`, {});
  }

  private symbolUrl(brokerageId: BrokerageId, symbol: string): string {
    return `${this.base(brokerageId)}/symbols/${encodeURIComponent(symbol)}`;
  }

  /** Omit an unset parameter entirely rather than sending an empty value. */
  private params(values: Record<string, string | number | undefined>): HttpParams {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined && value !== '') params = params.set(key, String(value));
    }
    return params;
  }
}
