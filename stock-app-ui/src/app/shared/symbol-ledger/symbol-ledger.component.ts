import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import {
  BrokerageComponent,
  BrokerageId,
  LedgerState,
  PnlCompleteness,
  SymbolLedgerDetail,
  SymbolLedgerListResponse,
  SymbolLedgerSummary,
} from '../../model/brokerage';

type StateFilter = 'active' | 'archived' | 'all';

/**
 * One durable row per underlying symbol — the replacement for Trade Groups.
 *
 * Deliberately absent: a group name, an editable status, and any control that
 * moves an event between rows. Lifecycle is derived from exposure by the API,
 * and an event belongs to its symbol, so there is nothing here to name or
 * reassign. Notes stay editable because they are the user's own work.
 *
 * The component takes a `brokerageId` and never inspects it. Everything it
 * renders comes from common fields, so Trading and Retirement are the same
 * screen with a different value in one input.
 */
@Component({
  selector: 'app-symbol-ledger',
  standalone: true,
  imports: [CommonModule, FormsModule, MatTooltipModule],
  templateUrl: './symbol-ledger.component.html',
  styleUrl: './symbol-ledger.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class SymbolLedgerComponent implements OnChanges {
  @Input({ required: true }) brokerageId!: BrokerageId;
  @Input() refreshToken = 0;

  data: SymbolLedgerListResponse | null = null;
  loading = false;
  error = '';
  state: StateFilter = 'active';
  search = '';

  expandedSymbol = '';
  detail: SymbolLedgerDetail | null = null;
  detailLoading = false;
  detailError = '';

  noteDraft = '';
  noteSaving = false;
  noteMessage = '';

  private requestSequence = 0;
  private detailSequence = 0;

  constructor(private readonly api: BrokerageService) {}

  ngOnChanges(): void {
    if (this.brokerageId) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.listSymbols(this.brokerageId, { state: this.state }).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.loading = false;
        if (this.expandedSymbol && !data.items.some(row => row.symbol === this.expandedSymbol)) {
          this.collapse();
        }
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.loading = false;
        this.error = this.message(err);
      },
    });
  }

  setState(state: StateFilter): void {
    if (this.state === state) return;
    this.state = state;
    this.collapse();
    this.load();
  }

  // ------------------------------------------------------------- detail ---

  toggle(symbol: string): void {
    if (this.expandedSymbol === symbol) {
      this.collapse();
      return;
    }
    this.expandedSymbol = symbol;
    this.detail = null;
    this.detailError = '';
    this.noteMessage = '';
    this.detailLoading = true;
    const request = ++this.detailSequence;
    this.api.getSymbol(this.brokerageId, symbol).subscribe({
      next: body => {
        if (request !== this.detailSequence) return;
        this.detail = body.symbol;
        this.noteDraft = body.symbol.notes;
        this.detailLoading = false;
      },
      error: err => {
        if (request !== this.detailSequence) return;
        this.detailLoading = false;
        this.detailError = this.message(err);
      },
    });
  }

  collapse(): void {
    this.expandedSymbol = '';
    this.detail = null;
    this.detailError = '';
    this.noteMessage = '';
    this.detailSequence++;
  }

  saveNote(): void {
    if (!this.detail || this.noteSaving) return;
    const symbol = this.detail.symbol;
    this.noteSaving = true;
    this.noteMessage = '';
    this.api.updateSymbolNotes(this.brokerageId, symbol, this.noteDraft).subscribe({
      next: body => {
        this.detail = body.symbol;
        this.noteDraft = body.symbol.notes;
        this.noteSaving = false;
        this.noteMessage = 'Note saved.';
        const row = this.data?.items.find(item => item.symbol === symbol);
        if (row) row.notes = body.symbol.notes;
      },
      error: err => {
        this.noteSaving = false;
        this.noteMessage = this.message(err);
      },
    });
  }

  get noteDirty(): boolean {
    return !!this.detail && this.noteDraft !== this.detail.notes;
  }

  // -------------------------------------------------------------- render ---

  rows(): SymbolLedgerSummary[] {
    const items = this.data?.items ?? [];
    const term = this.search.trim().toUpperCase();
    if (!term) return items;
    return items.filter(row =>
      row.symbol.includes(term)
      || row.notes.toUpperCase().includes(term)
      || row.accounts.some(account => account.toUpperCase().includes(term))
    );
  }

  /** `—` for anything unavailable. A missing value is never rendered as zero. */
  money(value: number | null | undefined, signed = false): string {
    if (value === null || value === undefined) return '—';
    const formatted = Math.abs(value).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    const sign = value < 0 ? '−' : signed && value > 0 ? '+' : '';
    return `${sign}$${formatted}`;
  }

  number(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    return value.toLocaleString();
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }

  completenessLabel(value: PnlCompleteness): string {
    if (value === 'COMPLETE') return 'Realized';
    if (value === 'INDICATIVE') return 'Indicative';
    return 'Unavailable';
  }

  completenessClass(value: PnlCompleteness): string {
    if (value === 'COMPLETE') return 'badge badge-pos';
    if (value === 'INDICATIVE') return 'badge badge-info';
    return 'badge badge-warn';
  }

  stateClass(state: LedgerState): string {
    return state === 'ACTIVE' ? 'badge badge-primary' : 'badge badge-neutral';
  }

  componentLabel(row: BrokerageComponent): string {
    if (row.instrument === 'EQUITY') {
      return row.state === 'FLAT' ? 'Closed shares' : 'Shares';
    }
    const side = row.side === 'SHORT' ? 'Short' : 'Long';
    const kind = row.option_type === 'PUT' ? 'put' : 'call';
    return `${side} ${kind}`;
  }

  contractLabel(row: BrokerageComponent): string {
    if (row.instrument !== 'OPTION') return '—';
    const strike = row.strike === null ? '—' : `$${row.strike}`;
    return `${strike} · ${row.expiry ?? '—'}`;
  }

  provenanceLabel(row: BrokerageComponent): string {
    return [
      row.provenance.position_source,
      row.provenance.activity_source,
      row.provenance.market_source,
    ].filter(Boolean).join(' · ') || '—';
  }

  timestamp(value: string | null): string {
    if (!value) return 'unavailable';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  private message(err: unknown): string {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message);
    }
    if (typeof detail === 'string') return detail;
    return 'The symbol ledger could not be loaded.';
  }
}
