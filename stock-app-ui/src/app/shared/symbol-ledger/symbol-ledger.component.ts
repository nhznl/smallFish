import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import {
  ArchiveCreatedResponse,
  BreakevenBand,
  BreakevenKind,
  BrokerageComponent,
  BrokerageId,
  LedgerEvent,
  LedgerEventsResponse,
  PnlCompleteness,
  StrikeRisk,
  SymbolLedgerDetail,
  SymbolLedgerListResponse,
  SymbolLedgerSummary,
} from '../../model/brokerage';
import { ModalComponent } from '../ui/modal.component';
import { pnlToneClass } from '../format-display';

type StateFilter = 'active' | 'closed';
type SortColumn = 'symbol' | 'dte' | 'total_pnl';

const ACTIVE_COLUMN_COUNT = 13;
const CLOSED_COLUMN_COUNT = 10;

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
  sortColumn: SortColumn = 'symbol';
  sortAscending = true;

  expandedSymbol = '';
  detail: SymbolLedgerDetail | null = null;
  detailLoading = false;
  detailError = '';

  eventHistory: LedgerEventsResponse | null = null;
  eventHistoryLoading = false;
  eventHistoryLoadingMore = false;
  eventHistoryError = '';
  archiveEventHistory: LedgerEventsResponse | null = null;
  archiveEventHistoryLoading = false;
  archiveEventHistoryLoadingMore = false;
  archiveEventHistoryError = '';
  selectedArchiveId = '';

  archiveConfirmation: SymbolLedgerDetail | null = null;
  archiveSaving = false;
  archiveMessage = '';
  archiveRetryBody: { request_id: string; expected_period_version: string } | null = null;
  archiveConflict = false;
  statusMessage = '';

  noteEditor: { symbol: string; notes: string; original: string } | null = null;
  noteSaving = false;
  noteMessage = '';

  private requestSequence = 0;
  private detailSequence = 0;
  private currentHistorySequence = 0;
  private archiveHistorySequence = 0;

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
    if (state === 'closed' && this.sortColumn === 'dte') {
      this.sortColumn = 'symbol';
      this.sortAscending = true;
    }
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
        this.detailLoading = false;
        this.loadCurrentEventHistory();
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
    this.detailSequence++;
    this.clearHistory();
  }

  openNoteEditor(row: SymbolLedgerSummary): void {
    this.noteEditor = {
      symbol: row.symbol,
      notes: row.notes,
      original: row.notes,
    };
    this.noteMessage = '';
  }

  closeNoteEditor(): void {
    if (this.noteSaving) return;
    this.noteEditor = null;
    this.noteMessage = '';
  }

  saveNote(): void {
    if (!this.noteEditor || this.noteSaving || !this.noteDirty) return;
    const { symbol, notes } = this.noteEditor;
    this.noteSaving = true;
    this.noteMessage = '';
    this.api.updateSymbolNotes(this.brokerageId, symbol, notes).subscribe({
      next: body => {
        this.noteSaving = false;
        this.noteEditor = null;
        this.noteMessage = '';
        const row = this.data?.items.find(item => item.symbol === symbol);
        if (row) row.notes = body.symbol.notes;
        if (this.detail?.symbol === symbol) this.detail = body.symbol;
        this.statusMessage = `Note saved for ${symbol}.`;
      },
      error: err => {
        this.noteSaving = false;
        this.noteMessage = this.message(err, 'The note could not be saved.');
      },
    });
  }

  get noteDirty(): boolean {
    return !!this.noteEditor && this.noteEditor.notes !== this.noteEditor.original;
  }

  // ------------------------------------------------------------- history ---

  selectArchive(archiveId: string): void {
    if (archiveId === this.selectedArchiveId) {
      this.clearArchiveHistory();
      return;
    }
    this.selectedArchiveId = archiveId;
    this.loadArchiveEventHistory(archiveId);
  }

  loadMoreEvents(): void {
    if (!this.eventHistory?.has_more || !this.eventHistory.next_cursor) return;
    this.loadCurrentEventHistory(this.eventHistory.next_cursor);
  }

  loadMoreArchiveEvents(): void {
    if (!this.archiveEventHistory?.has_more || !this.archiveEventHistory.next_cursor) return;
    this.loadArchiveEventHistory(this.selectedArchiveId, this.archiveEventHistory.next_cursor);
  }

  retryCurrentEventHistory(): void {
    this.loadCurrentEventHistory();
  }

  retryArchiveEventHistory(): void {
    if (this.selectedArchiveId) this.loadArchiveEventHistory(this.selectedArchiveId);
  }

  private loadCurrentEventHistory(cursor?: string | null): void {
    const symbol = this.expandedSymbol;
    if (!symbol) return;
    const append = !!cursor && this.eventHistory?.period === 'current';
    if (append) this.eventHistoryLoadingMore = true;
    else {
      this.eventHistoryLoading = true;
      this.eventHistoryError = '';
      if (!cursor) this.eventHistory = null;
    }
    const request = ++this.currentHistorySequence;
    this.api.getSymbolEvents(this.brokerageId, symbol, {
      period: 'current', cursor: cursor ?? undefined, limit: 25,
    }).subscribe({
      next: body => {
        if (request !== this.currentHistorySequence || symbol !== this.expandedSymbol) return;
        this.eventHistory = append && this.eventHistory
          ? { ...body, items: [...this.eventHistory.items, ...body.items] }
          : body;
        this.eventHistoryLoading = false;
        this.eventHistoryLoadingMore = false;
      },
      error: err => {
        if (request !== this.currentHistorySequence || symbol !== this.expandedSymbol) return;
        this.eventHistoryLoading = false;
        this.eventHistoryLoadingMore = false;
        this.eventHistoryError = this.message(err, 'The event history could not be loaded.');
      },
    });
  }

  private loadArchiveEventHistory(archiveId: string, cursor?: string | null): void {
    const symbol = this.expandedSymbol;
    if (!symbol || !archiveId) return;
    const append = !!cursor && this.archiveEventHistory?.period === archiveId;
    if (append) this.archiveEventHistoryLoadingMore = true;
    else {
      this.archiveEventHistoryLoading = true;
      this.archiveEventHistoryError = '';
      if (!cursor) this.archiveEventHistory = null;
    }
    const request = ++this.archiveHistorySequence;
    this.api.getSymbolEvents(this.brokerageId, symbol, {
      period: archiveId, cursor: cursor ?? undefined, limit: 25,
    }).subscribe({
      next: body => {
        if (request !== this.archiveHistorySequence || symbol !== this.expandedSymbol
          || archiveId !== this.selectedArchiveId) return;
        this.archiveEventHistory = append && this.archiveEventHistory
          ? { ...body, items: [...this.archiveEventHistory.items, ...body.items] }
          : body;
        this.archiveEventHistoryLoading = false;
        this.archiveEventHistoryLoadingMore = false;
      },
      error: err => {
        if (request !== this.archiveHistorySequence || symbol !== this.expandedSymbol
          || archiveId !== this.selectedArchiveId) return;
        this.archiveEventHistoryLoading = false;
        this.archiveEventHistoryLoadingMore = false;
        this.archiveEventHistoryError = this.message(err, 'The archived event history could not be loaded.');
      },
    });
  }

  private clearHistory(): void {
    this.eventHistory = null;
    this.eventHistoryLoading = false;
    this.eventHistoryLoadingMore = false;
    this.eventHistoryError = '';
    this.clearArchiveHistory();
    this.currentHistorySequence++;
  }

  private clearArchiveHistory(): void {
    this.archiveEventHistory = null;
    this.archiveEventHistoryLoading = false;
    this.archiveEventHistoryLoadingMore = false;
    this.archiveEventHistoryError = '';
    this.selectedArchiveId = '';
    this.archiveHistorySequence++;
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
    this.clearArchiveHistory();
    this.loadCurrentEventHistory();
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
    const filtered = !term ? items : items.filter(row =>
      row.symbol.includes(term)
      || row.notes.toUpperCase().includes(term)
      || row.accounts.some(account => account.toUpperCase().includes(term))
    );
    return [...filtered].sort((left, right) => {
      if (this.sortColumn === 'symbol') {
        return this.sortAscending
          ? left.symbol.localeCompare(right.symbol)
          : right.symbol.localeCompare(left.symbol);
      }
      const a = this.sortColumn === 'dte' ? left.dte : left.current_period.total_pnl;
      const b = this.sortColumn === 'dte' ? right.dte : right.current_period.total_pnl;
      const av = typeof a === 'number' ? a : Number.NEGATIVE_INFINITY;
      const bv = typeof b === 'number' ? b : Number.NEGATIVE_INFINITY;
      return this.sortAscending ? av - bv : bv - av;
    });
  }

  sortBy(column: SortColumn): void {
    if (this.sortColumn === column) this.sortAscending = !this.sortAscending;
    else {
      this.sortColumn = column;
      this.sortAscending = column === 'symbol' || column === 'dte';
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

  columnCount(): number {
    return this.state === 'active' ? ACTIVE_COLUMN_COUNT : CLOSED_COLUMN_COUNT;
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

  price(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    return `$${value.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    })}`;
  }

  number(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    return value.toLocaleString();
  }

  pnlClass(value: number | null | undefined): string {
    return pnlToneClass(value);
  }

  strikeRiskLabel(risk: StrikeRisk): string {
    if (risk === 'ITM') return 'ITM';
    if (risk === 'NEAR_STRIKE') return 'Near strike';
    return '';
  }

  strikeRiskBadgeClass(risk: StrikeRisk): string {
    if (risk === 'ITM') return 'badge badge-neg';
    if (risk === 'NEAR_STRIKE') return 'badge badge-warn';
    return '';
  }

  breakevenCaption(kind: BreakevenKind | null | undefined): string {
    if (kind === 'SHORT_CALL') return 'spot · strike · BE';
    if (kind === 'SHORT_PUT') return 'BE · strike · spot';
    if (kind === 'SHORT_STRANGLE') return 'put BE · spot · call BE';
    return '';
  }

  breakevenValues(band: BreakevenBand | null | undefined): string {
    if (!band?.points?.length) return '—';
    return band.points.map(point => this.price(point.value)).join(' · ');
  }

  breakevenTrack(band: BreakevenBand | null | undefined): {
    markers: { role: string; pct: number }[];
  } | null {
    if (!band?.points?.length) return null;
    const values = band.points.map(point => point.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return {
      markers: band.points.map(point => ({
        role: point.role.toLowerCase(),
        pct: ((point.value - min) / span) * 100,
      })),
    };
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
    const status = this.statusCode(err);
    const code = this.errorCode(err);
    if (status != null && code) return `${fallback} (${status} ${code})`;
    if (status != null) return `${fallback} (${status})`;
    return fallback;
  }
}
