import { CommonModule } from '@angular/common';
import { Component, DestroyRef, Input, OnChanges, OnInit, SimpleChanges, inject, ChangeDetectionStrategy } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { catchError, forkJoin, from, map, mergeMap, of, Subscription, timeout } from 'rxjs';
import { BrokerageService } from '../api/brokerage.service';
import { StockService } from '../api/stock.service';
import { BrokerageId } from '../model/brokerage';
import { OptionQuoteReport, OptionQuoteRow, OptionQuoteSnapshot } from '../model/option-quotes';

interface QuoteSideGroup {
  side: string;
  label: string;
  rows: OptionQuoteRow[];
}

interface QuoteExpiryGroup {
  expiry: string | null;
  actualDtes: string;
  requestedDtes: string;
  multipleRequestedDtes: boolean;
  count: number;
  sides: QuoteSideGroup[];
}

interface QuoteSymbolGroup {
  symbol: string | null;
  expiries: QuoteExpiryGroup[];
}

interface SymbolMarketPrice {
  loading: boolean;
  price: number | null;
  currency: string | null;
  retrievedAt: string | null;
}

interface SymbolHolding {
  shares: number;
  costBasis: number | null;
  sources: string[];
}

@Component({
  selector: 'app-option-quotes-tab',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, MatTooltipModule],
  templateUrl: './option-quotes-tab.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./option-quotes-tab.component.css']
})
export class OptionQuotesTabComponent implements OnInit, OnChanges {
  @Input() refreshToken = 0;

  private readonly stockService = inject(StockService);
  private readonly brokerageService = inject(BrokerageService);
  private readonly destroyRef = inject(DestroyRef);
  private archiveRequest?: Subscription;
  private reportsRequest?: Subscription;
  private priceRequest?: Subscription;
  private holdingsRequest?: Subscription;
  private readonly marketPrices = new Map<string, SymbolMarketPrice>();
  private readonly holdings = new Map<string, SymbolHolding>();
  holdingsLoading = false;
  holdingsIncomplete = false;
  holdingsNotice = '';
  snapshot: OptionQuoteSnapshot | null = null;
  reports: OptionQuoteReport[] = [];
  selectedRunId: string | null = null;
  loading = false;
  error = '';
  symbolFilter = '';

  ngOnInit(): void {
    this.load();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['refreshToken'] && !changes['refreshToken'].firstChange) this.load();
  }

  load(): void {
    this.archiveRequest?.unsubscribe();
    this.reportsRequest?.unsubscribe();
    this.priceRequest?.unsubscribe();
    this.holdingsRequest?.unsubscribe();
    this.marketPrices.clear();
    this.holdings.clear();
    this.holdingsLoading = false;
    this.holdingsIncomplete = false;
    this.holdingsNotice = '';
    this.loading = true;
    this.error = '';
    this.snapshot = null;
    this.reportsRequest = this.stockService.getOptionQuoteReports().pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: reports => {
        this.reports = reports ?? [];
        const selected = this.reports.find(report => report.runId === this.selectedRunId) ?? this.reports[0];
        if (selected?.runId) {
          this.loadReport(selected.runId);
        } else {
          this.loading = false;
          this.snapshot = { available: false, reason: 'No option-quote collection has been archived yet.' };
        }
      },
      error: () => {
        this.snapshot = null;
        this.loading = false;
        this.error = 'Could not load option-quote reports.';
      }
    });
  }

  selectReport(runId: string | undefined): void {
    if (!runId || runId === this.selectedRunId) return;
    this.loadReport(runId);
  }

  reportTitle(report: OptionQuoteReport | OptionQuoteSnapshot): string {
    const name = report.reportName;
    const match = name?.match(/^(\d{4}-\d{2}-\d{2})__horizon([^_]+)_cushion([^_]+)_filterholdings\(([TF])\)_etfOnly\(([TF])\)_rvRank([^_]+)_trend(ALL|BULLISH|BEARISH)$/);
    if (match) {
      const [, date, horizon, cushion, holdings, etfs, rvRank, trend] = match;
      return `${this.readableDate(date)} · ${horizon} DTE · ${cushion}% OTM · ` +
        `Holdings: ${holdings === 'T' ? 'Yes' : 'No'} · ETFs ${etfs === 'T' ? 'only' : 'all'} · ` +
        `RV Rank ${rvRank} · Trend ${trend[0] + trend.slice(1).toLowerCase()}`;
    }
    const horizon = report.collectionScope?.requestedDtes?.join(', ') ?? '—';
    return `${this.readableDate(report.asOf)} · ${horizon} DTE quote report`;
  }

  private readableDate(value: string | null | undefined): string {
    const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return value || 'Date unavailable';
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
      .format(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))));
  }

  private loadReport(runId: string): void {
    this.archiveRequest?.unsubscribe();
    this.priceRequest?.unsubscribe();
    this.holdingsRequest?.unsubscribe();
    this.marketPrices.clear();
    this.holdings.clear();
    this.holdingsLoading = false;
    this.holdingsIncomplete = false;
    this.holdingsNotice = '';
    this.selectedRunId = runId;
    this.loading = true;
    this.error = '';
    this.archiveRequest = this.stockService.getOptionQuotes(runId).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: snapshot => {
        this.snapshot = snapshot ?? null;
        this.loading = false;
        if (!snapshot) this.error = 'Could not load the selected quote report.';
        if (snapshot?.available) {
          this.loadMarketPrices(snapshot.rows ?? []);
          this.loadHoldings();
        }
      },
      error: err => {
        this.snapshot = null;
        this.loading = false;
        this.error = err?.error?.detail ?? 'Could not load the selected quote report.';
      }
    });
  }

  private loadMarketPrices(rows: OptionQuoteRow[]): void {
    const symbols = [...new Set(rows.map(row => row.symbol?.trim().toUpperCase()).filter(
      (symbol): symbol is string => !!symbol,
    ))];
    for (const symbol of symbols) {
      this.marketPrices.set(symbol, { loading: true, price: null, currency: null, retrievedAt: null });
    }
    // At most three underlying-info calls at once; never one call per contract.
    this.priceRequest = from(symbols).pipe(
      mergeMap(symbol => this.stockService.getStockInfo(symbol, { refresh: true }).pipe(
        timeout(15000),
        map(info => {
          const value = info?.price?.regularMarketPrice;
          const price = typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
          return { symbol, quote: {
            loading: false, price, currency: info?.price?.currency ?? null,
            retrievedAt: info?.retrievedAt ?? null,
          } };
        }),
        catchError(() => of({ symbol, quote: {
          loading: false, price: null, currency: null, retrievedAt: null,
        } })),
      ), 3),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(({ symbol, quote }) => this.marketPrices.set(symbol, quote));
  }

  marketPrice(symbol: string | null): SymbolMarketPrice | undefined {
    return this.marketPrices.get(symbol?.trim().toUpperCase() ?? '');
  }

  marketPriceTooltip(quote: SymbolMarketPrice | undefined): string {
    if (quote?.loading) return 'Loading the latest Yahoo regular-session market price.';
    if (quote?.price == null) return 'Current market price unavailable. No archived price is substituted.';
    return 'Yahoo regular-session price; may be delayed. Separate from the archived option quotes.' +
      (quote.retrievedAt ? ` Retrieved: ${quote.retrievedAt}.` : '');
  }

  private loadHoldings(): void {
    const brokerages: { id: BrokerageId; label: string }[] = [
      { id: 'tastytrade', label: 'Trading' },
      { id: 'fidelity', label: 'Retirement' },
    ];
    this.holdingsLoading = true;
    // Read saved projections once per brokerage, never sync or fetch per contract.
    this.holdingsRequest = forkJoin(brokerages.map(brokerage =>
      this.brokerageService.getHoldings(brokerage.id).pipe(
        timeout(15000),
        catchError(() => of(null)),
        map(data => ({ label: brokerage.label, data })),
      ),
    )).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(results => {
      const notices: string[] = [];
      for (const { label, data } of results) {
        if (!data || data.availability.status === 'UNAVAILABLE') {
          notices.push(`${label} holdings unavailable.`);
          continue;
        }
        if (data.availability.status === 'PARTIAL') notices.push(`${label} holdings incomplete.`);
        const source = `${label}: positions as of ${data.as_of.positions || 'unknown'}`;
        for (const item of data.items) {
          const symbol = item.symbol?.trim().toUpperCase();
          if (!symbol || item.instrument !== 'EQUITY' || item.side !== 'LONG' || item.state !== 'OPEN' ||
              !Number.isFinite(item.quantity) || item.quantity <= 0) continue;
          const holding = this.holdings.get(symbol) ?? { shares: 0, costBasis: 0, sources: [] };
          holding.shares += item.quantity;
          // The shared projection already applies broker/manual-basis precedence.
          // A missing basis in any account makes the combined cost unavailable.
          holding.costBasis = holding.costBasis != null && item.cost_basis != null && Number.isFinite(item.cost_basis)
            ? holding.costBasis + item.cost_basis : null;
          if (!holding.sources.includes(source)) holding.sources.push(source);
          this.holdings.set(symbol, holding);
        }
      }
      this.holdingsLoading = false;
      this.holdingsIncomplete = notices.length > 0;
      this.holdingsNotice = notices.length
        ? `${notices.join(' ')} Ownership figures cover loaded holdings only; missing symbols do not imply no position.`
        : '';
    });
  }

  holding(symbol: string | null): SymbolHolding | undefined {
    return this.holdings.get(symbol?.trim().toUpperCase() ?? '');
  }

  holdingsTooltip(holding: SymbolHolding): string {
    return 'Open long shares from saved brokerage holdings, not a live brokerage sync. ' +
      holding.sources.join('; ') + '. ' +
      'Average cost/share is total effective cost basis divided by shares, using broker basis or the saved manual fallback. ' +
      'It does not include option P/L.' +
      (holding.costBasis == null ? ' Cost is unavailable because at least one holding has no cost basis.' : '') +
      (this.holdingsIncomplete ? ' Figures cover known holdings only; combined ownership may be incomplete.' : '');
  }

  rows(): OptionQuoteRow[] {
    const query = this.symbolFilter.trim().toUpperCase();
    return (this.snapshot?.rows ?? []).filter(row =>
      !query || (row.symbol ?? '').toUpperCase().includes(query)
    );
  }

  /** Group the filtered archive without pairing strikes or dropping observations. */
  groupRows(rows: OptionQuoteRow[]): QuoteSymbolGroup[] {
    const symbols = new Map<string | null, Map<string | null, OptionQuoteRow[]>>();
    for (const row of rows) {
      let expiries = symbols.get(row.symbol);
      if (!expiries) {
        expiries = new Map();
        symbols.set(row.symbol, expiries);
      }
      const contracts = expiries.get(row.expiry) ?? [];
      contracts.push(row);
      expiries.set(row.expiry, contracts);
    }

    const compareText = (a: string | null, b: string | null) =>
      a == null ? (b == null ? 0 : 1) : b == null ? -1 : a.localeCompare(b);
    const compareNumber = (a: number | null, b: number | null) =>
      a == null ? (b == null ? 0 : 1) : b == null ? -1 : a - b;
    const dtes = (contracts: OptionQuoteRow[], field: 'actualDte' | 'requestedDte') =>
      [...new Set(contracts.map(row => row[field]))].sort(compareNumber);

    return [...symbols.entries()].sort(([a], [b]) => compareText(a, b))
      .map(([symbol, expiries]) => ({
        symbol,
        expiries: [...expiries.entries()].sort(([a], [b]) => compareText(a, b))
          .map(([expiry, contracts]) => {
            const sorted = [...contracts].sort((a, b) =>
              compareNumber(a.strike, b.strike) ||
              compareNumber(a.requestedDte, b.requestedDte) ||
              compareText(a.contractId, b.contractId));
            const sides: QuoteSideGroup[] = [
              { side: 'PUT', label: 'Puts', rows: sorted.filter(row => row.side === 'PUT') },
              { side: 'CALL', label: 'Calls', rows: sorted.filter(row => row.side === 'CALL') },
            ];
            const other = sorted.filter(row => row.side !== 'PUT' && row.side !== 'CALL');
            if (other.length) sides.push({ side: 'OTHER', label: 'Other / unknown side', rows: other });
            const requestedDtes = dtes(contracts, 'requestedDte');
            return {
              expiry,
              actualDtes: dtes(contracts, 'actualDte').map(dte => dte ?? '—').join(', '),
              requestedDtes: requestedDtes.map(dte => dte ?? '—').join(', '),
              multipleRequestedDtes: requestedDtes.length > 1,
              count: contracts.length,
              sides,
            };
          }),
      }));
  }

  viewLabel(view: string | null): string {
    if (view === 'ENTRY') return 'Entry';
    if (view === 'ROLL_EXIT') return 'Roll/exit';
    return view || '—';
  }

  providerValue(name: string): string | number | null {
    const value = this.snapshot?.quoteProvider?.[name];
    return typeof value === 'string' || typeof value === 'number' ? value : null;
  }

  /** Plain-language rendering of the manifest's recorded collection scope. */
  scopeDescription(): string {
    const scope = this.snapshot?.collectionScope;
    if (!scope?.scoped) {
      return '';
    }
    const parts: string[] = [];
    if (scope.requestedDtes?.length) {
      parts.push(`${scope.requestedDtes.join(', ')} DTE only`);
    }
    if (scope.symbolCount != null) {
      parts.push(`${scope.symbolCount} requested symbol${scope.symbolCount === 1 ? '' : 's'}`);
    }
    if (scope.minOtmPct) {
      parts.push(`entry strikes at least ${(scope.minOtmPct * 100).toFixed(1).replace(/\.0$/, '')}% OTM`);
    }
    if (scope.limit != null) {
      parts.push(`limit ${scope.limit}`);
    }
    const reportFilters = this.reportFilterDescription();
    if (reportFilters) parts.push(reportFilters);
    return parts.join('; ') + '.';
  }

  /** Filter fields embedded in the immutable report name by the collecting UI. */
  private reportFilterDescription(): string {
    const name = this.snapshot?.reportName;
    const match = name?.match(/_filterholdings\(([TF])\).*_trend(ALL|BULLISH|BEARISH)$/);
    if (!match) return '';
    const [, holdings, trend] = match;
    return `Holdings: ${holdings === 'T' ? 'Yes' : 'No'}; Trend: ${trend[0] + trend.slice(1).toLowerCase()}`;
  }
}
