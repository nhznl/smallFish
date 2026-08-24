import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  ElementRef,
  inject
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DrawerComponent } from '../shared/ui/drawer.component';
import { ModalComponent } from '../shared/ui/modal.component';
import { PortfolioService } from '../api/portfolio.service';
import {
  PortfolioDetailResponse,
  PortfolioListResponse,
  PortfolioMember,
  PortfolioSummary,
  SymbolChip
} from '../model/portfolio';

/** Sortable list columns. */
type SortKey = 'name' | 'symbol_count' | 'week_return' | 'inception_vs_spy' | 'ytd_vs_spy';

/**
 * A symbol field and the validated chips parsed out of it.
 *
 * The create modal and the drawer's add row share this so both name their
 * offenders the same way: universe membership is a hard rule, and finding out
 * only after a failed round-trip is not the same affordance as being told
 * which chip to fix before submitting.
 */
class SymbolEntry {
  raw = '';
  chips: SymbolChip[] = [];

  get symbols(): string[] {
    return this.chips.map(chip => chip.symbol);
  }

  get unknown(): string[] {
    return this.chips.filter(chip => !chip.known).map(chip => chip.symbol);
  }

  /** Submittable: at least one symbol, none of them off-universe. */
  get valid(): boolean {
    return this.chips.length > 0 && this.unknown.length === 0;
  }

  clear(): void {
    this.raw = '';
    this.chips = [];
  }
}

@Component({
  selector: 'app-portfolios',
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    MatTooltipModule,
    DrawerComponent,
    ModalComponent
],
  templateUrl: './portfolios.component.html',
  styleUrl: './portfolios.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class PortfoliosComponent {
  private readonly api = inject(PortfolioService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host: ElementRef<HTMLElement> = inject(ElementRef);

  // ── list ────────────────────────────────────────────────────────────────
  snapshot: PortfolioListResponse | null = null;
  rows: PortfolioSummary[] = [];
  loading = true;
  error: string | null = null;
  sortKey: SortKey = 'inception_vs_spy';
  sortAsc = false;
  snapshotting = false;

  // ── create modal ────────────────────────────────────────────────────────
  createOpen = false;
  createName = '';
  createDescription = '';
  createSector = '';
  createIndustry = '';
  readonly createEntry = new SymbolEntry();
  createSaving = false;
  createError: string | null = null;
  sectorOptions: string[] = [];

  // ── detail drawer ───────────────────────────────────────────────────────
  detail: PortfolioDetailResponse | null = null;
  detailLoading = false;
  detailError: string | null = null;
  editing = false;
  editName = '';
  editDescription = '';
  editSector = '';
  editIndustry = '';
  editSaving = false;
  readonly addEntry = new SymbolEntry();
  addBusy = false;
  /** Metadata-edit failures, shown next to the header form. */
  memberError: string | null = null;
  /** Member add/remove failures, shown next to the add row that caused them. */
  addError: string | null = null;
  /** One-action undo after a member removal; re-adding is the reversal. */
  undoSymbol: string | null = null;
  deleteOpen = false;
  deleting = false;
  /** Transient feedback on the Copy symbols button, mirroring Strategy's. */
  copyState: 'idle' | 'copied' | 'failed' = 'idle';
  private copyTimer?: ReturnType<typeof setTimeout>;

  private openerElement: HTMLElement | null = null;

  constructor() {
    this.destroyRef.onDestroy(() => clearTimeout(this.copyTimer));
    this.load();
    this.api.sectors()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.sectorOptions = response.sectors ?? [];
          this.cdr.markForCheck();
        },
        // Suggestions are a convenience; the field stays free text without them.
        error: () => undefined
      });
  }

  load(focusId?: string): void {
    this.loading = true;
    this.error = null;
    this.cdr.markForCheck();
    this.api.list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.snapshot = response;
          this.rows = this.sortRows(response.portfolios ?? []);
          this.loading = false;
          this.cdr.markForCheck();
          if (focusId) this.focusRow(focusId);
        },
        error: error => {
          this.loading = false;
          this.error = this.message(error, 'Unable to load portfolios.');
          this.cdr.markForCheck();
        }
      });
  }

  // ── stat strip ──────────────────────────────────────────────────────────

  /** The widest since-inception spread on the page, and who owns it. */
  get bestVsSpy(): PortfolioSummary | null {
    const ranked = this.rows.filter(row => row.inception_vs_spy != null);
    if (!ranked.length) return null;
    return ranked.reduce((best, row) =>
      (row.inception_vs_spy ?? 0) > (best.inception_vs_spy ?? 0) ? row : best);
  }

  get totalMissingData(): number {
    return this.rows.filter(row => row.missing_data_symbols.length).length;
  }

  inceptionSnapshotDates(): { snapshot_date: string }[] {
    return this.snapshot?.inception_vs_spy_snapshots ?? [];
  }

  captureInceptionVsSpySnapshot(): void {
    this.snapshotting = true;
    this.error = null;
    this.cdr.markForCheck();
    this.api.captureInceptionVsSpySnapshot()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.snapshot = response;
          this.rows = this.sortRows(response.portfolios ?? []);
          this.snapshotting = false;
          this.cdr.markForCheck();
        },
        error: error => {
          this.snapshotting = false;
          this.error = this.message(error, 'Incep vs SPY snapshot could not be saved.');
          this.cdr.markForCheck();
        }
      });
  }

  // ── sorting ─────────────────────────────────────────────────────────────

  sortBy(key: SortKey): void {
    if (this.sortKey === key) {
      this.sortAsc = !this.sortAsc;
    } else {
      this.sortKey = key;
      // Names read naturally A→Z; numeric columns read largest-first.
      this.sortAsc = key === 'name';
    }
    this.rows = this.sortRows(this.rows);
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

  private sortRows(rows: PortfolioSummary[]): PortfolioSummary[] {
    const direction = this.sortAsc ? 1 : -1;
    return [...rows].sort((left, right) => {
      if (this.sortKey === 'name') {
        const byName = left.name.localeCompare(right.name);
        return byName === 0 ? left.id.localeCompare(right.id) : byName * direction;
      }
      const a = left[this.sortKey];
      const b = right[this.sortKey];
      // Portfolios whose spread is undefined sink to the bottom either way,
      // rather than pretending to be the worst performer.
      if (a == null && b == null) return left.name.localeCompare(right.name);
      if (a == null) return 1;
      if (b == null) return -1;
      return a === b ? left.name.localeCompare(right.name) : (a - b) * direction;
    });
  }

  // ── create ──────────────────────────────────────────────────────────────

  openCreate(event?: Event): void {
    this.openerElement = (event?.target as HTMLElement) ?? null;
    this.createOpen = true;
    this.createName = '';
    this.createDescription = '';
    this.createSector = '';
    this.createIndustry = '';
    this.createEntry.clear();
    this.createError = null;
    this.cdr.markForCheck();
  }

  closeCreate(): void {
    this.createOpen = false;
    this.cdr.markForCheck();
    this.restoreFocus();
  }

  // ── symbol entry (shared by the create modal and the drawer's add row) ───

  /**
   * Parse a symbol field into validated chips: upper-cased, de-duplicated, and
   * priced from the cache so the user sees what each member will be anchored
   * to. Runs on blur, which is what makes the off-universe symbols visible
   * before submission rather than after a rejected request.
   */
  parseSymbols(entry: SymbolEntry, onError: (message: string) => void,
               settled?: () => void): void {
    const symbols = this.tokenize(entry.raw);
    entry.raw = symbols.join(', ');
    if (!symbols.length) {
      entry.chips = [];
      this.cdr.markForCheck();
      return;
    }
    this.api.lookupSymbols(symbols.join(','))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          const priced = new Map(response.known.map(known => [known.symbol, known]));
          entry.chips = symbols.map(symbol => {
            const known = priced.get(symbol);
            return {
              symbol,
              known: !!known,
              price: known?.price ?? null,
              name: known?.name ?? ''
            };
          });
          this.cdr.markForCheck();
          settled?.();
        },
        error: error => {
          onError(this.message(error, 'Unable to validate symbols.'));
          this.cdr.markForCheck();
        }
      });
  }

  removeChip(entry: SymbolEntry, symbol: string): void {
    entry.chips = entry.chips.filter(chip => chip.symbol !== symbol);
    entry.raw = entry.symbols.join(', ');
    this.cdr.markForCheck();
  }

  parseCreateSymbols(): void {
    this.parseSymbols(this.createEntry, message => (this.createError = message));
  }

  parseAddSymbols(): void {
    this.parseSymbols(this.addEntry, message => (this.addError = message));
  }

  get canCreate(): boolean {
    return !!this.createName.trim() && !this.createEntry.unknown.length && !this.createSaving;
  }

  submitCreate(): void {
    if (!this.canCreate) return;
    this.createSaving = true;
    this.createError = null;
    this.cdr.markForCheck();
    this.api.create({
      name: this.createName.trim(),
      description: this.createDescription.trim(),
      sector: this.createSector.trim(),
      industry: this.createIndustry.trim(),
      symbols: this.createEntry.symbols
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.createSaving = false;
          this.createOpen = false;
          this.cdr.markForCheck();
          this.load(response.portfolio.id);
        },
        error: error => {
          this.createSaving = false;
          this.createError = this.message(error, 'Unable to create the portfolio.');
          this.cdr.markForCheck();
        }
      });
  }

  // ── detail drawer ───────────────────────────────────────────────────────

  openDetail(row: PortfolioSummary, event?: Event): void {
    // A row click hands back the `<tr>`; focus has to return to something
    // focusable inside it, which is the name button.
    const target = (event?.currentTarget as HTMLElement) ?? null;
    this.openerElement = target?.querySelector<HTMLElement>('.row-open') ?? target;
    this.detail = null;
    this.detailLoading = true;
    this.detailError = null;
    this.editing = false;
    this.memberError = null;
    this.addError = null;
    this.undoSymbol = null;
    this.addEntry.clear();
    this.copyState = 'idle';
    this.cdr.markForCheck();
    this.fetchDetail(row.id);
  }

  private fetchDetail(id: string): void {
    this.api.detail(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.detail = response;
          this.detailLoading = false;
          this.cdr.markForCheck();
        },
        error: error => {
          this.detailLoading = false;
          this.detailError = this.message(error, 'Unable to load this portfolio.');
          this.cdr.markForCheck();
        }
      });
  }

  /**
   * Escape reaches every open overlay, so a confirm dialog layered over the
   * drawer must absorb it — dismissing the confirm should not also throw away
   * the drawer the user was reading.
   */
  onDrawerClosed(): void {
    if (this.deleteOpen) {
      this.deleteOpen = false;
      this.cdr.markForCheck();
      return;
    }
    this.closeDetail();
  }

  closeDetail(): void {
    this.detail = null;
    this.detailLoading = false;
    this.detailError = null;
    this.cdr.markForCheck();
    this.restoreFocus();
  }

  startEditing(): void {
    const portfolio = this.detail?.portfolio;
    if (!portfolio) return;
    this.editName = portfolio.name;
    this.editDescription = portfolio.description;
    this.editSector = portfolio.sector;
    this.editIndustry = portfolio.industry;
    this.editing = true;
    this.memberError = null;
    this.cdr.markForCheck();
  }

  cancelEditing(): void {
    this.editing = false;
    this.cdr.markForCheck();
  }

  saveEditing(): void {
    const portfolio = this.detail?.portfolio;
    if (!portfolio || !this.editName.trim()) return;
    this.editSaving = true;
    this.memberError = null;
    this.cdr.markForCheck();
    this.api.update(portfolio.id, {
      name: this.editName.trim(),
      description: this.editDescription.trim(),
      sector: this.editSector.trim(),
      industry: this.editIndustry.trim()
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.detail = response;
          this.editSaving = false;
          this.editing = false;
          this.cdr.markForCheck();
          this.refreshList();
        },
        error: error => {
          this.editSaving = false;
          this.memberError = this.message(error, 'Unable to save the changes.');
          this.cdr.markForCheck();
        }
      });
  }

  /** Blocked only once validation has actually seen an off-universe symbol —
   *  unvalidated text stays clickable so one click can validate and submit. */
  get canAdd(): boolean {
    return !this.addBusy && !!this.addEntry.raw.trim() && !this.addEntry.unknown.length;
  }

  addMembers(): void {
    const portfolio = this.detail?.portfolio;
    if (!portfolio) return;
    // Chips are the source of truth. Text typed since the last blur has not
    // been validated, so validate first and carry on into the add when it is
    // clean — the user should not have to click Add twice.
    if (this.tokenize(this.addEntry.raw).join(',') !== this.addEntry.symbols.join(',')) {
      this.parseSymbols(
        this.addEntry,
        message => (this.addError = message),
        () => this.submitAdd(portfolio.id)
      );
      return;
    }
    this.submitAdd(portfolio.id);
  }

  /** Off-universe symbols stop here; the chips under the field name them. */
  private submitAdd(portfolioId: string): void {
    if (!this.addEntry.valid) return;
    this.addBusy = true;
    this.addError = null;
    this.undoSymbol = null;
    this.cdr.markForCheck();
    this.api.addSymbols(portfolioId, this.addEntry.symbols)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.detail = response;
          this.addEntry.clear();
          this.addBusy = false;
          this.cdr.markForCheck();
          this.refreshList();
        },
        error: error => {
          this.addBusy = false;
          this.addError = this.message(error, 'Unable to add those symbols.');
          this.cdr.markForCheck();
        }
      });
  }

  removeMember(symbol: string): void {
    const portfolio = this.detail?.portfolio;
    if (!portfolio) return;
    this.addBusy = true;
    this.addError = null;
    this.cdr.markForCheck();
    this.api.removeSymbol(portfolio.id, symbol)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.detail = response;
          this.addBusy = false;
          this.undoSymbol = symbol;
          this.cdr.markForCheck();
          this.refreshList();
        },
        error: error => {
          this.addBusy = false;
          this.addError = this.message(error, `Unable to remove ${symbol}.`);
          this.cdr.markForCheck();
        }
      });
  }

  /** Re-add the symbol just removed. It was in the universe a moment ago, so
   *  this skips the chip round-trip and goes straight back to the server. */
  undoRemove(): void {
    const portfolio = this.detail?.portfolio;
    const symbol = this.undoSymbol;
    if (!portfolio || !symbol) return;
    this.undoSymbol = null;
    this.addBusy = true;
    this.addError = null;
    this.cdr.markForCheck();
    this.api.addSymbols(portfolio.id, [symbol])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.detail = response;
          this.addBusy = false;
          this.cdr.markForCheck();
          this.refreshList();
        },
        error: error => {
          this.addBusy = false;
          this.addError = this.message(error, `Unable to restore ${symbol}.`);
          this.cdr.markForCheck();
        }
      });
  }

  dismissUndo(): void {
    this.undoSymbol = null;
    this.cdr.markForCheck();
  }

  /**
   * Copy the member symbols space-separated, which is what the Momentum and
   * Strategy symbol filters parse (`/[\s,]+/`), so the list pastes straight in.
   * Every member is copied, including ones with no cached data — the list is
   * the portfolio, not the subset that happened to price.
   */
  copySymbols(): void {
    const symbols = (this.detail?.members ?? []).map(member => member.symbol).join(' ');
    if (!symbols) return;
    navigator.clipboard.writeText(symbols)
      .then(() => this.flagCopy('copied'))
      // Clipboard access can be refused (insecure context, denied permission);
      // say so rather than leaving the button looking like it worked.
      .catch(() => this.flagCopy('failed'));
  }

  get copyLabel(): string {
    if (this.copyState === 'copied') return '✓ Copied!';
    return this.copyState === 'failed' ? 'Copy failed' : 'Copy symbols';
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

  confirmDelete(): void {
    this.deleteOpen = true;
    this.cdr.markForCheck();
  }

  cancelDelete(): void {
    this.deleteOpen = false;
    this.cdr.markForCheck();
  }

  deletePortfolio(): void {
    const portfolio = this.detail?.portfolio;
    if (!portfolio) return;
    this.deleting = true;
    this.cdr.markForCheck();
    this.api.remove(portfolio.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.deleting = false;
          this.deleteOpen = false;
          this.detail = null;
          this.cdr.markForCheck();
          this.load();
        },
        error: error => {
          this.deleting = false;
          this.deleteOpen = false;
          this.memberError = this.message(error, 'Unable to delete the portfolio.');
          this.cdr.markForCheck();
        }
      });
  }

  /** Re-read the list quietly so the table matches an edit made in the drawer. */
  private refreshList(): void {
    this.api.list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: response => {
          this.snapshot = response;
          this.rows = this.sortRows(response.portfolios ?? []);
          this.cdr.markForCheck();
        },
        error: () => undefined
      });
  }

  // ── formatting ──────────────────────────────────────────────────────────

  /** Signed percent, one decimal. Sign and color always travel together. */
  pct(value: number | null | undefined): string {
    if (value == null) return '—';
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)}%`;
  }

  /** Signed percentage-point spread; the unit is stated so it reads as a gap. */
  pp(value: number | null | undefined): string {
    if (value == null) return '—';
    return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)} pp`;
  }

  /** Whole dollars — portfolio-level averages are not decision-relevant to the cent. */
  dollars(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 0
    });
  }

  /** Cents shown — per-share prices are compared directly. */
  price(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2
    });
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

  /** The decomposition behind a `vs SPY` cell, kept off the row itself. */
  inceptionTooltip(row: PortfolioSummary): string {
    return `Portfolio ${this.pct(row.inception_return)} vs SPY `
      + `${this.pct(row.spy_inception_return)} since inception ${this.date(row.created_date)}.`;
  }

  ytdTooltip(row: PortfolioSummary): string {
    return `Portfolio ${this.pct(row.ytd_return)} vs SPY ${this.pct(row.spy_ytd_return)} `
      + `year to date.`;
  }

  missingTooltip(row: PortfolioSummary): string {
    return `No cached price data for ${row.missing_data_symbols.join(', ')}. `
      + `Excluded from this portfolio's averages.`;
  }

  memberBaselineTooltip(member: PortfolioMember): string {
    if (!member.has_data) {
      return 'No cached price data for this symbol. It is excluded from the portfolio averages.';
    }
    const base = `Baseline ${this.price(member.inception_baseline_close)} on `
      + `${this.date(member.inception_baseline_date)}.`;
    return member.partial_history
      ? `${base} This symbol had no cached history at the portfolio's creation date, so its first `
        + `available close was used.`
      : base;
  }

  rangePosition(member: PortfolioMember): number {
    return Math.min(100, Math.max(0, member.range_position ?? 0));
  }

  trackBySymbol(_index: number, member: PortfolioMember): string {
    return member.symbol;
  }

  trackById(_index: number, row: PortfolioSummary): string {
    return row.id;
  }

  // ── focus plumbing ──────────────────────────────────────────────────────

  /** Land keyboard focus on a freshly created row so the result is announced. */
  private focusRow(id: string): void {
    setTimeout(() => {
      const element = this.host.nativeElement
        .querySelector<HTMLElement>(`[data-portfolio-id="${id}"] .row-open`);
      element?.focus();
    });
  }

  private restoreFocus(): void {
    const element = this.openerElement;
    this.openerElement = null;
    setTimeout(() => element?.focus());
  }

  private tokenize(raw: string): string[] {
    const seen: string[] = [];
    for (const token of (raw || '').split(/[\s,;]+/)) {
      const symbol = token.trim().toUpperCase().replace(/\./g, '-');
      if (symbol && !seen.includes(symbol)) seen.push(symbol);
    }
    return seen;
  }

  private message(error: any, fallback: string): string {
    const detail = error?.error?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    return error?.message || fallback;
  }
}
