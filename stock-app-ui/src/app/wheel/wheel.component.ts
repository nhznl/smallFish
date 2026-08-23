import { Component, OnInit, OnDestroy, inject, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { MatTableModule, MatTableDataSource } from '@angular/material/table';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSortModule, MatSort, MatSortable } from '@angular/material/sort';
import { MatTooltipModule } from '@angular/material/tooltip';
import { FormsModule } from '@angular/forms';
import { StockService } from '../api/stock.service';
import { SymbolFilterService } from '../services/symbol-filter.service';
import { Subscription } from 'rxjs';
import { RvPercentileDetail, WheelCandidate } from '../model/wheel-candidate';
import { OptionQuotesTabComponent } from './option-quotes-tab.component';
import { WheelCandidatesViewModel } from './wheel-candidates.view-model';
import { DrawerComponent } from '../shared/ui/drawer.component';

@Component({
  selector: 'app-wheel',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatTableModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatSortModule,
    MatTooltipModule,
    FormsModule,
    OptionQuotesTabComponent,
    DrawerComponent,
  ],
  templateUrl: './wheel.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./wheel.component.css']
})
export class WheelComponent implements OnInit, OnDestroy {
  // Same pattern as StrategyStocksComponent: the table is behind an *ngIf, so
  // the MatSort directive only exists once data has rendered. A setter wires
  // sorting up whenever the directive appears.
  private sort?: MatSort;

  @ViewChild(MatSort) set matSort(s: MatSort | undefined) {
    this.sort = s;
    if (s) {
      this.dataSource.sort = s;
      this.dataSource.sortingDataAccessor = (item, property) => this.sortingAccessor(item, property);
      if (!s.active) {
        s.sort({ id: 'rvPercentile252', start: 'desc', disableClear: false } as MatSortable);
      }
    }
  }

  private stockService = inject(StockService);
  private symbolFilterService = inject(SymbolFilterService);
  private filterSub?: Subscription;
  private loadSub?: Subscription;
  private rvDetailSub?: Subscription;

  readonly candidatesVm = new WheelCandidatesViewModel();

  // Raw candidates for ALL horizons; the table shows one horizon at a time.
  dataSource = new MatTableDataSource<WheelCandidate>([]);

  get loadingData(): boolean {
    return this.candidatesVm.loadingData();
  }

  get loadError(): boolean {
    return this.candidatesVm.loadError();
  }

  get currentRunMode(): string {
    return this.candidatesVm.runMode();
  }

  // Filters / selectors
  readonly horizons: number[] = [7, 14, 30, 37, 45];
  horizon = 37;                       // default per requirements section 5
  readonly cushions: number[] = [2.5, 5, 7.5, 10];
  cushion = 5;                        // default OTM cushion shown in the ITM/touch columns
  hideBearish = true;                 // default-ON; hides ONLY trendDirection === 'BEARISH'
  showUnavailable = false;            // default-OFF; live chain eligibility requires quality OK
  etfsOnly = false;
  symbolFilter = '';

  // Curated, decision-relevant subset of the versioned wheel columns.
  // the CSV report (and the Wheel Explainer) for auditing -- see the column-sync
  // rule (section 5). The put/call ITM + touch columns follow the cushion selector.
  displayedColumns: string[] = [
    'symbol',
    'horizonDte',
    'lastClose',
    'rvPercentile252',
    'sigmaMovePct',
    'putExpiryItm',
    'putTouch',
    'callExpiryItm',
    'callTouch',
    'minCushion20pctItm',
    'sampleCount',
    'dataQuality',
    'avgDollarVolume20',
    'daysToEvent',
    'earningsWindowState',
    'scoreTotal',
    'signalBand',
    'sector',
    'trendDirection'
  ];

  // Two-row header: a band spanning the cushion-dependent Put/Call pairs sits
  // above the column names. The spans must add up to displayedColumns.length.
  readonly groupHeaderColumns: string[] = ['groupLead', 'groupPut', 'groupCall', 'groupTail'];

  readonly skeletonRows = Array.from({ length: 8 });

  // Run Wheel button state (mirrors the Strategy Explainer "Run Scan Now" button)
  wheelRunning = false;
  wheelStatus: 'idle' | 'ok' | 'warning' | 'error' = 'idle';
  wheelMessage = '';
  wheelMessageAt: Date | null = null;
  chainsRunning = false;
  chainsStatus: 'idle' | 'ok' | 'error' = 'idle';
  chainsMessage = '';
  chainsMessageAt: Date | null = null;
  activeTab: 'scan' | 'quotes' = 'scan';
  quoteArchiveRefreshToken = 0;
  rvDetailSymbol = '';
  rvDetail?: RvPercentileDetail;
  rvDetailLoading = false;
  rvDetailError = '';

  // Collection scoping. ON by default so the button collects what the controls
  // above it describe; turn it off to run the full configured sweep. Scope is
  // subtractive only -- it can never widen a run or relax a quality gate.
  scopeCollection = true;
  // Only these DTEs are configured for chain collection (chains.yaml
  // `chain_dtes`). The wheel table offers more horizons than the chain policy
  // covers, so a scoped run at 14/30/45 DTE has nothing to collect.
  readonly collectibleHorizons: number[] = [7, 37];
  // A symbol list only rides in the query string when the view is genuinely
  // narrowed. The unfiltered table runs to four figures, which would overflow
  // the URL and narrow nothing the collection pool does not already cap.
  readonly maxScopedSymbols = 100;

  ngOnInit(): void {
    this.symbolFilter = this.symbolFilterService.getFilter();
    this.load();
    this.filterSub = this.symbolFilterService.filter$.subscribe(() => this.applyFilters());
  }

  load(): void {
    this.loadSub?.unsubscribe();
    this.loadSub = this.candidatesVm.load(this.stockService, () => this.applyFilters());
  }

  applyFilters(): void {
    const rows = this.candidatesVm.candidates().filter(c => {
      if (c.wheel?.horizonDte !== this.horizon) {
        return false;
      }
      // Symbol filter (shared across tabs).
      if (!this.symbolFilterService.matchesFilter(c.wheel?.symbol ?? '')) {
        return false;
      }
      // Hide predicate is EXACTLY trendDirection === 'BEARISH'. A null/absent
      // trendDirection ("not evaluated") is NEVER hidden.
      if (this.hideBearish && c.trendDirection === 'BEARISH') {
        return false;
      }
      if (!this.showUnavailable && c.wheel?.dataQuality !== 'OK') {
        return false;
      }
      if (this.etfsOnly && c.type !== 'ETF') {
        return false;
      }
      return true;
    });
    this.dataSource.data = rows;
    if (this.sort) {
      this.dataSource.sort = this.sort;
      this.dataSource.sortingDataAccessor = (item, property) => this.sortingAccessor(item, property);
    }
  }

  openRvDetail(candidate: WheelCandidate): void {
    const symbol = candidate.wheel.symbol;
    if (!symbol || candidate.wheel.rvPercentile252 == null) {
      return;
    }
    this.rvDetailSub?.unsubscribe();
    this.rvDetailSymbol = symbol;
    this.rvDetail = undefined;
    this.rvDetailError = '';
    this.rvDetailLoading = true;
    this.rvDetailSub = this.stockService.getWheelRvDetail(symbol).subscribe({
      next: detail => {
        this.rvDetail = detail;
        this.rvDetailLoading = false;
      },
      error: () => {
        this.rvDetailError = 'RV-percentile detail is unavailable for this report. Run Wheel to create a current report.';
        this.rvDetailLoading = false;
      },
    });
  }

  closeRvDetail(): void {
    this.rvDetailSub?.unsubscribe();
    this.rvDetailSymbol = '';
    this.rvDetail = undefined;
    this.rvDetailLoading = false;
    this.rvDetailError = '';
  }

  rvObservationsNewestFirst(): Array<{ date: string; rv: number }> {
    return this.rvDetail ? [...this.rvDetail.observations].reverse() : [];
  }

  rvObservationsAtOrBelowCurrent(): number {
    if (!this.rvDetail) {
      return 0;
    }
    return this.rvDetail.observations.filter(item => item.rv <= this.rvDetail!.current_rv).length;
  }

  // ── Cushion-aware accessors (which of the 4 cushion columns to display) ──
  putItm(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.putExpiryItm25;
      case 5: return w.putExpiryItm5;
      case 7.5: return w.putExpiryItm75;
      case 10: return w.putExpiryItm10;
      default: return undefined;
    }
  }

  putTouchVal(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.putTouch25;
      case 5: return w.putTouch5;
      case 7.5: return w.putTouch75;
      case 10: return w.putTouch10;
      default: return undefined;
    }
  }

  callItm(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.callExpiryItm25;
      case 5: return w.callExpiryItm5;
      case 7.5: return w.callExpiryItm75;
      case 10: return w.callExpiryItm10;
      default: return undefined;
    }
  }

  callTouchVal(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.callTouch25;
      case 5: return w.callTouch5;
      case 7.5: return w.callTouch75;
      case 10: return w.callTouch10;
      default: return undefined;
    }
  }

  putItmNonoverlap(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.putExpiryItmNonoverlap25;
      case 5: return w.putExpiryItmNonoverlap5;
      case 7.5: return w.putExpiryItmNonoverlap75;
      case 10: return w.putExpiryItmNonoverlap10;
      default: return undefined;
    }
  }

  callItmNonoverlap(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.callExpiryItmNonoverlap25;
      case 5: return w.callExpiryItmNonoverlap5;
      case 7.5: return w.callExpiryItmNonoverlap75;
      case 10: return w.callExpiryItmNonoverlap10;
      default: return undefined;
    }
  }

  putTouchNonoverlap(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.putTouchNonoverlap25;
      case 5: return w.putTouchNonoverlap5;
      case 7.5: return w.putTouchNonoverlap75;
      case 10: return w.putTouchNonoverlap10;
      default: return undefined;
    }
  }

  callTouchNonoverlap(c: WheelCandidate): number | undefined {
    const w = c.wheel;
    switch (this.cushion) {
      case 2.5: return w.callTouchNonoverlap25;
      case 5: return w.callTouchNonoverlap5;
      case 7.5: return w.callTouchNonoverlap75;
      case 10: return w.callTouchNonoverlap10;
      default: return undefined;
    }
  }

  /** Format a 0..1 fraction as a whole-percent string; null/undefined → "—". */
  fmtPct(v: number | undefined): string {
    return v != null ? Math.round(v * 100) + '%' : '—';
  }

  /** Trend direction for display; null/absent renders as "—" (not evaluated). */
  trendLabel(c: WheelCandidate): string {
    return c.trendAvailable && c.trendDirection ? c.trendDirection : '—';
  }

  trendClass(c: WheelCandidate): string {
    if (!c.trendAvailable || !c.trendDirection) {
      return '';
    }
    return 'trend-' + c.trendDirection.toLowerCase();
  }

  bandClass(band: string | undefined): string {
    if (!band) {
      return '';
    }
    return 'band-' + band.toLowerCase().replace(/\s+/g, '-');
  }

  qualityBadgeClass(state: string | undefined): string {
    if (state === 'OK') return 'badge-pos';
    if (!state || state === 'UNKNOWN') return 'badge-neutral';
    return 'badge-warn';
  }

  earningsBadgeClass(state: string | undefined): string {
    if (state === 'KNOWN_EVENT') return 'badge-neg';
    if (state === 'NO_EVENT_IN_FETCHED_RANGE') return 'badge-pos';
    if (state === 'UNKNOWN_STALE') return 'badge-warn';
    return 'badge-neutral';
  }

  jobStatusClass(status: 'idle' | 'ok' | 'warning' | 'error'): string {
    if (status === 'ok') return 'job-ok';
    if (status === 'warning') return 'job-warning';
    if (status === 'error') return 'job-error';
    return 'job-running';
  }

  applySymbolFilter(): void {
    this.symbolFilterService.setFilter(this.symbolFilter);
  }

  clearSymbolFilter(): void {
    this.symbolFilter = '';
    this.symbolFilterService.clearFilter();
  }

  runWheel(): void {
    this.wheelRunning = true;
    this.wheelStatus = 'idle';
    this.wheelMessage = 'Running wheel scan… this can take a few minutes.';
    this.wheelMessageAt = new Date();
    this.stockService.runWheel().subscribe(res => {
      this.wheelRunning = false;
      if (res && res.status === 'ok') {
        this.wheelStatus = res.warning ? 'warning' : 'ok';
        const secs = res.durationMs ? Math.round(res.durationMs / 1000) : null;
        const completion = `Wheel scan complete${secs != null ? ' in ' + secs + 's' : ''}.`;
        this.wheelMessage = res.warning
          ? `${completion} ${res.warning}`
          : `${completion} Reloading candidates…`;
        this.wheelMessageAt = new Date();
        this.load();
      } else {
        this.wheelStatus = 'error';
        this.wheelMessage = '✗ Wheel scan failed: ' + (res?.message || res?.output || 'see server logs') + '.';
        this.wheelMessageAt = new Date();
      }
    });
  }

  /** Symbols currently visible in the table — what a scoped run collects. */
  scopedSymbols(): string[] {
    return Array.from(new Set(
      this.dataSource.data.map(c => (c.wheel?.symbol ?? '').toUpperCase()).filter(s => s)
    )).sort();
  }

  /** True when the selected horizon is one the chain policy actually collects. */
  horizonCollectible(): boolean {
    return this.collectibleHorizons.includes(this.horizon);
  }

  /**
   * The visible symbols, but only when that list is small enough to be a real
   * narrowing. Above the cap the request omits symbols entirely and the
   * collection pool's own quality/liquidity gates and rank cap apply.
   */
  private scopedSymbolsForRequest(): string[] | undefined {
    const symbols = this.scopedSymbols();
    return symbols.length && symbols.length <= this.maxScopedSymbols ? symbols : undefined;
  }

  /** Blocking reason for a scoped run, or '' when it can proceed. */
  scopeBlockReason(): string {
    if (!this.scopeCollection) {
      return '';
    }
    if (!this.horizonCollectible()) {
      return `${this.horizon} DTE is not a configured collection horizon ` +
        `(${this.collectibleHorizons.join(', ')}). Switch horizon, or turn off ` +
        `"Scope collection to this view" to run the full sweep.`;
    }
    if (!this.scopedSymbols().length) {
      return 'No symbols match the current filters, so a scoped run would ' +
        'collect nothing.';
    }
    return '';
  }

  /** One-line description of what the button will collect. */
  scopeSummary(): string {
    if (!this.scopeCollection) {
      return 'Full configured sweep: every collection horizon and the whole eligible pool.';
    }
    const scoped = this.scopedSymbolsForRequest();
    const symbolText = scoped
      ? `${scoped.length} symbol${scoped.length === 1 ? '' : 's'} in view`
      : `the eligible pool (${this.scopedSymbols().length} shown is above the ` +
        `${this.maxScopedSymbols}-symbol scoping limit — filter further to target symbols)`;
    return `Scoped: ${this.horizon} DTE, ${symbolText}, entry strikes at least ` +
      `${this.cushion}% OTM. Roll/exit (ITM) strikes are unaffected by the cushion.`;
  }

  runChains(): void {
    const blocked = this.scopeBlockReason();
    if (blocked) {
      this.chainsStatus = 'error';
      this.chainsMessage = '✗ ' + blocked;
      this.chainsMessageAt = new Date();
      return;
    }
    const scoped = this.scopedSymbolsForRequest();
    this.chainsRunning = true;
    this.chainsStatus = 'idle';
    this.chainsMessage = this.scopeCollection
      ? `Collecting exact Tastytrade option quotes — ${this.horizon} DTE, ` +
        `${scoped ? scoped.length + ' symbols' : 'eligible pool'}, ≥${this.cushion}% OTM…`
      : 'Collecting exact Tastytrade option quotes for the full pool…';
    this.chainsMessageAt = new Date();
    const scope = this.scopeCollection
      ? {
          horizonDte: this.horizon,
          symbols: scoped,
          minOtmPct: this.cushion
        }
      : undefined;
    this.stockService.runChains(scope).subscribe(res => {
      this.chainsRunning = false;
      if (res && res.status === 'ok') {
        this.chainsStatus = 'ok';
        const secs = res.durationMs ? Math.round(res.durationMs / 1000) : null;
        const output = res.output ? ` ${res.output.split('\n').slice(-1)[0]}` : '';
        this.chainsMessage = `✓ Quote collection complete${secs != null ? ' in ' + secs + 's' : ''}.${output}`;
        this.chainsMessageAt = new Date();
        this.activeTab = 'quotes';
        this.quoteArchiveRefreshToken++;
      } else {
        this.chainsStatus = 'error';
        this.chainsMessage = '✗ Quote collection failed closed: ' +
          (res?.message || res?.output || 'see server logs') + '.';
        this.chainsMessageAt = new Date();
      }
    });
  }

  private sortingAccessor(item: WheelCandidate, property: string): string | number {
    const w = item.wheel;
    switch (property) {
      case 'symbol': return w?.symbol ?? '';
      case 'horizonDte': return w?.horizonDte ?? 0;
      case 'lastClose': return w?.lastClose ?? -Infinity;
      case 'rvPercentile252': return w?.rvPercentile252 ?? -Infinity;
      case 'sigmaMovePct': return w?.sigmaMovePct ?? -Infinity;
      case 'putExpiryItm': return this.putItm(item) ?? -Infinity;
      case 'putTouch': return this.putTouchVal(item) ?? -Infinity;
      case 'callExpiryItm': return this.callItm(item) ?? -Infinity;
      case 'callTouch': return this.callTouchVal(item) ?? -Infinity;
      case 'minCushion20pctItm': return w?.minCushion20pctItm ?? '';
      case 'sampleCount': return w?.sampleCount ?? -Infinity;
      case 'dataQuality': return w?.dataQuality ?? '';
      case 'avgDollarVolume20': return w?.avgDollarVolume20 ?? -Infinity;
      case 'daysToEvent': return w?.daysToEvent ?? Infinity;
      case 'earningsWindowState': return w?.earningsWindowState ?? '';
      case 'scoreTotal': return w?.scoreTotal ?? -Infinity;
      case 'signalBand': return w?.signalBand ?? '';
      case 'sector': return w?.sector ?? '';
      case 'trendDirection': return item.trendDirection ?? '';
      default: return '';
    }
  }

  ngOnDestroy(): void {
    this.filterSub?.unsubscribe();
    this.loadSub?.unsubscribe();
    this.rvDetailSub?.unsubscribe();
  }
}
