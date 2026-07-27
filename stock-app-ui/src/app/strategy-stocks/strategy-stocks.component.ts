import { Component, Input, OnInit, inject, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { MatTableModule, MatTableDataSource } from '@angular/material/table';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSortModule, MatSort, MatSortable } from '@angular/material/sort';
import { MatTooltipModule } from '@angular/material/tooltip';
import { FormsModule } from '@angular/forms';
import { SymbolFilterService } from '../services/symbol-filter.service';
import { DrawerComponent } from '../shared/ui/drawer.component';
import { Observable, of } from 'rxjs';
import { StrategyReport, StrategyStock } from '../model/stock';

interface ScoreEntry {
  label: string;
  value: number;
  cap: number;
  width: number;
}

@Component({
  selector: 'app-strategy-stocks',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatTableModule,
    MatFormFieldModule,
    MatInputModule,
    MatSortModule,
    MatTooltipModule,
    FormsModule,
    DrawerComponent
  ],
  templateUrl: './strategy-stocks.component.html',
  styleUrls: ['./strategy-stocks.component.css']
})
export class StrategyStocksComponent implements OnInit {
  @Input() set candidates(value: StrategyStock[] | null) {
    if (value == null) return;
    this.allCandidates = value;
    this.applyFilters();
  }
  private allCandidates: StrategyStock[] = [];
  // The table lives behind an *ngIf (async), so the MatSort directive doesn't
  // exist until the data arrives and the table renders. A plain @ViewChild
  // resolves to undefined in ngAfterViewInit and sorting never attaches. A
  // setter re-runs whenever the directive appears, so we wire it up then.
  private sort?: MatSort;

  @ViewChild(MatSort) set matSort(s: MatSort | undefined) {
    this.sort = s;
    if (s) {
      this.dataSource.sort = s;
      this.dataSource.sortingDataAccessor = (item, property) => this.sortingAccessor(item, property);
      if (!s.active) {
        s.sort({ id: 'scoreTotal', start: 'desc', disableClear: false } as MatSortable);
      }
    }
  }

  private symbolFilterService = inject(SymbolFilterService);

  strategyStocks$: Observable<StrategyStock[]> = of([]);
  dataSource = new MatTableDataSource<StrategyStock>([]);
  symbolFilter: string = '';
  etfsOnly = false;
  selectedStock: StrategyStock | null = null;

  readonly skeletonRows = Array.from({ length: 8 });

  // Decision-relevant set only. Everything else — the score decomposition,
  // timing diagnostics and the full reason text — lives in the details drawer.
  displayedColumns: string[] = [
    'symbol',
    'price',
    'fiftyTwoWeekRange',
    'signalBand',
    'scoreTotal',
    'scorePct',
    'shiftLabel',
    'trendSignal',
    'relStrengthSpy',
    'atrPct',
    'event',
    'sector',
    'details'
  ];

  /** Component caps, from studies/pre_earnings_momentum/scoring.py. */
  private readonly scoreCaps: ReadonlyArray<{ label: string; cap: number; key: keyof StrategyReport & string }> = [
    { label: 'Trend', cap: 25, key: 'scoreTrend' },
    { label: 'Momentum', cap: 20, key: 'scoreMomentum' },
    { label: 'Extension', cap: 10, key: 'scoreExtension' },
    { label: 'Event', cap: 30, key: 'scoreEvent' },
    { label: 'Tradability', cap: 10, key: 'scoreTradability' },
    { label: 'Persistence', cap: 5, key: 'scorePersistence' }
  ];

  ngOnInit(): void {
    this.symbolFilter = this.symbolFilterService.getFilter();
    this.applyFilters();
  }

  openDetails(stock: StrategyStock): void {
    this.selectedStock = stock;
  }

  closeDetails(): void {
    this.selectedStock = null;
  }

  /** Score decomposition bars for the drawer, each against its own cap. */
  scoreEntries(stock: StrategyStock): ScoreEntry[] {
    const report = stock.strategyReport;
    if (!report) {
      return [];
    }
    return this.scoreCaps.map(({ label, cap, key }) => {
      const value = (report as unknown as Record<string, number>)[key] ?? 0;
      return {
        label,
        value,
        cap,
        width: Math.max(0, Math.min(100, (value / cap) * 100))
      };
    });
  }

  get marketRegime(): string {
    return this.dataSource.data[0]?.strategyReport?.marketRegime ?? '';
  }

  get regimeSizeFactor(): number {
    return this.dataSource.data[0]?.strategyReport?.regimeSizeFactor ?? 1;
  }

  regimeBannerClass(): string {
    const r = this.marketRegime;
    if (r === 'Risk-Off') return 'banner-neg';
    if (r === 'Neutral') return 'banner-warn';
    if (r === 'Risk-On') return 'banner-pos';
    return 'banner-warn';
  }

  regimeBadgeClass(): string {
    const r = this.marketRegime;
    if (r === 'Risk-Off') return 'badge-neg';
    if (r === 'Neutral') return 'badge-warn';
    if (r === 'Risk-On') return 'badge-pos';
    return 'badge-warn';
  }

  regimeIcon(): string {
    const r = this.marketRegime;
    if (r === 'Risk-On') return '✓';
    if (r === 'Neutral') return '•';
    return '⚠';
  }

  bandBadgeClass(band: string | undefined): string {
    switch (band) {
      case 'Super High': return 'badge-pos band-super-high';
      case 'High': return 'badge-pos';
      case 'Medium': return 'badge-warn';
      case 'Low': return 'badge-neutral';
      default: return 'badge-neutral';
    }
  }

  shiftBadgeClass(label: string | undefined): string {
    const key = this.shiftClass(label);
    if (key === 'shift-fresh-confirmed') return 'badge-pos';
    if (key === 'shift-developing') return 'badge-warn';
    return 'badge-neutral';
  }

  shiftClass(label: string | undefined): string {
    if (!label) {
      return '';
    }
    return 'shift-' + label.toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '');
  }

  /** `2026-08-18` → `Aug 18`, parsed locally so the date never slips a day. */
  formatEventDate(value: string | undefined): string {
    if (!value) {
      return '';
    }
    const [year, month, day] = value.split('-').map(Number);
    if (!year || !month || !day) {
      return value;
    }
    return new Date(year, month - 1, day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  formatRangePrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1
    }).format(value);
  }

  fiftyTwoWeekRangeTooltip(stock: StrategyStock): string {
    const { fiftyTwoWeekLow: low, fiftyTwoWeekHigh: high, fiftyTwoWeekPosition: position } = stock;
    if (low == null || high == null || position == null) {
      return '52-week range unavailable: fewer than 252 usable cached daily bars.';
    }
    return `Cached 52-week range: ${this.formatRangePrice(low)} low to ${this.formatRangePrice(high)} high. `
      + `Latest cached close is ${position.toFixed(0)}% of the way from low to high.`;
  }

  applySymbolFilter(): void {
    this.symbolFilterService.setFilter(this.symbolFilter);
    this.applyFilters();
  }

  clearSymbolFilter(): void {
    this.symbolFilter = '';
    this.symbolFilterService.clearFilter();
    this.applyFilters();
  }

  applyEtfFilter(): void {
    this.applyFilters();
  }

  private applyFilters(): void {
    const stocks = this.allCandidates.filter(stock =>
      (!this.symbolFilter.trim() || this.symbolFilterService.matchesFilter(stock.code))
      && (!this.etfsOnly || stock.type === 'ETF')
    );
    this.strategyStocks$ = of(stocks);
    this.dataSource.data = stocks;
    if (this.sort) {
      this.dataSource.sort = this.sort;
      this.dataSource.sortingDataAccessor = (item, property) => this.sortingAccessor(item, property);
    }
  }

  private sortingAccessor(item: StrategyStock, property: string): string | number {
    switch (property) {
      case 'symbol':
        return item.code;
      case 'price':
        return item.lastTradeStats?.close ?? 0;
      case 'fiftyTwoWeekRange':
        return item.fiftyTwoWeekPosition ?? -Infinity;
      case 'sector':
        return item.strategyReport?.sector ?? '';
      case 'trendSignal':
        return item.signal ?? '';
      case 'atrPct':
        return item.strategyReport?.atrPct ?? -Infinity;
      case 'relStrengthSpy':
        return item.strategyReport?.relStrengthSpy ?? -Infinity;
      case 'signalBand':
        return item.strategyReport?.signalBand ?? '';
      case 'scorePct':
        return item.strategyReport?.scorePct ?? -Infinity;
      case 'scoreTotal':
        return item.strategyReport?.scoreTotal ?? -Infinity;
      case 'shiftLabel':
        return item.strategyReport?.shiftLabel ?? '';
      // The Event column merges type, date and days-to-event; days is what a
      // reader actually sorts by.
      case 'event':
      default:
        return '';
    }
  }
}
