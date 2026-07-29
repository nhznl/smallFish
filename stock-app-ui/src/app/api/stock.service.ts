import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of, throwError } from 'rxjs';
import { catchError, tap, shareReplay, finalize } from 'rxjs/operators';
import { StockInfo } from '../model/stock-info';
import {
  RetirementGainLossSnapshotResponse,
  RetirementHoldingsSyncReport,
  RetirementPortfolioData,
} from '../model/retirement';
import { RetirementOptionsData } from '../model/retirement-options';
import { WheelCandidate } from '../model/wheel-candidate';
import { CollectionScopeRequest, OptionQuoteSnapshot } from '../model/option-quotes';
import { SectorRotationSnapshot } from '../model/sector-rotation';
import { MomentumStock, StockAnalysis } from '../model/stock';
import {
  OptionsActivitySnapshot, OptionsActivitySyncReport,
  OptionsSnapshot, OptionsTradeGroup
} from '../model/options-ledger';

@Injectable({ providedIn: 'root' })
export class StockService {
  private readonly http = inject(HttpClient);

  private readonly apiBaseUrl = window.location.port === '4200'
    ? 'http://localhost:8000'
    : window.location.origin;
  private stockUrl = `${this.apiBaseUrl}/stocks`;
  private momentumStocksUrl = `${this.apiBaseUrl}/momentumStocks`;
  private readonly stockInfoCache = new Map<string, StockInfo>();
  private readonly stockInfoRequests = new Map<string, Observable<StockInfo>>();

  constructor() {}

  /** Fetch the focused cached-analysis payload used by Stock Detail. */
  getStockAnalysis(symbol: string): Observable<StockAnalysis> {
    const normalizedSymbol = symbol?.trim().toUpperCase();
    if (!normalizedSymbol) {
      return throwError(() => new Error('Stock symbol is required'));
    }
    return this.http.get<StockAnalysis>(
      `${this.stockUrl}/${encodeURIComponent(normalizedSymbol)}/analysis`
    );
  }

  /**
   * Handle Http operation that failed.
   * Let the app continue.
   * @param operation - name of the operation that failed
   * @param result - optional value to return as the observable result
   */
  private handleError<T>(operation = 'operation', result?: T) {
    return (error: any): Observable<T> => {
      // TODO: send the error to remote logging infrastructure
      console.error(`${operation} failed:`, error);

      // Let the app keep running by returning an empty result.
      return of(result as T);
    };
  }

  getStockInfo(symbol: string): Observable<StockInfo> {
    const normalizedSymbol = symbol?.trim().toUpperCase();
    if (!normalizedSymbol) {
      return throwError(() => new Error('Stock symbol is required'));
    }

    const cached = this.stockInfoCache.get(normalizedSymbol);
    if (cached) {
      return of(cached);
    }

    const pending = this.stockInfoRequests.get(normalizedSymbol);
    if (pending) {
      return pending;
    }

    const url = `${this.stockUrl}/${encodeURIComponent(normalizedSymbol)}/info`;
    const shared$ = this.http.get<StockInfo>(url).pipe(
      tap(info => {
        this.stockInfoCache.set(normalizedSymbol, info);
      }),
      shareReplay({ bufferSize: 1, refCount: true })
    );

    const managed$ = shared$.pipe(
      finalize(() => {
        this.stockInfoRequests.delete(normalizedSymbol);
      })
    );

    this.stockInfoRequests.set(normalizedSymbol, managed$);
    return managed$;
  }

  /** GET the merged, setup-specific momentum scanner payload. */
  getMomentumStocks(): Observable<MomentumStock[]> {
    // Let this error reach the scanner so a failed API call is not presented
    // as a legitimate empty candidate set.
    return this.http.get<MomentumStock[]>(this.momentumStocksUrl);
  }

  /**
   * GET wheel candidates from the server (Phase 1 options-wheel scan).
   * When `horizon` is supplied the backend returns only that DTE bucket;
   * omitted, it returns ALL symbol x horizon rows.
   */
  getWheelCandidates(horizon?: number): Observable<WheelCandidate[]> {
    const url = horizon != null
      ? `${this.apiBaseUrl}/wheelCandidates?horizon=${horizon}`
      : `${this.apiBaseUrl}/wheelCandidates`;
    return this.http.get<WheelCandidate[]>(url)
      .pipe(
        catchError(this.handleError<WheelCandidate[]>('getWheelCandidates', []))
      );
  }

  /**
   * Refresh the shared upcoming-earnings calendar (Finnhub, via `ensure-events`)
   * and report how many scanner symbols have a known upcoming report.
   */
  runEarningsScan(): Observable<any> {
    return this.http.get<any>(`${this.apiBaseUrl}/runEarningsScan`)
      .pipe(
        catchError(this.handleError<any>('runEarningsScan', { status: 'error' }))
      );
  }

  /** Trigger the Python wheel scan on the server and reload its cache. */
  runWheel(): Observable<any> {
    return this.http.get<any>(`${this.apiBaseUrl}/runWheel`)
      .pipe(
        catchError(this.handleError<any>('runWheel', { status: 'error' }))
      );
  }

  /**
   * Collect option quotes, optionally scoped to the Wheel view's horizon,
   * cushion, and filtered symbols. Scope only ever narrows the collection.
   */
  runChains(scope?: CollectionScopeRequest): Observable<any> {
    let params = new HttpParams();
    if (scope?.horizonDte != null) {
      params = params.set('horizonDte', String(scope.horizonDte));
    }
    if (scope?.symbols?.length) {
      params = params.set('symbols', scope.symbols.join(','));
    }
    if (scope?.minOtmPct != null) {
      params = params.set('minOtmPct', String(scope.minOtmPct));
    }
    return this.http.get<any>(`${this.apiBaseUrl}/runChains`, { params })
      .pipe(
        catchError((err) => {
          console.error('runChains failed:', err);
          // A 400 carries an actionable scope message; surface it, not a generic failure.
          return of({ status: 'error', message: err?.error?.detail ?? undefined });
        })
      );
  }

  /** Read the latest immutable option-quote archive; this never fetches a quote provider. */
  getOptionQuotes(): Observable<OptionQuoteSnapshot> {
    return this.http.get<OptionQuoteSnapshot>(`${this.apiBaseUrl}/optionQuotes`);
  }

  /** Read the latest archived sector-leadership snapshot; never fetches prices. */
  getSectorRotation(): Observable<SectorRotationSnapshot> {
    return this.http.get<SectorRotationSnapshot>(`${this.apiBaseUrl}/sectorRotation`);
  }

  /** Recompute the sector-leadership snapshot from the local price cache. */
  runSectorRotation(): Observable<any> {
    return this.http.get<any>(`${this.apiBaseUrl}/runSectorRotation`)
      .pipe(
        catchError(this.handleError<any>('runSectorRotation', { status: 'error' }))
      );
  }

  /** Fetch the separate options ledger, totals, wheel summaries, and §6 risk dashboard. */
  getOptions(account: string = 'ALL'): Observable<OptionsSnapshot> {
    const suffix = account && account !== 'ALL' ? `?account=${encodeURIComponent(account)}` : '';
    return this.http.get<OptionsSnapshot>(`${this.apiBaseUrl}/options${suffix}`)
      .pipe(catchError(this.handleError<OptionsSnapshot>('getOptions')));
  }

  getOptionsActivity(account: string = 'ALL'): Observable<OptionsActivitySnapshot> {
    const suffix = account && account !== 'ALL' ? `?account=${encodeURIComponent(account)}` : '';
    return this.http.get<OptionsActivitySnapshot>(`${this.apiBaseUrl}/options/activity${suffix}`)
      .pipe(catchError(this.handleError<OptionsActivitySnapshot>('getOptionsActivity')));
  }

  syncOptionsActivity(): Observable<OptionsActivitySyncReport> {
    return this.http.post<OptionsActivitySyncReport>(`${this.apiBaseUrl}/options/activity/sync`, {})
      .pipe(catchError(this.handleError<OptionsActivitySyncReport>('syncOptionsActivity')));
  }

  updateOptionsGroup(groupId: string, request: {
    name?: string; notes?: string; status?: string;
  }): Observable<OptionsTradeGroup> {
    return this.http.put<OptionsTradeGroup>(
      `${this.apiBaseUrl}/options/groups/${encodeURIComponent(groupId)}`, request
    ).pipe(catchError(this.handleError<OptionsTradeGroup>('updateOptionsGroup')));
  }

  /** Manual reconciliation rows deliberately skip `handleError` so a rejected
   *  entry surfaces the server's validation detail instead of failing silently. */
  createManualOptionsEvent(request: {
    account: string; contract_key: string; underlying_symbol?: string;
    instrument_type?: string; quantity: number; transaction_date: string;
    price?: number | null; net_cash?: number | null; fees?: number | null;
    description?: string; group_id?: string | null;
  }): Observable<{ event_id: string; group_id: string | null }> {
    return this.http.post<{ event_id: string; group_id: string | null }>(
      `${this.apiBaseUrl}/options/activity/manual`, request
    );
  }

  updateManualOptionsEvent(eventId: string, request: {
    quantity: number; transaction_date: string;
    price?: number | null; net_cash?: number | null; fees?: number | null;
    description?: string;
  }): Observable<{ event_id: string; updated: boolean }> {
    return this.http.put<{ event_id: string; updated: boolean }>(
      `${this.apiBaseUrl}/options/activity/manual/${eventId.split('/').map(encodeURIComponent).join('/')}`,
      request
    );
  }

  deleteManualOptionsEvent(eventId: string): Observable<{ event_id: string; deleted: boolean }> {
    return this.http.delete<{ event_id: string; deleted: boolean }>(
      `${this.apiBaseUrl}/options/activity/manual/${eventId.split('/').map(encodeURIComponent).join('/')}`
    );
  }

  /** Fetch retirement holdings (SnapTrade ledger + editable enrichment). */
  getRetirementPortfolio(): Observable<RetirementPortfolioData> {
    return this.http.get<RetirementPortfolioData>(`${this.apiBaseUrl}/retirement/portfolio/live`)
      .pipe(
        catchError(this.handleError<RetirementPortfolioData>('getRetirementPortfolio'))
      );
  }

  /** Pull current holdings from the broker via SnapTrade and rewrite the ledger.
   *  Errors propagate to the caller so the UI can show why a sync failed. */
  syncRetirementHoldings(): Observable<RetirementHoldingsSyncReport> {
    return this.http.post<RetirementHoldingsSyncReport>(`${this.apiBaseUrl}/retirement/holdings/sync`, {});
  }

  /** Save or replace the G/L percentages for the current Fidelity sync date. */
  captureRetirementGainLossSnapshot(): Observable<RetirementGainLossSnapshotResponse> {
    return this.http.post<RetirementGainLossSnapshotResponse>(
      `${this.apiBaseUrl}/retirement/holdings/gain-loss-snapshots`, {}
    );
  }

  /** Create or update the editable category/industry/note for one symbol.
   *  Errors propagate to the caller. */
  updateRetirementEnrichment(
    symbol: string,
    body: { category?: string; industry?: string; note?: string }
  ): Observable<any> {
    return this.http.put<any>(
      `${this.apiBaseUrl}/retirement/enrichment/${encodeURIComponent(symbol)}`, body
    );
  }

  /** Retirement options: trade groups + broker risk positions. */
  getRetirementOptions(): Observable<RetirementOptionsData> {
    return this.http.get<RetirementOptionsData>(`${this.apiBaseUrl}/retirement/options`)
      .pipe(catchError(this.handleError<RetirementOptionsData>('getRetirementOptions')));
  }

  /** Update the editable name/status/notes for one smallFish option group. */
  updateRetirementOptionGroup(
    groupId: string,
    body: { name?: string; status?: string; notes?: string }
  ): Observable<any> {
    return this.http.put<any>(
      `${this.apiBaseUrl}/retirement/options/groups/${encodeURIComponent(groupId)}`, body
    );
  }
}
