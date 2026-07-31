import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import { StockService } from '../../api/stock.service';
import { BrokerageId, HoldingItem, HoldingsResponse } from '../../model/brokerage';
import { StockRange } from '../../model/stock';
import {
  formatFixedPercent,
  formatIsoTimestamp,
  formatQuantity,
  formatUsdMoney,
  pnlToneClass,
} from '../format-display';
import { ModalComponent } from '../ui/modal.component';
import { DrawerComponent } from '../ui/drawer.component';

/** Columns a user can sort by; the rest are display-only. */
type SortColumn =
  | 'symbol' | 'category' | 'account' | 'industry' | 'quantity' | 'cost_per_unit'
  | 'mark_per_unit' | 'cost_basis' | 'market_value' | 'pct_of_total'
  | 'unrealized_pnl' | 'unrealized_pnl_pct';

@Component({
  selector: 'app-brokerage-holdings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, MatTooltipModule, ModalComponent, DrawerComponent],
  templateUrl: './brokerage-holdings.component.html',
  styleUrl: './brokerage-holdings.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageHoldingsComponent implements OnChanges {
  @Input({ required: true }) brokerageId!: BrokerageId;
  @Input() refreshToken = 0;
  @Output() countChange = new EventEmitter<number>();

  data: HoldingsResponse | null = null;
  loading = false;
  snapshotting = false;
  saving = false;
  savingBaselines = false;
  baselinesDrawerOpen = false;
  error = '';
  message = '';
  editError = '';
  search = '';
  category = '';
  account = '';
  decliningOnly = false;
  copySuccess = false;
  editing: {
    symbol: string;
    accountId: string;
    quantity: number;
    category: string;
    industry: string;
    note: string;
    basisEditable: boolean;
    basisMode: 'TOTAL' | 'PER_UNIT' | null;
    costBasis: number | null;
    costPerUnit: number | null;
  } | null = null;
  baselines: {
    totalContributions: number | null;
    yearBeginningBalance: number | null;
    baselineYear: number | null;
  } = {
    totalContributions: null,
    yearBeginningBalance: null,
    baselineYear: null,
  };
  baselinesError = '';
  sortColumn: SortColumn = 'market_value';
  sortAscending = false;

  private requestSequence = 0;
  private rangeRequestSequence = 0;
  private stockRanges = new Map<string, StockRange>();
  private copyTimer?: ReturnType<typeof setTimeout>;
  private messageTimer?: ReturnType<typeof setTimeout>;

  constructor(
    private readonly api: BrokerageService,
    private readonly stockService: StockService,
  ) {}

  ngOnChanges(): void {
    if (this.brokerageId) {
      this.load();
      this.loadStockRanges();
    }
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getHoldings(this.brokerageId).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.syncBaselinesForm(data);
        this.countChange.emit(data.items.length);
        this.loading = false;
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.countChange.emit(0);
        this.loading = false;
        this.error = this.message_(err, 'The holdings view could not be loaded.');
      },
    });
  }

  private loadStockRanges(): void {
    const request = ++this.rangeRequestSequence;
    this.stockService.getStockRanges().subscribe({
      next: ranges => {
        if (request !== this.rangeRequestSequence) return;
        this.stockRanges = new Map(ranges.map(range => [range.code, range]));
      },
      error: () => {
        if (request === this.rangeRequestSequence) this.stockRanges = new Map();
      },
    });
  }

  rangeFor(symbol: string): StockRange | null {
    return this.stockRanges.get(symbol) ?? null;
  }

  isUniverseSymbol(symbol: string): boolean {
    return this.stockRanges.has(symbol);
  }

  rangePrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1,
    }).format(value);
  }

  rangeTooltip(range: StockRange): string {
    if (range.fiftyTwoWeekLow == null || range.fiftyTwoWeekHigh == null
      || range.fiftyTwoWeekPosition == null) {
      return '52-week range unavailable: fewer than 252 usable cached daily bars.';
    }
    return `Cached 52-week range: ${this.rangePrice(range.fiftyTwoWeekLow)} low to `
      + `${this.rangePrice(range.fiftyTwoWeekHigh)} high. Latest cached close is `
      + `${range.fiftyTwoWeekPosition.toFixed(0)}% of the way from low to high.`;
  }

  get items(): HoldingItem[] {
    return this.data?.items ?? [];
  }

  filteredHoldings(): HoldingItem[] {
    const query = this.search.trim().toUpperCase();
    const rows = this.items.filter(row => {
      if (this.category && row.category !== this.category) return false;
      if (this.account && row.account !== this.account) return false;
      if (this.decliningOnly && !row.trend.alert) return false;
      return !query || `${row.symbol} ${row.industry} ${row.note}`.toUpperCase().includes(query);
    });
    return [...rows].sort((left, right) => {
      const a = left[this.sortColumn];
      const b = right[this.sortColumn];
      if (typeof a === 'string' && typeof b === 'string') {
        return this.sortAscending ? a.localeCompare(b) : b.localeCompare(a);
      }
      const av = typeof a === 'number' ? a : Number.NEGATIVE_INFINITY;
      const bv = typeof b === 'number' ? b : Number.NEGATIVE_INFINITY;
      return this.sortAscending ? av - bv : bv - av;
    });
  }

  /** Aggregates for the currently filtered rows. Fail closed on missing inputs. */
  filteredTotals(): {
    cost_basis: number | null;
    market_value: number | null;
    pct_of_total: number | null;
    unrealized_pnl: number | null;
    unrealized_pnl_pct: number | null;
  } {
    const rows = this.filteredHoldings();
    const cost_basis = this.sumExact(rows.map(row => row.cost_basis));
    const market_value = this.sumExact(rows.map(row => row.market_value));
    const portfolioTotal = this.data?.summary.total_market_value ?? null;
    const unrealized_pnl = (
      cost_basis == null || market_value == null
        ? null
        : market_value - cost_basis
    );
    return {
      cost_basis,
      market_value,
      pct_of_total: (
        market_value == null || portfolioTotal == null || portfolioTotal === 0
          ? null
          : (market_value / portfolioTotal) * 100
      ),
      unrealized_pnl,
      unrealized_pnl_pct: (
        unrealized_pnl == null || cost_basis == null || cost_basis === 0
          ? null
          : (unrealized_pnl / cost_basis) * 100
      ),
    };
  }

  private sumExact(values: Array<number | null>): number | null {
    if (values.some(value => value == null)) return null;
    return values.reduce((total: number, value) => total + (value as number), 0);
  }

  categories(): string[] {
    return [...new Set(this.items.map(row => row.category).filter(Boolean))].sort();
  }

  accounts(): string[] {
    return [...new Set(this.items.map(row => row.account).filter(Boolean))].sort();
  }

  industries(): string[] {
    return [...new Set(this.items.map(row => row.industry)
      .filter(value => value && value !== 'UNCLASSIFIED'))].sort();
  }

  decliningCount(): number {
    return this.items.filter(row => row.trend.alert).length;
  }

  missingCostBasisCount(): number {
    return this.items.filter(row => row.cost_basis == null).length;
  }

  /** The retained capture dates, one comparison column each. */
  snapshotDates(): { sync_date: string }[] {
    return this.data?.summary.gain_loss_snapshots ?? [];
  }

  resetFilters(): void {
    this.search = '';
    this.category = '';
    this.account = '';
    this.decliningOnly = false;
  }

  sortBy(column: SortColumn): void {
    if (this.sortColumn === column) this.sortAscending = !this.sortAscending;
    else {
      this.sortColumn = column;
      this.sortAscending = ['symbol', 'category', 'account', 'industry'].includes(column);
    }
  }

  ariaSort(column: SortColumn): 'ascending' | 'descending' | 'none' {
    if (this.sortColumn !== column) return 'none';
    return this.sortAscending ? 'ascending' : 'descending';
  }

  sortIcon(column: SortColumn): string {
    if (this.sortColumn !== column) return '';
    return this.sortAscending ? '▲' : '▼';
  }

  captureSnapshot(): void {
    this.snapshotting = true;
    this.error = '';
    this.api.captureGainLossSnapshot(this.brokerageId).subscribe({
      next: response => {
        this.snapshotting = false;
        const verb = response.replaced ? 'replaced' : 'saved';
        this.flash(`G/L snapshot for ${this.snapshotDateLabel(response.sync_date)} ${verb}.`);
        this.load();
      },
      error: err => {
        this.snapshotting = false;
        this.error = this.message_(err, 'The G/L snapshot could not be saved.');
      },
    });
  }

  saveBaselines(): void {
    const totalContributions = this.validCost(this.baselines.totalContributions);
    const yearBeginningBalance = this.validCost(this.baselines.yearBeginningBalance);
    if (totalContributions == null && yearBeginningBalance == null) {
      this.baselinesError = 'Enter at least one baseline amount.';
      return;
    }
    this.savingBaselines = true;
    this.baselinesError = '';
    this.api.updateHoldingsSettings(this.brokerageId, {
      total_contributions: totalContributions,
      year_beginning_balance: yearBeginningBalance,
      baseline_year: yearBeginningBalance == null
        ? null
        : (this.baselines.baselineYear ?? this.currentYear()),
    }).subscribe({
      next: () => {
        this.savingBaselines = false;
        this.baselinesDrawerOpen = false;
        this.flash('Performance baselines saved.');
        this.load();
      },
      error: err => {
        this.savingBaselines = false;
        this.baselinesError = this.message_(
          err, 'The performance baselines could not be saved.',
        );
      },
    });
  }

  openBaselinesDrawer(): void {
    if (this.data) this.syncBaselinesForm(this.data);
    this.baselinesError = '';
    this.baselinesDrawerOpen = true;
  }

  closeBaselinesDrawer(): void {
    this.baselinesDrawerOpen = false;
    if (this.data) this.syncBaselinesForm(this.data);
    this.baselinesError = '';
  }

  clearBaselines(): void {
    this.baselines = {
      totalContributions: null,
      yearBeginningBalance: null,
      baselineYear: this.currentYear(),
    };
    this.savingBaselines = true;
    this.baselinesError = '';
    this.api.updateHoldingsSettings(this.brokerageId, {
      total_contributions: null,
      year_beginning_balance: null,
      baseline_year: null,
    }).subscribe({
      next: () => {
        this.savingBaselines = false;
        this.flash('Performance baselines cleared.');
        this.load();
      },
      error: err => {
        this.savingBaselines = false;
        this.baselinesError = this.message_(
          err, 'The performance baselines could not be cleared.',
        );
      },
    });
  }

  baselineYearLabel(): string {
    return String(
      this.data?.summary.performance_baselines.baseline_year ?? this.currentYear()
    );
  }

  currentYear(): number {
    return new Date().getFullYear();
  }

  private syncBaselinesForm(data: HoldingsResponse): void {
    const baselines = data.summary.performance_baselines;
    this.baselines = {
      totalContributions: baselines.total_contributions,
      yearBeginningBalance: baselines.year_beginning_balance,
      baselineYear: baselines.baseline_year ?? this.currentYear(),
    };
  }

  copySymbols(): void {
    const seen = new Set<string>();
    const symbols = this.filteredHoldings()
      .filter(row => row.category !== 'CASH' && row.category !== 'FUND' && row.symbol)
      .map(row => row.symbol)
      .filter(symbol => !seen.has(symbol) && !!seen.add(symbol))
      .join(' ');
    navigator.clipboard.writeText(symbols).then(() => {
      this.copySuccess = true;
      clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => (this.copySuccess = false), 2000);
    });
  }

  openEditor(row: HoldingItem): void {
    this.editing = {
      symbol: row.symbol,
      accountId: row.account_id,
      quantity: row.quantity,
      category: row.category === 'UNCLASSIFIED' ? '' : row.category,
      industry: row.industry === 'UNCLASSIFIED' ? '' : row.industry,
      note: row.note,
      basisEditable: row.cost_basis_source !== 'BROKER',
      basisMode: row.cost_basis_override_mode,
      costBasis: row.cost_basis,
      costPerUnit: row.cost_per_unit,
    };
    this.editError = '';
  }

  closeEditor(): void {
    this.editing = null;
    this.editError = '';
  }

  saveEnrichment(): void {
    if (!this.editing) return;
    const {
      symbol, accountId, category, industry, note, basisEditable, basisMode,
      costBasis, costPerUnit,
    } = this.editing;
    const payload: {
      category: string;
      industry: string;
      note: string;
      account_id?: string;
      cost_basis?: number | null;
      cost_per_unit?: number | null;
    } = { category, industry, note };
    if (basisEditable) {
      payload.account_id = accountId;
      payload.cost_basis = basisMode === 'TOTAL' ? costBasis : null;
      payload.cost_per_unit = basisMode === 'PER_UNIT' ? costPerUnit : null;
    }
    this.saving = true;
    this.editError = '';
    this.api.updateHoldingsMetadata(this.brokerageId, symbol, payload)
      .subscribe({
        next: () => {
          this.saving = false;
          this.editing = null;
          this.load();
        },
        error: err => {
          this.saving = false;
          this.editError = this.message_(err, 'The holding changes could not be saved.');
        },
      });
  }

  updateCostBasis(value: number | null): void {
    if (!this.editing) return;
    this.editing.costBasis = this.validCost(value);
    this.editing.basisMode = this.editing.costBasis == null ? null : 'TOTAL';
    this.editing.costPerUnit = (
      this.editing.costBasis == null || !this.editing.quantity
        ? null : this.editing.costBasis / this.editing.quantity
    );
  }

  updateCostPerUnit(value: number | null): void {
    if (!this.editing) return;
    this.editing.costPerUnit = this.validCost(value);
    this.editing.basisMode = this.editing.costPerUnit == null ? null : 'PER_UNIT';
    this.editing.costBasis = (
      this.editing.costPerUnit == null
        ? null : this.editing.costPerUnit * this.editing.quantity
    );
  }

  clearCostBasis(): void {
    if (!this.editing) return;
    this.editing.costBasis = null;
    this.editing.costPerUnit = null;
    this.editing.basisMode = null;
  }

  private validCost(value: number | null): number | null {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
      ? value : null;
  }

  snapshotDateLabel(value: string): string {
    const parsed = new Date(`${value}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    });
  }

  capturedPct(row: HoldingItem, date: string): number | null {
    const value = row.gain_loss_snapshots[date];
    return value == null ? null : value;
  }

  trendTooltip(row: HoldingItem): string {
    const trend = row.trend;
    if (!trend.alert || trend.from_pct == null || trend.to_pct == null || trend.drop_pct == null) {
      return '';
    }
    const signed = (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
    const when = trend.alert_at ? ` (${new Date(trend.alert_at).toLocaleDateString()})` : '';
    return trend.direction === 'LOSS'
      ? `Loss deepened ${trend.drop_pct.toFixed(0)}%: ${signed(trend.from_pct)} → ${signed(trend.to_pct)}${when}.`
      : `Gain shrank ${trend.drop_pct.toFixed(0)}%: ${signed(trend.from_pct)} → ${signed(trend.to_pct)}${when}.`;
  }

  money(value: number | null | undefined, signed = false): string {
    return formatUsdMoney(value, signed);
  }

  percent(value: number | null | undefined, signed = false): string {
    return formatFixedPercent(value, signed);
  }

  quantity(value: number | null | undefined): string {
    return formatQuantity(value);
  }

  pnlClass(value: number | null | undefined): string {
    return pnlToneClass(value);
  }

  timestamp(value: string | null | undefined): string {
    return formatIsoTimestamp(value);
  }

  private flash(message: string): void {
    this.message = message;
    clearTimeout(this.messageTimer);
    this.messageTimer = setTimeout(() => (this.message = ''), 6000);
  }

  /** Common errors carry a safe code and message; fall back to the default. */
  private message_(err: unknown, fallback: string): string {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message);
    }
    return typeof detail === 'string' ? detail : fallback;
  }
}
