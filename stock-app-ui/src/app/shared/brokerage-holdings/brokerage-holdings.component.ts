import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import { BrokerageId, HoldingItem, HoldingsResponse } from '../../model/brokerage';
import { ModalComponent } from '../ui/modal.component';

/** Columns a user can sort by; the rest are display-only. */
type SortColumn =
  | 'symbol' | 'category' | 'account' | 'industry' | 'quantity' | 'cost_per_unit'
  | 'mark_per_unit' | 'cost_basis' | 'market_value' | 'pct_of_total'
  | 'unrealized_pnl' | 'unrealized_pnl_pct';

@Component({
  selector: 'app-brokerage-holdings',
  standalone: true,
  imports: [CommonModule, FormsModule, MatTooltipModule, ModalComponent],
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
  error = '';
  message = '';
  editError = '';
  search = '';
  category = '';
  account = '';
  decliningOnly = false;
  copySuccess = false;
  editing: { symbol: string; category: string; industry: string; note: string } | null = null;
  sortColumn: SortColumn = 'market_value';
  sortAscending = false;

  private requestSequence = 0;
  private copyTimer?: ReturnType<typeof setTimeout>;
  private messageTimer?: ReturnType<typeof setTimeout>;

  constructor(private readonly api: BrokerageService) {}

  ngOnChanges(): void {
    if (this.brokerageId) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getHoldings(this.brokerageId).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
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
      category: row.category === 'UNCLASSIFIED' ? '' : row.category,
      industry: row.industry === 'UNCLASSIFIED' ? '' : row.industry,
      note: row.note,
    };
    this.editError = '';
  }

  closeEditor(): void {
    this.editing = null;
    this.editError = '';
  }

  saveEnrichment(): void {
    if (!this.editing) return;
    const { symbol, category, industry, note } = this.editing;
    this.saving = true;
    this.editError = '';
    this.api.updateHoldingsMetadata(this.brokerageId, symbol, { category, industry, note })
      .subscribe({
        next: () => {
          this.saving = false;
          this.editing = null;
          this.load();
        },
        error: err => {
          this.saving = false;
          this.editError = this.message_(err, 'The holding note could not be saved.');
        },
      });
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
    if (value == null) return '—';
    const formatted = Math.abs(value).toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    if (value < 0) return `−${formatted}`;
    return signed && value > 0 ? `+${formatted}` : formatted;
  }

  percent(value: number | null | undefined, signed = false): string {
    if (value == null) return '—';
    const formatted = `${Math.abs(value).toFixed(2)}%`;
    if (value < 0) return `−${formatted}`;
    return signed && value > 0 ? `+${formatted}` : formatted;
  }

  quantity(value: number | null | undefined): string {
    if (value == null) return '—';
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }

  timestamp(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
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
