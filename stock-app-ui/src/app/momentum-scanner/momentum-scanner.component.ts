import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  inject
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSortModule, Sort } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DrawerComponent } from '../shared/ui/drawer.component';
import { CapabilityStateComponent } from '../shared/ui/capability-state.component';
import { Capability, CapabilityService } from '../api/capability.service';
import { StockService } from '../api/stock.service';
import { MomentumSetup, MomentumStock } from '../model/stock';

type SetupFilter = MomentumSetup | 'ACTIONABLE' | 'ALL';

interface SetupOption {
  value: SetupFilter;
  label: string;
  icon: string;
  tooltip: string;
}

@Component({
  selector: 'app-momentum-scanner',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSortModule,
    MatTableModule,
    MatTooltipModule,
    DrawerComponent,
    CapabilityStateComponent
  ],
  templateUrl: './momentum-scanner.component.html',
  styleUrls: ['./momentum-scanner.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class MomentumScannerComponent {
  private readonly stockService = inject(StockService);
  private readonly capabilityService = inject(CapabilityService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly cdr = inject(ChangeDetectorRef);
  private allStocks: MomentumStock[] = [];
  private filteredRows: MomentumStock[] = [];
  private currentSort: Sort = { active: 'setupScore', direction: 'desc' };

  visibleRows: MomentumStock[] = [];
  /** Lets an empty table say "no data downloaded yet" rather than
   *  "nothing matched" on a first run. */
  coreData: Capability | null = null;
  readonly displayedColumns = [
    'setup',
    'symbol',
    'price',
    'fiftyTwoWeekRange',
    'setupScore',
    'fiveDay',
    'fiveWeek',
    'relativeStrength',
    'volumeRatio',
    'rsi',
    'atrPct',
    'trigger',
    'daysToEarnings',
    'freshness',
    'details'
  ];

  /** A week is complete at five sessions; fewer weakens its return volatility. */
  private static readonly FULL_WEEK_SESSIONS = 5;
  /** Reports inside this many days are flagged as event risk on the row. */
  private static readonly EARNINGS_SOON_DAYS = 7;

  readonly setupOptions: SetupOption[] = [
    { value: 'BULLISH_CONTINUATION', label: 'Bullish', icon: '↑', tooltip: 'Established upward trend with aligned momentum. Research stock or call entries; this is a screen, not a trade recommendation.' },
    { value: 'BEARISH_CONTINUATION', label: 'Bearish', icon: '↓', tooltip: 'Established downward trend with aligned downside momentum. Research put entries; this is a screen, not a trade recommendation.' },
    { value: 'BULLISH_REVERSAL', label: 'Bullish Reversal', icon: '↘', tooltip: 'A bullish stock with a strongly confirmed reversal toward bearish: downside price break, falling RSI/MACD, fresh data, and participating volume. Research a possible put entry after due diligence.' },
    { value: 'BEARISH_REVERSAL', label: 'Bearish Reversal', icon: '↗', tooltip: 'A bearish stock with a strongly confirmed reversal toward bullish: upside price break, rising RSI/MACD, fresh data, and participating volume. Research a possible stock or call entry after due diligence.' },
    { value: 'ACTIONABLE', label: 'Actionable', icon: '◆', tooltip: 'Shows confirmed Bullish, Bearish, Bullish Reversal, and Bearish Reversal setups after excluding continuation rows with preliminary reversal warnings and rows with insufficient history.' },
    { value: 'ALL', label: 'All', icon: '≡', tooltip: 'Shows every non-penny stock in the scanner, including continuation rows with preliminary reversal warnings and rows with incomplete evidence.' }
  ];

  selectedSetup: SetupFilter = 'BULLISH_CONTINUATION';
  symbolFilter = '';
  minimumPrice = 6;
  minimumDollarVolumeMillions = 5;
  minimumScore = 50;
  freshOnly = true;
  participatingVolumeOnly = false;
  etfsOnly = false;
  selectedStock: MomentumStock | null = null;
  loading = true;
  error: string | null = null;
  dataAsOf: Date | null = null;
  staleCount = 0;
  fiveDayAnchor: Date | null = null;
  fiveWeekAnchor: Date | null = null;
  pageIndex = 0;
  pageSize = 50;
  earningsRunning = false;
  earningsStatus: 'idle' | 'ok' | 'error' = 'idle';
  earningsMessage: string | null = null;
  earningsMessageAt: Date | null = null;

  constructor() {
    this.capabilityService.get('core-data')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(capability => {
        this.coreData = capability;
        this.cdr.markForCheck();
      });

    this.loadStocks();
  }

  private loadStocks(): void {
    this.stockService.getMomentumStocks()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: stocks => {
          this.allStocks = stocks ?? [];
          this.loading = false;
          this.updateSnapshotMetadata();
          this.applyFilters();
          this.cdr.markForCheck();
        },
        error: error => {
          this.loading = false;
          this.error = error?.message || 'Unable to load momentum candidates.';
          this.cdr.markForCheck();
        }
      });
  }

  get visibleCount(): number {
    return this.filteredRows.length;
  }

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.filteredRows.length / this.pageSize));
  }

  get pageStart(): number {
    return this.filteredRows.length ? this.pageIndex * this.pageSize + 1 : 0;
  }

  get pageEnd(): number {
    return Math.min((this.pageIndex + 1) * this.pageSize, this.filteredRows.length);
  }

  countFor(filter: SetupFilter): number {
    if (filter === 'ALL') return this.allStocks.length;
    if (filter === 'ACTIONABLE') {
      return this.allStocks.filter(stock => this.isActionable(stock)).length;
    }
    return this.allStocks.filter(stock => stock.setup === filter).length;
  }

  selectSetup(filter: SetupFilter): void {
    this.selectedSetup = filter;
    this.applyFilters();
  }

  applyFilters(): void {
    // Exact tickers, not substrings: pasting a portfolio's symbols must return
    // that portfolio. Substring matching pulled in every incidental hit — `KE`
    // dragged along NKE, KEYS, BKE, CAKE, and KEY.
    const symbols = new Set(
      this.symbolFilter
        .split(/[\s,]+/)
        .map(value => value.trim().toUpperCase())
        .filter(Boolean)
    );

    const filtered = this.allStocks.filter(stock => {
      if (symbols.size && !symbols.has(stock.code.toUpperCase())) return false;
      if ((stock.lastTradeStats?.close ?? 0) < this.minimumPrice) return false;
      if ((stock.averageDollarVolume20 ?? 0) < this.minimumDollarVolumeMillions * 1_000_000) return false;
      if ((stock.setupScore ?? 0) < this.minimumScore) return false;
      if (this.freshOnly && stock.freshnessStatus !== 'FRESH') return false;
      if (this.participatingVolumeOnly && (stock.volumeRatio ?? 0) < 1) return false;
      if (this.etfsOnly && stock.type !== 'ETF') return false;
      if (this.selectedSetup === 'ACTIONABLE') return this.isActionable(stock);
      if (this.selectedSetup !== 'ALL' && stock.setup !== this.selectedSetup) return false;
      return true;
    });

    this.filteredRows = filtered;
    this.pageIndex = 0;
    this.renderPage();
    this.cdr.markForCheck();
  }

  onSort(sort: Sort): void {
    this.currentSort = sort.direction ? sort : { active: 'setupScore', direction: 'desc' };
    this.pageIndex = 0;
    this.renderPage();
  }

  changePage(delta: number): void {
    this.pageIndex = Math.min(this.totalPages - 1, Math.max(0, this.pageIndex + delta));
    this.renderPage();
  }

  changePageSize(): void {
    this.pageIndex = 0;
    this.renderPage();
  }

  resetFilters(): void {
    this.symbolFilter = '';
    this.minimumPrice = 6;
    this.minimumDollarVolumeMillions = 5;
    this.minimumScore = 50;
    this.freshOnly = true;
    this.participatingVolumeOnly = false;
    this.etfsOnly = false;
    this.applyFilters();
  }

  /**
   * Refresh the upcoming-earnings calendar, then reload the table so the
   * days-to-earnings column reflects what was just written.
   */
  runEarningsScan(): void {
    if (this.earningsRunning) return;
    this.earningsRunning = true;
    this.earningsStatus = 'idle';
    this.earningsMessage = 'Checking the upcoming-earnings calendar…';
    this.earningsMessageAt = new Date();
    this.cdr.markForCheck();

    this.stockService.runEarningsScan()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(res => {
        this.earningsRunning = false;
        this.earningsMessageAt = new Date();
        const covered = res?.symbolsWithUpcomingEarnings;
        const total = res?.scannerSymbols;
        const coverage = covered != null && total != null
          ? `${covered} of ${total} scanner symbols have upcoming earnings`
          : null;
        if (res && res.status === 'ok') {
          this.earningsStatus = 'ok';
          const through = res.eventsCoverageEnd ? ` through ${res.eventsCoverageEnd}` : '';
          this.earningsMessage = `✓ Earnings calendar ready${through}`
            + (coverage ? `. ${coverage}.` : '.');
          this.loadStocks();
        } else {
          this.earningsStatus = 'error';
          // A failed refresh keeps the previous calendar, so say what the table
          // is still showing rather than implying the column is empty.
          const reason = res?.message || res?.output || 'see server logs';
          this.earningsMessage = `✗ Earnings refresh failed: ${reason}`
            + (coverage ? ` Showing the previous calendar: ${coverage}.` : '');
        }
        this.cdr.markForCheck();
      });
  }

  earningsStatusClass(): string {
    if (this.earningsStatus === 'ok') return 'job-ok';
    if (this.earningsStatus === 'error') return 'job-error';
    return 'job-running';
  }

  openDetails(stock: MomentumStock): void {
    this.selectedStock = stock;
    this.cdr.markForCheck();
  }

  closeDetails(): void {
    this.selectedStock = null;
    this.cdr.markForCheck();
  }

  setupLabel(setup?: MomentumSetup): string {
    switch (setup) {
      case 'BULLISH_CONTINUATION': return 'Bullish';
      case 'BEARISH_CONTINUATION': return 'Bearish';
      case 'BULLISH_REVERSAL': return 'Bullish Reversal';
      case 'BEARISH_REVERSAL': return 'Bearish Reversal';
      case 'NOT_EVALUATED': return 'Not Evaluated';
      default: return 'Watch';
    }
  }

  setupIcon(setup?: MomentumSetup): string {
    switch (setup) {
      case 'BULLISH_CONTINUATION': return '↑';
      case 'BEARISH_CONTINUATION': return '↓';
      case 'BULLISH_REVERSAL': return '↘';
      case 'BEARISH_REVERSAL': return '↗';
      default: return '—';
    }
  }

  rowClass(stock: MomentumStock): string {
    const setupClass = `row-${(stock.setup || 'watch').toLowerCase().replaceAll('_', '-')}`;
    return stock.freshnessStatus === 'FRESH' ? setupClass : `${setupClass} row-stale`;
  }

  scoreEntries(stock: MomentumStock): Array<{ key: string; label: string; value: number; width: number }> {
    const maximums: Record<string, number> = {
      trendAlignment: 25,
      multiHorizonMomentum: 20,
      relativeStrength: 15,
      participation: 15,
      entryTiming: 15,
      tradability: 10,
      bearishContext: 20,
      bullishContext: 20,
      bullishTrigger: 30,
      bearishTrigger: 30,
      priceConfirmation: 25,
      riskAndRoom: 10,
      preliminaryReversalPenalty: 20
    };
    return Object.entries(stock.setupScoreComponents ?? {}).map(([key, value]) => ({
      key,
      label: this.componentLabel(key),
      value,
      width: Math.min(100, Math.abs(value) / (maximums[key] || 100) * 100)
    }));
  }

  componentLabel(key: string): string {
    const labels: Record<string, string> = {
      trendAlignment: 'Trend alignment',
      multiHorizonMomentum: 'Multi-horizon momentum',
      relativeStrength: 'Relative strength',
      participation: 'Participation',
      entryTiming: 'Entry timing',
      tradability: 'Tradability',
      bearishContext: 'Bearish context',
      bullishContext: 'Bullish context',
      bullishTrigger: 'Bullish trigger',
      bearishTrigger: 'Bearish trigger',
      priceConfirmation: 'Price confirmation',
      riskAndRoom: 'Risk and room',
      preliminaryReversalPenalty: 'Preliminary reversal penalty'
    };
    return labels[key] || key;
  }

  metricClass(value: number | null | undefined): string {
    if (value == null) return '';
    return value > 0 ? 'positive' : value < 0 ? 'negative' : '';
  }

  rsiArrow(stock: MomentumStock): string {
    const change = stock.rsiChangeFiveDay;
    if (change == null || Math.abs(change) < 0.5) return '→';
    return change > 0 ? '↑' : '↓';
  }

  formatDate(value: Date | string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
    }).format(parsed);
  }

  formatHeaderDate(value: Date | null): string {
    if (!value) return '';
    return new Intl.DateTimeFormat('en-US', {
      month: '2-digit', day: '2-digit', timeZone: 'UTC'
    }).format(value);
  }

  formatCompact(value: number | null | undefined): string {
    if (value == null) return '—';
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
  }

  formatRangePrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1
    }).format(value);
  }

  hasFullWeek(week: MomentumStock['recentWeeks'][number]): boolean {
    return (week.sessionCount ?? 0) >= MomentumScannerComponent.FULL_WEEK_SESSIONS;
  }

  /** How the week's return volatility was computed, and what weakens it. */
  returnVolatilityTooltip(week: MomentumStock['recentWeeks'][number]): string {
    const full = MomentumScannerComponent.FULL_WEEK_SESSIONS;
    const sessions = week.sessionCount ?? 0;
    const base = 'Return Volatility: the standard deviation of this week\'s day-to-day '
      + 'closing-price changes, divided by the week\'s average close, shown as a percent. '
      + 'It measures how much the daily moves varied within the week, not their direction, '
      + 'and it is computed only from this week\'s cached sessions.';
    if (sessions === 0) {
      return `${base} No cached sessions for this week, so there is nothing to measure.`;
    }
    if (sessions < full) {
      return `${base} Incomplete: based on ${sessions} of ${full} sessions`
        + `${sessions === 1 ? ' — a single session has no day-to-day change, so it reads 0.00%' : ''}`
        + `, which is ${sessions - 1} daily change${sessions - 1 === 1 ? '' : 's'} instead of ${full - 1}. `
        + 'A holiday-shortened or partially cached week makes this figure less reliable.';
    }
    return `${base} Based on all ${full} sessions of the week.`;
  }

  trendAgeTooltip(stock: MomentumStock): string {
    const days = stock.advancedTrendWithVolume?.currentTrendDays;
    if (days == null) return 'No trend age: the trend engine has insufficient history.';
    return `Elapsed, not remaining: the current ${stock.advancedTrendWithVolume?.direction?.toLowerCase() || ''} `
      + `trend has been in place for ${days} session${days === 1 ? '' : 's'} through the latest cached close. `
      + 'It says nothing about how much longer it will last.';
  }

  earningsSoon(stock: MomentumStock): boolean {
    const days = stock.daysToEarnings;
    return days != null && days <= MomentumScannerComponent.EARNINGS_SOON_DAYS;
  }

  earningsTooltip(stock: MomentumStock): string {
    const days = stock.daysToEarnings;
    if (days == null) {
      return 'No upcoming earnings date for this symbol in the cached Finnhub calendar. '
        + 'That can mean no report is scheduled inside the fetched window, or that the '
        + 'calendar has not been refreshed — run Earnings Scan to refresh it.';
    }
    const when = days === 0 ? 'today'
      : days === 1 ? 'tomorrow'
      : `in ${days} calendar days`;
    return `Reports ${when}, on ${this.formatDate(stock.nextEarningsDate)} `
      + '(nearest upcoming date in the cached Finnhub calendar). '
      + 'An earnings report can break a momentum setup regardless of its score.';
  }

  fiftyTwoWeekRangeTooltip(stock: MomentumStock): string {
    const { fiftyTwoWeekLow: low, fiftyTwoWeekHigh: high, fiftyTwoWeekPosition: position } = stock;
    if (low == null || high == null || position == null) {
      return '52-week range unavailable: fewer than 252 usable cached daily bars, '
        + 'likely because it is newly listed.';
    }
    return `Cached 52-week range: ${this.formatRangePrice(low)} low to ${this.formatRangePrice(high)} high. `
      + `Latest cached close is ${position.toFixed(0)}% of the way from low to high.`;
  }

  private isActionable(stock: MomentumStock): boolean {
    if (stock.preliminaryReversal) return false;
    return stock.setup === 'BULLISH_CONTINUATION'
      || stock.setup === 'BEARISH_CONTINUATION'
      || stock.setup === 'BULLISH_REVERSAL'
      || stock.setup === 'BEARISH_REVERSAL';
  }

  private updateSnapshotMetadata(): void {
    this.dataAsOf = this.modeDate(this.allStocks.map(stock => stock.lastTradeStats?.tradeDate));
    this.fiveDayAnchor = this.modeDate(this.allStocks.map(stock => stock.fiveDaysToDate?.startDate));
    this.fiveWeekAnchor = this.modeDate(this.allStocks.map(stock => stock.fiveWeeksToDate?.startDate));
    this.staleCount = this.allStocks.filter(stock => stock.freshnessStatus !== 'FRESH').length;
  }

  private modeDate(values: Array<Date | string | undefined>): Date | null {
    const counts = new Map<string, number>();
    for (const value of values) {
      if (!value) continue;
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) continue;
      const key = parsed.toISOString().slice(0, 10);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    let best: [string, number] | null = null;
    for (const entry of counts.entries()) {
      if (!best || entry[1] > best[1] || (entry[1] === best[1] && entry[0] > best[0])) best = entry;
    }
    return best ? new Date(`${best[0]}T00:00:00Z`) : null;
  }

  private sortRows(rows: MomentumStock[], sort: Sort): MomentumStock[] {
    if (!sort.active || !sort.direction) return rows.slice();
    const direction = sort.direction === 'asc' ? 1 : -1;
    const setupOrder: Record<string, number> = {
      BULLISH_CONTINUATION: 0,
      BEARISH_CONTINUATION: 1,
      BULLISH_REVERSAL: 2,
      BEARISH_REVERSAL: 3,
      WATCH: 4,
      NOT_EVALUATED: 5
    };
    return rows.slice().sort((left, right) => {
      if ((this.selectedSetup === 'ALL' || this.selectedSetup === 'ACTIONABLE')
          && sort.active === 'setupScore') {
        const setupCompare = (setupOrder[left.setup || ''] ?? 99) - (setupOrder[right.setup || ''] ?? 99);
        if (setupCompare) return setupCompare;
      }
      const a = this.sortValue(left, sort.active);
      const b = this.sortValue(right, sort.active);
      if (a == null && b == null) return left.code.localeCompare(right.code);
      if (a == null) return 1;
      if (b == null) return -1;
      const compared = typeof a === 'string' || typeof b === 'string'
        ? String(a).localeCompare(String(b))
        : Number(a) - Number(b);
      return compared === 0 ? left.code.localeCompare(right.code) : compared * direction;
    });
  }

  private renderPage(): void {
    const sorted = this.sortRows(this.filteredRows, this.currentSort);
    const start = this.pageIndex * this.pageSize;
    this.visibleRows = sorted.slice(start, start + this.pageSize);
    this.cdr.markForCheck();
  }

  private sortValue(stock: MomentumStock, property: string): string | number | null {
    switch (property) {
      case 'setup': return stock.setup || '';
      case 'symbol': return stock.code;
      case 'price': return stock.lastTradeStats?.close ?? null;
      case 'fiftyTwoWeekRange': return stock.fiftyTwoWeekPosition ?? null;
      case 'setupScore': return stock.setupScore ?? null;
      case 'fiveDay': return stock.fiveDaysToDate?.gainLoss ?? null;
      case 'fiveWeek': return stock.fiveWeeksToDate?.gainLoss ?? null;
      case 'relativeStrength': return stock.relativeStrengthSpyOneMonth ?? null;
      case 'volumeRatio': return stock.volumeRatio ?? null;
      case 'rsi': return stock.advancedTrendWithVolume?.rsi ?? null;
      case 'atrPct': return stock.atrPct ?? null;
      // The column renders trend age, so it sorts by trend age. It previously
      // sorted by MACD cross age, which is a different number entirely.
      case 'trigger': return stock.advancedTrendWithVolume?.currentTrendDays ?? null;
      case 'daysToEarnings': return stock.daysToEarnings ?? null;
      case 'freshness': return stock.freshnessStatus || '';
      default: return null;
    }
  }
}
