import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  inject
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ModalComponent } from '../shared/ui/modal.component';
import { TrackedStockService } from '../api/tracked-stock.service';
import {
  SymbolChip,
  TRACKED_STOCK_CATEGORIES,
  TRACKED_STOCK_CATEGORY_READY,
  TRACKED_STOCK_CATEGORY_SOLD,
  TrackedStockCategory,
  TrackedStockCategoryFilter,
  TrackedStockListResponse,
  TrackedStockRow
} from '../model/tracked-stock';
import { MomentumSetup } from '../model/stock';

type SortKey =
  | 'symbol'
  | 'coverage_initiation_date'
  | 'setup_score'
  | 'coverage_vs_spy'
  | 'ytd_vs_spy'
  | 'target_date'
  | 'target_amount';

class SymbolEntry {
  raw = '';
  chips: SymbolChip[] = [];

  get symbols(): string[] {
    return this.chips.map(chip => chip.symbol);
  }

  get unknown(): string[] {
    return this.chips.filter(chip => !chip.known).map(chip => chip.symbol);
  }

  get valid(): boolean {
    return this.chips.length > 0 && this.unknown.length === 0;
  }

  clear(): void {
    this.raw = '';
    this.chips = [];
  }
}

@Component({
  selector: 'app-tracking-sold-stocks',
  standalone: true,
  imports: [FormsModule, RouterLink, MatTooltipModule, ModalComponent],
  templateUrl: './tracking-sold-stocks.component.html',
  styleUrl: './tracking-sold-stocks.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class TrackingSoldStocksComponent {
  private readonly api = inject(TrackedStockService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);

  readonly readyCategory = TRACKED_STOCK_CATEGORY_READY;

  snapshot: TrackedStockListResponse | null = null;
  allRows: TrackedStockRow[] = [];
  rows: TrackedStockRow[] = [];
  loading = true;
  error: string | null = null;
  sortKey: SortKey = 'coverage_initiation_date';
  sortAsc = false;
  categoryFilter: TrackedStockCategoryFilter = 'ALL';
  readonly categoryOptions = TRACKED_STOCK_CATEGORIES;

  addOpen = false;
  readonly addEntry = new SymbolEntry();
  addCategory: TrackedStockCategory = TRACKED_STOCK_CATEGORY_SOLD;
  addCoverageDate = '';
  addNotes = '';
  addTargetDate = '';
  addTargetAmount: number | null = null;
  addSaving = false;
  addError: string | null = null;

  editOpen = false;
  editRow: TrackedStockRow | null = null;
  editCategory: TrackedStockCategory = TRACKED_STOCK_CATEGORY_SOLD;
  editCoverageDate = '';
  editNotes = '';
  editTargetDate = '';
  editTargetAmount: number | null = null;
  editSaving = false;
  editError: string | null = null;

  removingSymbol: string | null = null;
  snapshotting = false;
  copyState: 'idle' | 'copied' | 'failed' = 'idle';
  private copyTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.destroyRef.onDestroy(() => clearTimeout(this.copyTimer));
    this.load();
  }

  load(): void {
    this.loading = true;
    this.error = null;
    this.cdr.markForCheck();
    this.api.list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.snapshot = response;
          this.allRows = response.stocks ?? [];
          this.applyView();
          this.loading = false;
          this.cdr.markForCheck();
        },
        error: error => {
          this.loading = false;
          this.error = this.message(error, 'Unable to load tracked stocks.');
          this.cdr.markForCheck();
        }
      });
  }

  openAdd(): void {
    this.addOpen = true;
    this.addEntry.clear();
    this.addCategory = TRACKED_STOCK_CATEGORY_SOLD;
    this.addCoverageDate = new Date().toISOString().slice(0, 10);
    this.addNotes = '';
    this.addTargetDate = '';
    this.addTargetAmount = null;
    this.addError = null;
    this.cdr.markForCheck();
  }

  closeAdd(): void {
    this.addOpen = false;
    this.addError = null;
    this.cdr.markForCheck();
  }

  get showAddTargets(): boolean {
    return this.addCategory === TRACKED_STOCK_CATEGORY_READY;
  }

  get showEditTargets(): boolean {
    return this.editCategory === TRACKED_STOCK_CATEGORY_READY;
  }

  parseSymbols(): void {
    const symbols = this.addEntry.raw
      .toUpperCase()
      .split(/[\s,]+/)
      .map(symbol => symbol.trim())
      .filter(Boolean);
    if (!symbols.length) {
      this.addEntry.chips = [];
      this.cdr.markForCheck();
      return;
    }
    this.api.lookupSymbols(symbols.join(','))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          const known = new Map(response.known.map(entry => [entry.symbol, entry]));
          this.addEntry.chips = symbols.map(symbol => ({
            symbol,
            known: known.has(symbol),
            name: known.get(symbol)?.name ?? '',
            price: known.get(symbol)?.price ?? null
          }));
          this.addError = null;
          this.cdr.markForCheck();
        },
        error: error => {
          this.addError = this.message(error, 'Unable to validate symbols.');
          this.cdr.markForCheck();
        }
      });
  }

  removeChip(symbol: string): void {
    this.addEntry.chips = this.addEntry.chips.filter(chip => chip.symbol !== symbol);
    this.addEntry.raw = this.addEntry.symbols.join(' ');
    this.cdr.markForCheck();
  }

  submitAdd(): void {
    if (!this.addEntry.valid) {
      this.addError = this.addEntry.unknown.length
        ? `Unknown symbols: ${this.addEntry.unknown.join(', ')}`
        : 'Add at least one universe symbol.';
      this.cdr.markForCheck();
      return;
    }
    this.addSaving = true;
    this.addError = null;
    this.cdr.markForCheck();
    this.api.add({
      symbols: this.addEntry.symbols,
      category: this.addCategory,
      coverage_initiation_date: this.addCoverageDate || undefined,
      notes: this.addNotes.trim() || undefined,
      ...this.targetPayload(this.addCategory, this.addTargetDate, this.addTargetAmount)
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.applyResponse(response);
          this.addSaving = false;
          this.addOpen = false;
          this.cdr.markForCheck();
        },
        error: error => {
          this.addSaving = false;
          this.addError = this.message(error, 'Unable to add tracked stocks.');
          this.cdr.markForCheck();
        }
      });
  }

  openEdit(row: TrackedStockRow): void {
    this.editOpen = true;
    this.editRow = row;
    this.editCategory = row.category;
    this.editCoverageDate = row.coverage_initiation_date;
    this.editNotes = row.notes || '';
    this.editTargetDate = row.target_date || '';
    this.editTargetAmount = row.target_amount;
    this.editError = null;
    this.cdr.markForCheck();
  }

  closeEdit(): void {
    this.editOpen = false;
    this.editRow = null;
    this.editError = null;
    this.cdr.markForCheck();
  }

  submitEdit(): void {
    const row = this.editRow;
    if (!row) return;
    this.editSaving = true;
    this.editError = null;
    this.cdr.markForCheck();
    this.api.update(row.symbol, {
      category: this.editCategory,
      coverage_initiation_date: this.editCoverageDate || undefined,
      notes: this.editNotes,
      ...this.targetPayload(this.editCategory, this.editTargetDate, this.editTargetAmount)
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.applyResponse(response);
          this.editSaving = false;
          this.editOpen = false;
          this.editRow = null;
          this.cdr.markForCheck();
        },
        error: error => {
          this.editSaving = false;
          this.editError = this.message(error, `Unable to update ${row.symbol}.`);
          this.cdr.markForCheck();
        }
      });
  }

  removeRow(symbol: string): void {
    this.removingSymbol = symbol;
    this.cdr.markForCheck();
    this.api.remove(symbol)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.applyResponse(response);
          this.removingSymbol = null;
          this.cdr.markForCheck();
        },
        error: error => {
          this.removingSymbol = null;
          this.error = this.message(error, `Unable to remove ${symbol}.`);
          this.cdr.markForCheck();
        }
      });
  }

  copySymbols(): void {
    const symbols = this.rows.map(row => row.symbol).join(' ');
    if (!symbols) return;
    navigator.clipboard.writeText(symbols)
      .then(() => this.flagCopy('copied'))
      .catch(() => this.flagCopy('failed'));
  }

  get copyLabel(): string {
    if (this.copyState === 'copied') return '✓ Copied!';
    return this.copyState === 'failed' ? 'Copy failed' : 'Copy symbols';
  }

  setCategoryFilter(filter: TrackedStockCategoryFilter): void {
    this.categoryFilter = filter;
    this.applyView();
    this.cdr.markForCheck();
  }

  get hasTrackedStocks(): boolean {
    return this.allRows.length > 0;
  }

  get hasVisibleRows(): boolean {
    return this.rows.length > 0;
  }

  coverageSnapshotDates(): { snapshot_date: string }[] {
    return this.snapshot?.coverage_vs_spy_snapshots ?? [];
  }

  captureCoverageVsSpySnapshot(): void {
    this.snapshotting = true;
    this.error = null;
    this.cdr.markForCheck();
    this.api.captureCoverageVsSpySnapshot()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.applyResponse(response);
          this.snapshotting = false;
          this.cdr.markForCheck();
        },
        error: error => {
          this.snapshotting = false;
          this.error = this.message(error, 'Coverage vs SPY snapshot could not be saved.');
          this.cdr.markForCheck();
        }
      });
  }

  isReady(row: TrackedStockRow): boolean {
    return row.category === TRACKED_STOCK_CATEGORY_READY;
  }

  sortBy(key: SortKey): void {
    if (this.sortKey === key) {
      this.sortAsc = !this.sortAsc;
    } else {
      this.sortKey = key;
      this.sortAsc = key === 'symbol';
    }
    this.applyView();
    this.cdr.markForCheck();
  }

  ariaSort(key: SortKey): 'ascending' | 'descending' | 'none' {
    if (this.sortKey !== key) return 'none';
    return this.sortAsc ? 'ascending' : 'descending';
  }

  sortIcon(key: SortKey): string {
    if (this.sortKey !== key) return '';
    return this.sortAsc ? '▲' : '▼';
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

  setupClass(setup?: MomentumSetup): string {
    return `setup-badge setup-${(setup || 'watch').toLowerCase().replaceAll('_', '-')}`;
  }

  formatRangePrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 2
    });
  }

  rangeTooltip(row: TrackedStockRow): string {
    if (row.fifty_two_week_low == null || row.fifty_two_week_high == null
        || row.range_position == null) {
      return 'Insufficient cached history for a 52-week range.';
    }
    return `Low ${this.formatRangePrice(row.fifty_two_week_low)}, `
      + `high ${this.formatRangePrice(row.fifty_two_week_high)}, `
      + `latest close at ${row.range_position.toFixed(0)}% of the band.`;
  }

  coverageTooltip(row: TrackedStockRow): string {
    return `Stock ${this.pct(row.coverage_return)} vs SPY ${this.pct(row.spy_coverage_return)} `
      + `since coverage began ${this.date(row.coverage_initiation_date)}.`;
  }

  ytdTooltip(row: TrackedStockRow): string {
    return `Stock ${this.pct(row.ytd_return)} vs SPY ${this.pct(this.snapshot?.spy_ytd_return)} `
      + `year to date.`;
  }

  price(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  dollars(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 0
    });
  }

  pct(value: number | null | undefined): string {
    if (value == null) return '—';
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}%`;
  }

  pp(value: number | null | undefined): string {
    if (value == null) return '—';
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)} pp`;
  }

  signClass(value: number | null | undefined): string {
    if (value == null) return '';
    return value > 0 ? 'pos-value' : value < 0 ? 'neg-value' : '';
  }

  date(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
    }).format(parsed);
  }

  trackBySymbol(_index: number, row: TrackedStockRow): string {
    return row.symbol;
  }

  private targetPayload(
    category: TrackedStockCategory,
    targetDate: string,
    targetAmount: number | null
  ): { target_date: string | null; target_amount: number | null } {
    if (category !== TRACKED_STOCK_CATEGORY_READY) {
      return { target_date: null, target_amount: null };
    }
    return {
      target_date: targetDate.trim() || null,
      target_amount: targetAmount
    };
  }

  private applyResponse(response: TrackedStockListResponse): void {
    this.snapshot = response;
    this.allRows = response.stocks ?? [];
    this.applyView();
  }

  private applyView(): void {
    const filtered = this.categoryFilter === 'ALL'
      ? this.allRows
      : this.allRows.filter(row => row.category === this.categoryFilter);
    this.rows = this.sortRows(filtered);
  }

  private sortRows(rows: TrackedStockRow[]): TrackedStockRow[] {
    const direction = this.sortAsc ? 1 : -1;
    return [...rows].sort((left, right) => {
      if (this.sortKey === 'symbol') {
        return left.symbol.localeCompare(right.symbol) * direction;
      }
      const leftValue = left[this.sortKey];
      const rightValue = right[this.sortKey];
      if (leftValue == null && rightValue == null) {
        return left.symbol.localeCompare(right.symbol);
      }
      if (leftValue == null) return 1;
      if (rightValue == null) return -1;
      if (leftValue === rightValue) return left.symbol.localeCompare(right.symbol);
      if (typeof leftValue === 'string' && typeof rightValue === 'string') {
        return leftValue.localeCompare(rightValue) * direction;
      }
      return ((leftValue as number) - (rightValue as number)) * direction;
    });
  }

  private flagCopy(state: 'copied' | 'failed'): void {
    clearTimeout(this.copyTimer);
    this.copyState = state;
    this.cdr.markForCheck();
    this.copyTimer = setTimeout(() => {
      this.copyState = 'idle';
      this.cdr.markForCheck();
    }, 2000);
  }

  private message(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return typeof detail === 'string' && detail.trim() ? detail : fallback;
  }
}
