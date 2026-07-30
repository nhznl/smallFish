import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of, throwError } from 'rxjs';
import { catchError, tap, shareReplay, finalize } from 'rxjs/operators';
import { StockInfo } from '../model/stock-info';
import { WheelCandidate } from '../model/wheel-candidate';
import { CollectionScopeRequest, OptionQuoteSnapshot } from '../model/option-quotes';
import { SectorRotationSnapshot } from '../model/sector-rotation';
import { MomentumStock, StockAnalysis } from '../model/stock';
import {
  ChainsJobResult,
  EarningsScanResult,
  SectorRotationJobResult,
  WheelJobResult,
} from '../model/job-results';
import { API_BASE_URL } from './api-base';

@Injectable({ providedIn: 'root' })
export class StockService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = inject(API_BASE_URL);
  private stockUrl = `${this.apiBaseUrl}/stocks`;
  private momentumStocksUrl = `${this.apiBaseUrl}/momentumStocks`;
  private readonly stockInfoCache = new Map<string, StockInfo>();
  private readonly stockInfoRequests = new Map<string, Observable<StockInfo>>();


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
    // Propagate transport errors so the Wheel view can distinguish failed from empty.
    return this.http.get<WheelCandidate[]>(url);
  }

  /**
   * Refresh the shared upcoming-earnings calendar (Finnhub, via `ensure-events`)
   * and report how many scanner symbols have a known upcoming report.
   */
  runEarningsScan(): Observable<EarningsScanResult> {
    return this.http.post<EarningsScanResult>(`${this.apiBaseUrl}/runEarningsScan`, null)
      .pipe(
        catchError(this.handleError<EarningsScanResult>('runEarningsScan', { status: 'error' as const }))
      );
  }

  /** Trigger the Python wheel scan on the server and reload its cache. */
  runWheel(): Observable<WheelJobResult> {
    return this.http.post<WheelJobResult>(`${this.apiBaseUrl}/runWheel`, null)
      .pipe(
        catchError(this.handleError<WheelJobResult>('runWheel', { status: 'error' as const }))
      );
  }

  /**
   * Collect option quotes, optionally scoped to the Wheel view's horizon,
   * cushion, and filtered symbols. Scope only ever narrows the collection.
   */
  runChains(scope?: CollectionScopeRequest): Observable<ChainsJobResult> {
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
    return this.http.post<ChainsJobResult>(`${this.apiBaseUrl}/runChains`, null, { params })
      .pipe(
        catchError((err) => {
          console.error('runChains failed:', err);
          // A 400 carries an actionable scope message; surface it, not a generic failure.
          return of({
            status: 'error' as const,
            message: err?.error?.detail ?? undefined,
          } satisfies ChainsJobResult);
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
  runSectorRotation(): Observable<SectorRotationJobResult> {
    return this.http.post<SectorRotationJobResult>(`${this.apiBaseUrl}/runSectorRotation`, null)
      .pipe(
        catchError(this.handleError<SectorRotationJobResult>('runSectorRotation', { status: 'error' as const }))
      );
  }

}
