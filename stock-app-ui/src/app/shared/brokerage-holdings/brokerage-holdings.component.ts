import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageLedgerService } from '../../api/brokerage-ledger.service';
import {
  BrokerageHolding,
  BrokerageHoldingsSnapshot,
} from '../../model/brokerage-holdings';
import { BrokerageLedgerPortfolioSlug } from '../../model/brokerage-ledger';
import { ModalComponent } from '../ui/modal.component';

@Component({
  selector: 'app-brokerage-holdings',
  standalone: true,
  imports: [CommonModule, FormsModule, MatTooltipModule, ModalComponent],
  templateUrl: './brokerage-holdings.component.html',
  styleUrl: './brokerage-holdings.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageHoldingsComponent implements OnChanges {
  @Input({ required: true }) portfolio!: BrokerageLedgerPortfolioSlug;
  @Input() refreshToken = 0;
  @Output() countChange = new EventEmitter<number>();

  data: BrokerageHoldingsSnapshot | null = null;
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
  sortColumn: keyof BrokerageHolding = 'currentValue';
  sortAscending = false;

  private requestSequence = 0;
  private copyTimer?: ReturnType<typeof setTimeout>;
  private messageTimer?: ReturnType<typeof setTimeout>;

  constructor(private readonly api: BrokerageLedgerService) {}

  ngOnChanges(): void {
    if (this.portfolio) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getHoldings(this.portfolio).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        data.gainLossSnapshots ??= [];
        data.holdings.forEach(row => row.gainLossSnapshots ??= {});
        this.data = data;
        this.countChange.emit(data.holdings.length);
        this.loading = false;
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.countChange.emit(0);
        this.loading = false;
        this.error = err?.error?.detail ?? 'The holdings view could not be loaded.';
      },
    });
  }

  filteredHoldings(): BrokerageHolding[] {
    const query = this.search.trim().toUpperCase();
    const rows = (this.data?.holdings ?? []).filter(row => {
      if (this.category && row.category !== this.category) return false;
      if (this.account && row.accountType !== this.account) return false;
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
    return [...new Set((this.data?.holdings ?? []).map(row => row.category).filter(Boolean))].sort();
  }

  accounts(): string[] {
    return [...new Set((this.data?.holdings ?? []).map(row => row.accountType).filter(Boolean))].sort();
  }

  industries(): string[] {
    return [...new Set((this.data?.holdings ?? []).map(row => row.industry)
      .filter(value => value && value !== 'UNCLASSIFIED'))].sort();
  }

  decliningCount(): number {
    return (this.data?.holdings ?? []).filter(row => row.trend.alert).length;
  }

  resetFilters(): void {
    this.search = '';
    this.category = '';
    this.account = '';
    this.decliningOnly = false;
  }

  sortBy(column: keyof BrokerageHolding): void {
    if (this.sortColumn === column) this.sortAscending = !this.sortAscending;
    else {
      this.sortColumn = column;
      this.sortAscending = ['symbol', 'category', 'accountType', 'industry'].includes(column);
    }
  }

  ariaSort(column: keyof BrokerageHolding): 'ascending' | 'descending' | 'none' {
    if (this.sortColumn !== column) return 'none';
    return this.sortAscending ? 'ascending' : 'descending';
  }

  sortIcon(column: keyof BrokerageHolding): string {
    if (this.sortColumn !== column) return '';
    return this.sortAscending ? '▲' : '▼';
  }

  captureSnapshot(): void {
    this.snapshotting = true;
    this.error = '';
    this.api.captureHoldingSnapshot(this.portfolio).subscribe({
      next: response => {
        this.snapshotting = false;
        this.data = response.portfolio;
        const verb = response.snapshot.replaced ? 'replaced' : 'saved';
        this.flash(`G/L snapshot for ${this.snapshotDateLabel(response.snapshot.syncDate)} ${verb}.`);
      },
      error: err => {
        this.snapshotting = false;
        this.error = err?.error?.detail ?? 'The G/L snapshot could not be saved.';
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

  openEditor(row: BrokerageHolding): void {
    this.editing = {
      symbol: row.enrichmentSymbol || row.symbol,
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
    this.api.updateHoldingEnrichment(this.portfolio, symbol, { category, industry, note }).subscribe({
      next: () => {
        this.saving = false;
        this.editing = null;
        this.load();
      },
      error: err => {
        this.saving = false;
        this.editError = err?.error?.detail ?? 'The holding note could not be saved.';
      },
    });
  }

  snapshotDateLabel(value: string): string {
    const parsed = new Date(`${value}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    });
  }

  trendTooltip(row: BrokerageHolding): string {
    const trend = row.trend;
    if (!trend.alert || trend.fromPct == null || trend.toPct == null || trend.dropPct == null) return '';
    const signed = (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
    const when = trend.alertAt ? ` (${new Date(trend.alertAt).toLocaleDateString()})` : '';
    return trend.direction === 'LOSS'
      ? `Loss deepened ${trend.dropPct.toFixed(0)}%: ${signed(trend.fromPct)} → ${signed(trend.toPct)}${when}.`
      : `Gain shrank ${trend.dropPct.toFixed(0)}%: ${signed(trend.fromPct)} → ${signed(trend.toPct)}${when}.`;
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
}
