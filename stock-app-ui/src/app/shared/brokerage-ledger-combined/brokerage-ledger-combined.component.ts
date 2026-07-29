import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { BrokerageLedgerService } from '../../api/brokerage-ledger.service';
import {
  BrokerageLedgerAnnotation,
  BrokerageLedgerCompleteness,
  BrokerageLedgerComponent,
  BrokerageLedgerPortfolioSlug,
  BrokerageLedgerSnapshot,
  BrokerageLedgerSymbol,
  BrokerageLedgerWarning,
} from '../../model/brokerage-ledger';

@Component({
  selector: 'app-brokerage-ledger-combined',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './brokerage-ledger-combined.component.html',
  styleUrl: './brokerage-ledger-combined.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageLedgerCombinedComponent implements OnChanges {
  @Input({ required: true }) portfolio!: BrokerageLedgerPortfolioSlug;
  @Input() refreshToken = 0;

  data: BrokerageLedgerSnapshot | null = null;
  loading = false;
  error = '';
  search = '';
  expandedSymbol = '';

  private requestSequence = 0;

  constructor(private readonly api: BrokerageLedgerService) {}

  ngOnChanges(): void {
    if (this.portfolio) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getCombined(this.portfolio).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.loading = false;
        if (this.expandedSymbol && !data.symbols.some(row => row.symbol === this.expandedSymbol)) {
          this.expandedSymbol = '';
        }
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.loading = false;
        this.error = err?.error?.detail ?? 'The option-adjusted basis view could not be loaded.';
      },
    });
  }

  basisSymbols(): BrokerageLedgerSymbol[] {
    return (this.data?.symbols ?? []).filter(row =>
      row.exposure === 'EQUITY_AND_OPTIONS'
      && row.components.some(component =>
        component.instrument === 'EQUITY'
        && component.side === 'LONG'
        && component.state === 'OPEN'
        && component.quantity > 0
      )
      && row.components.some(component => component.instrument === 'OPTION')
    );
  }

  filteredSymbols(): BrokerageLedgerSymbol[] {
    const query = this.search.trim().toUpperCase();
    return this.basisSymbols().filter(row => {
      if (!query) return true;
      const notes = row.annotations.map(annotation => annotation.text).join(' ');
      return `${row.symbol} ${row.accounts.join(' ')} ${notes}`.toUpperCase().includes(query);
    });
  }

  resetFilters(): void {
    this.search = '';
  }

  hasFilters(): boolean {
    return !!this.search;
  }

  toggleSymbol(symbol: string): void {
    this.expandedSymbol = this.expandedSymbol === symbol ? '' : symbol;
  }

  unavailableBasisCount(): number {
    return this.basisSymbols().filter(
      row => row.adjusted_basis.completeness === 'UNAVAILABLE'
    ).length;
  }

  basisWarnings(): BrokerageLedgerWarning[] {
    const symbols = new Set(this.basisSymbols().map(row => row.symbol));
    return (this.data?.warnings ?? []).filter(warning =>
      warning.scope === 'PORTFOLIO' || (!!warning.symbol && symbols.has(warning.symbol))
    );
  }

  basisTotal(field: 'equity_market_value' | 'option_market_value' | 'open_market_value' | 'net_pnl'): number | null {
    const rows = this.basisSymbols();
    if (!rows.length) return 0;
    const values = rows.map(row => row[field]);
    if (values.some(value => value == null)) return null;
    return values.reduce<number>((total, value) => total + (value ?? 0), 0);
  }

  componentType(component: BrokerageLedgerComponent): string {
    if (component.instrument === 'EQUITY') {
      return `${component.side === 'LONG' ? 'Long' : 'Short'} equity`;
    }
    const side = component.side === 'LONG' ? 'Long' : 'Short';
    const optionType = component.option_type === 'CALL' ? 'call' : 'put';
    return `${side} ${optionType}`;
  }

  componentState(component: BrokerageLedgerComponent): string {
    return component.state === 'OPEN' ? 'Open' : 'Flat';
  }

  contractLabel(component: BrokerageLedgerComponent): string {
    if (component.instrument === 'EQUITY') return '—';
    const strike = component.strike == null ? '—' : this.money(component.strike);
    return `${component.expiry || '—'} · ${strike}`;
  }

  completenessLabel(value: BrokerageLedgerCompleteness): string {
    const labels: Record<BrokerageLedgerCompleteness, string> = {
      COMPLETE: 'Complete',
      INDICATIVE: 'Indicative',
      UNAVAILABLE: 'Unavailable',
    };
    return labels[value];
  }

  completenessClass(value: BrokerageLedgerCompleteness): string {
    if (value === 'COMPLETE') return 'badge-pos';
    if (value === 'INDICATIVE') return 'badge-warn';
    return 'badge-neg';
  }

  cashBasisLabel(component: BrokerageLedgerComponent): string {
    const labels: Record<BrokerageLedgerComponent['cash_flow_basis'], string> = {
      BROKER_ACTIVITY: 'Broker activity',
      POSITION_COST_BASIS: 'Broker position basis',
      UNAVAILABLE: 'Cash history unavailable',
    };
    return labels[component.cash_flow_basis];
  }

  sourceLabel(component: BrokerageLedgerComponent): string {
    const sources = [
      component.provenance.position_source,
      component.provenance.activity_source,
      component.provenance.market_source,
    ].filter((source): source is string => !!source);
    return [...new Set(sources)].join(' · ') || 'Source unavailable';
  }

  annotationText(annotations: BrokerageLedgerAnnotation[]): string {
    return annotations.map(annotation => annotation.text).filter(Boolean).join(' · ');
  }

  notePreview(annotations: BrokerageLedgerAnnotation[]): string {
    const text = this.annotationText(annotations);
    return text || '—';
  }

  money(value: number | null | undefined, signed = false): string {
    if (value == null) return '—';
    const formatted = Math.abs(value).toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    if (value < 0) return `−${formatted}`;
    return signed && value > 0 ? `+${formatted}` : formatted;
  }

  quantity(value: number | null | undefined): string {
    if (value == null) return '—';
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }

  timestamp(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }

  trackSymbol(_index: number, row: BrokerageLedgerSymbol): string {
    return row.symbol;
  }

  trackComponent(_index: number, row: BrokerageLedgerComponent): string {
    return row.id;
  }
}
