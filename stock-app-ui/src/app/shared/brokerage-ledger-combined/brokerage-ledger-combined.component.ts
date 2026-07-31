import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { BrokerageService } from '../../api/brokerage.service';
import {
  AdjustedBasisItem,
  AdjustedBasisResponse,
  BrokerageComponent,
  BrokerageId,
  BrokerageWarning,
  PnlCompleteness,
} from '../../model/brokerage';
import {
  formatIsoTimestamp,
  formatQuantity,
  formatUsdMoney,
  pnlToneClass,
} from '../format-display';

@Component({
  selector: 'app-brokerage-ledger-combined',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './brokerage-ledger-combined.component.html',
  styleUrl: './brokerage-ledger-combined.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageLedgerCombinedComponent implements OnChanges {
  @Input({ required: true }) brokerageId!: BrokerageId;
  @Input() refreshToken = 0;

  data: AdjustedBasisResponse | null = null;
  loading = false;
  error = '';
  search = '';
  expandedSymbol = '';

  private requestSequence = 0;

  constructor(private readonly api: BrokerageService) {}

  ngOnChanges(): void {
    if (this.brokerageId) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getOptionAdjustedBasis(this.brokerageId).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.loading = false;
        if (this.expandedSymbol && !data.items.some(row => row.symbol === this.expandedSymbol)) {
          this.expandedSymbol = '';
        }
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.loading = false;
        this.error = err?.error?.detail
          ?? 'The Combined Adjusted Basis view could not be loaded.';
      },
    });
  }

  basisSymbols(): AdjustedBasisItem[] {
    return (this.data?.items ?? []).filter(row =>
      row.components.some(component =>
        component.instrument === 'EQUITY'
        && component.side === 'LONG'
        && component.state === 'OPEN'
        && component.quantity > 0
      )
      && row.components.some(component => component.instrument === 'OPTION')
    );
  }

  filteredSymbols(): AdjustedBasisItem[] {
    const query = this.search.trim().toUpperCase();
    return this.basisSymbols().filter(row => {
      if (!query) return true;
      return `${row.symbol} ${row.accounts.join(' ')}`.toUpperCase().includes(query);
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

  basisWarnings(): BrokerageWarning[] {
    const symbols = new Set(this.basisSymbols().map(row => row.symbol));
    return (this.data?.warnings ?? []).filter(warning =>
      warning.scope === 'PORTFOLIO' || (!!warning.symbol && symbols.has(warning.symbol))
    );
  }

  basisTotal(field: 'current_equity' | 'option_market_value' | 'net_pnl'): number | null {
    const rows = this.basisSymbols();
    if (!rows.length) return 0;
    const values = rows.map(row => row[field]);
    if (values.some(value => value == null)) return null;
    return values.reduce<number>((total, value) => total + (value ?? 0), 0);
  }

  totalMarketValue(): number | null {
    const equity = this.basisTotal('current_equity');
    const options = this.basisTotal('option_market_value');
    return equity == null || options == null ? null : equity + options;
  }

  currentPricePerShare(row: AdjustedBasisItem): number | null {
    return row.current_equity == null || row.share_quantity <= 0
      ? null : row.current_equity / row.share_quantity;
  }

  componentType(component: BrokerageComponent): string {
    if (component.instrument === 'EQUITY') {
      return `${component.side === 'LONG' ? 'Long' : 'Short'} equity`;
    }
    const side = component.side === 'LONG' ? 'Long' : 'Short';
    const optionType = component.option_type === 'CALL' ? 'call' : 'put';
    return `${side} ${optionType}`;
  }

  componentState(component: BrokerageComponent): string {
    return component.state === 'OPEN' ? 'Open' : 'Flat';
  }

  contractLabel(component: BrokerageComponent): string {
    if (component.instrument === 'EQUITY') return '—';
    const strike = component.strike == null ? '—' : this.money(component.strike);
    return `${component.expiry || '—'} · ${strike}`;
  }

  completenessLabel(value: PnlCompleteness): string {
    const labels: Record<PnlCompleteness, string> = {
      COMPLETE: 'Complete',
      INDICATIVE: 'Indicative',
      UNAVAILABLE: 'Unavailable',
    };
    return labels[value];
  }

  completenessClass(value: PnlCompleteness): string {
    if (value === 'COMPLETE') return 'badge-pos';
    if (value === 'INDICATIVE') return 'badge-warn';
    return 'badge-neg';
  }

  cashBasisLabel(component: BrokerageComponent): string {
    const labels: Record<string, string> = {
      BROKER_ACTIVITY: 'Broker activity',
      POSITION_COST_BASIS: 'Broker position basis',
      UNAVAILABLE: 'Cash history unavailable',
    };
    return labels[component.cash_flow_basis] ?? component.cash_flow_basis;
  }

  sourceLabel(component: BrokerageComponent): string {
    const sources = [
      component.provenance.position_source,
      component.provenance.activity_source,
      component.provenance.market_source,
    ].filter((source): source is string => !!source);
    return [...new Set(sources)].join(' · ') || 'Source unavailable';
  }

  money(value: number | null | undefined, signed = false): string {
    return formatUsdMoney(value, signed);
  }

  quantity(value: number | null | undefined): string {
    return formatQuantity(value);
  }

  timestamp(value: string | null | undefined): string {
    return formatIsoTimestamp(value);
  }

  pnlClass(value: number | null | undefined): string {
    return pnlToneClass(value);
  }

  trackSymbol(_index: number, row: AdjustedBasisItem): string {
    return row.symbol;
  }

  trackComponent(_index: number, row: BrokerageComponent): string {
    return row.id;
  }
}
