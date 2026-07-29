import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import {
  ArchiveCreatedResponse,
  ArchiveSummary,
  BrokerageComponent,
  BrokerageId,
  LedgerEvent,
  LedgerEventsResponse,
  LedgerState,
  PnlCompleteness,
  SymbolLedgerDetail,
  SymbolLedgerListResponse,
  SymbolLedgerSummary,
} from '../../model/brokerage';
import { ModalComponent } from '../ui/modal.component';

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
  imports: [CommonModule, FormsModule, MatTooltipModule, ModalComponent],
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

  eventHistory: LedgerEventsResponse | null = null;
  eventHistoryLoading = false;
  eventHistoryLoadingMore = false;
  eventHistoryError = '';
  selectedArchiveId = '';

  archiveConfirmation: SymbolLedgerDetail | null = null;
  archiveSaving = false;
  archiveMessage = '';
  archiveRetryBody: { request_id: string; expected_period_version: string } | null = null;
  archiveConflict = false;
  statusMessage = '';

  noteDraft = '';
  noteSaving = false;
  noteMessage = '';

  private requestSequence = 0;
  private detailSequence = 0;
  private eventHistorySequence = 0;

  constructor(private readonly api: BrokerageService) {}

  ngOnChanges(): void {
    if (this.brokerageId) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.listSymbols(
      this.brokerageId, { state: this.state, exposure: 'options' }
    ).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.loading = false;
        if (this.expandedSymbol
          && !this.optionItems().some(row => row.symbol === this.expandedSymbol)) {
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
    this.clearHistory();
    this.loadDetail(symbol);
  }

  private loadDetail(symbol: string): void {
    this.detailLoading = true;
    this.detailError = '';
    const request = ++this.detailSequence;
    this.api.getSymbol(this.brokerageId, symbol).subscribe({
      next: body => {
        if (request !== this.detailSequence) return;
        this.detail = body.symbol;
        this.noteDraft = body.symbol.notes;
        this.detailLoading = false;
        this.loadEventHistory('current');
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
    this.clearHistory();
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

  // ------------------------------------------------------------- history ---

  selectEventPeriod(period: 'current' | 'all' | string): void {
    if (period === this.eventHistory?.period && !this.eventHistoryLoading) return;
    this.selectedArchiveId = period !== 'current' && period !== 'all' ? period : '';
    this.loadEventHistory(period);
  }

  loadMoreEvents(): void {
    if (!this.eventHistory?.has_more || !this.eventHistory.next_cursor) return;
    this.loadEventHistory(this.eventHistory.period, this.eventHistory.next_cursor);
  }

  private loadEventHistory(period: string, cursor?: string | null): void {
    const symbol = this.expandedSymbol;
    if (!symbol) return;
    const append = !!cursor && this.eventHistory?.period === period;
    if (append) this.eventHistoryLoadingMore = true;
    else {
      this.eventHistoryLoading = true;
      this.eventHistoryError = '';
      if (!cursor) this.eventHistory = null;
    }
    const request = ++this.eventHistorySequence;
    this.api.getSymbolEvents(this.brokerageId, symbol, {
      period, cursor: cursor ?? undefined, limit: 25,
    }).subscribe({
      next: body => {
        if (request !== this.eventHistorySequence || symbol !== this.expandedSymbol) return;
        this.eventHistory = append && this.eventHistory
          ? { ...body, items: [...this.eventHistory.items, ...body.items] }
          : body;
        this.eventHistoryLoading = false;
        this.eventHistoryLoadingMore = false;
      },
      error: err => {
        if (request !== this.eventHistorySequence || symbol !== this.expandedSymbol) return;
        this.eventHistoryLoading = false;
        this.eventHistoryLoadingMore = false;
        this.eventHistoryError = this.message(err, 'The event history could not be loaded.');
      },
    });
  }

  private clearHistory(): void {
    this.eventHistory = null;
    this.eventHistoryLoading = false;
    this.eventHistoryLoadingMore = false;
    this.eventHistoryError = '';
    this.selectedArchiveId = '';
    this.eventHistorySequence++;
  }

  eventLabel(event: LedgerEvent): string {
    if (event.instrument === 'EQUITY') return 'Shares';
    const kind = event.option_type === 'PUT' ? 'put' : 'call';
    return `${kind[0].toUpperCase()}${kind.slice(1)} option`;
  }

  eventContract(event: LedgerEvent): string {
    if (event.instrument === 'EQUITY') return '—';
    const strike = event.strike == null ? '—' : `$${event.strike}`;
    return `${strike} · ${event.expiry ?? '—'}`;
  }

  selectedArchive(): ArchiveSummary | null {
    return this.detail?.archives.find(row => row.archive_id === this.selectedArchiveId) ?? null;
  }

  // -------------------------------------------------------------- archive ---

  openArchiveConfirmation(): void {
    if (!this.detail?.reset_eligible) return;
    this.archiveConfirmation = this.detail;
    this.archiveMessage = '';
    this.archiveConflict = false;
    this.archiveRetryBody = null;
  }

  closeArchiveConfirmation(): void {
    if (this.archiveSaving) return;
    this.archiveConfirmation = null;
    this.archiveMessage = '';
    this.archiveConflict = false;
    this.archiveRetryBody = null;
  }

  archiveCurrentPeriod(): void {
    const symbol = this.archiveConfirmation;
    if (!symbol || this.archiveSaving || this.archiveConflict) return;
    const body = this.archiveRetryBody ?? {
      request_id: this.requestId(),
      expected_period_version: symbol.current_period.period_version,
    };
    this.archiveSaving = true;
    this.archiveMessage = '';
    this.api.createArchive(this.brokerageId, symbol.symbol, body).subscribe({
      next: created => this.archiveSucceeded(created),
      error: err => this.archiveFailed(err, body),
    });
  }

  refreshAfterArchiveConflict(): void {
    const symbol = this.expandedSymbol;
    this.closeArchiveConfirmation();
    this.statusMessage = 'The ledger was refreshed with the latest broker activity.';
    if (symbol) this.loadDetail(symbol);
    this.load();
  }

  private archiveSucceeded(created: ArchiveCreatedResponse): void {
    this.archiveSaving = false;
    this.archiveConfirmation = null;
    this.archiveRetryBody = null;
    this.archiveConflict = false;
    this.statusMessage = `${created.symbol.symbol} completed history archived.`;
    this.detail = created.symbol;
    this.noteDraft = created.symbol.notes;
    const row = this.data?.items.find(item => item.symbol === created.symbol.symbol);
    if (row) Object.assign(row, created.symbol);
    this.load();
  }

  private archiveFailed(err: unknown, body: { request_id: string; expected_period_version: string }): void {
    this.archiveSaving = false;
    this.archiveRetryBody = body;
    const code = this.errorCode(err);
    this.archiveConflict = code === 'PERIOD_CHANGED' || this.statusCode(err) === 409;
    this.archiveMessage = this.message(err, 'The completed history could not be archived.');
  }

  archiveBlocker(code: string): string {
    const labels: Record<string, string> = {
      PERIOD_EMPTY: 'The current period has no imported events to archive.',
      SYMBOL_NOT_FLAT: 'Open exposure remains, so this symbol is still active.',
      SYMBOL_NOT_RECONCILED: 'Imported activity does not reconcile with the broker position yet.',
      PERIOD_INCOMPLETE: 'The retained history is incomplete, so its result cannot be sealed.',
    };
    return labels[code] ?? 'This current period cannot be archived yet.';
  }

  // -------------------------------------------------------------- render ---

  /** The component is mounted in the Options tab, where equity-only holdings
   * already have their own view. Equity remains visible when it is part of a
   * symbol that also has option activity or positions. */
  optionItems(): SymbolLedgerSummary[] {
    return (this.data?.items ?? []).filter(row => row.exposure !== 'EQUITY');
  }

  rows(): SymbolLedgerSummary[] {
    const items = this.optionItems();
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

  private requestId(): string {
    return globalThis.crypto?.randomUUID?.()
      ?? `archive-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  private errorCode(err: unknown): string | null {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    return detail && typeof detail === 'object' && 'code' in detail
      ? String((detail as { code: unknown }).code)
      : null;
  }

  private statusCode(err: unknown): number | null {
    const status = (err as { status?: unknown })?.status;
    return typeof status === 'number' ? status : null;
  }

  private message(err: unknown, fallback = 'The symbol ledger could not be loaded.'): string {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message);
    }
    if (typeof detail === 'string') return detail;
    return fallback;
  }
}
