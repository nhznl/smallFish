import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ModalComponent } from '../shared/ui/modal.component';
import { CapabilityStateComponent } from '../shared/ui/capability-state.component';
import { Capability, CapabilityService } from '../api/capability.service';
import { StockService } from '../api/stock.service';
import { RetirementHolding, RetirementGroupSummary, RetirementPortfolioData } from '../model/retirement';
import {
  RetirementOptionsData, RetirementOptionRow, RetirementOptionRisk, RetirementOptionGroup,
  RetirementOptionEvent
} from '../model/retirement-options';
import { OptionsRiskAccount } from '../model/options-ledger';

interface RiskWarning {
  level: 'critical' | 'warn' | 'info';
  message: string;
}

interface Recommendation {
  severity: 'critical' | 'warn' | 'info';
  area: string;
  what: string;
  why: string;
}

interface DonutSlice {
  label: string;
  value: number;
  pct: number;
  color: string;
  d: string;           // SVG path of the annular segment
  labelX: number;
  labelY: number;
  anchor: 'start' | 'end';
  showLabel: boolean;
}

interface IndustryBar {
  industry: string;
  invested: number;
  current: number;
  gainLossPct: number;
  investedW: number;   // % width relative to chart max
  currentW: number;
}

interface ChartTip {
  x: number;
  y: number;
  label: string;
  value: number;
  pct: number;
}

interface CorrelationCluster {
  industry: string;
  holdings: RetirementHolding[];
  totalValue: number;
  totalPct: number;
  riskNote: string;
  severity: 'high' | 'medium' | 'low';
}

@Component({
  selector: 'app-retirement-portfolio',
  standalone: true,
  imports: [CommonModule, FormsModule, MatTooltipModule, ModalComponent, CapabilityStateComponent],
  templateUrl: './retirement-portfolio.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./retirement-portfolio.component.css']
})
export class RetirementPortfolioComponent implements OnInit {
  private stockService = inject(StockService);
  private capabilityService = inject(CapabilityService);

  /** An unconfigured brokerage and an empty account are different states. */
  snaptrade: Capability | null = null;
  /** SnapTrade alone cannot supply exact-contract Greeks or beta. */
  retirementRisk: Capability | null = null;

  data: RetirementPortfolioData | null = null;
  loading = false;
  syncing = false;
  snapshotting = false;
  syncedAt: Date | null = null;
  error: string | null = null;
  syncMessage = '';
  tab: 'holdings' | 'analysis' | 'options' = 'holdings';

  // Options sub-view (trade groups + broker risk positions)
  optionsData: RetirementOptionsData | null = null;
  detailGroup: RetirementOptionGroup | null = null;
  optionsLoading = false;
  optionsError: string | null = null;
  optionsMessage = '';
  optionGroupSaving = false;
  optionGroupMessage = '';
  strikeSortDirection: 'asc' | 'desc' = 'asc';
  readonly nearStrikeThresholdPercent = 5;

  // Enrichment editor (category/industry/note live in an editable CSV,
  // separate from immutable broker facts)
  editing: { symbol: string; category: string; industry: string; note: string } | null = null;
  saving = false;
  editError: string | null = null;

  // Filters
  filterCategory = '';
  filterAccount = '';
  filterSymbol = '';
  showOnlyAlerts = false;

  // Derived
  filteredHoldings: RetirementHolding[] = [];
  categoryEntries: [string, RetirementGroupSummary][] = [];
  industryEntries: [string, RetirementGroupSummary][] = [];
  accountEntries: [string, RetirementGroupSummary][] = [];
  riskWarnings: RiskWarning[] = [];
  recommendations: Recommendation[] = [];
  correlationClusters: CorrelationCluster[] = [];

  // Charts (current-value allocation donuts + industry performance bars)
  categoryDonut: DonutSlice[] = [];
  industryDonut: DonutSlice[] = [];
  industryBars: IndustryBar[] = [];
  industryBarMax = 0;
  chartTip: ChartTip | null = null;

  // Fixed categorical hues (dataviz palette, validated for CVD + contrast).
  private readonly categoryColors: Record<string, string> = {
    GROWTH: '#008300', CASH: '#eda100', FUND: '#2a78d6',
    SPECULATIVE: '#eb6834', DIV: '#1baf7a', UNCLASSIFIED: '#4a3aa7',
  };
  private readonly palette = [
    '#2a78d6', '#008300', '#e87ba4', '#eda100', '#1baf7a',
    '#eb6834', '#4a3aa7', '#e34948',
  ];

  // Sort state for holdings table
  sortCol: keyof RetirementHolding = 'currentValue';
  sortAsc = false;

  // Copy button feedback
  copySuccess = false;
  private syncMessageTimer?: ReturnType<typeof setTimeout>;
  private optionsMessageTimer?: ReturnType<typeof setTimeout>;

  ngOnInit(): void {
    this.capabilityService.get('snaptrade')
      .subscribe(capability => this.snaptrade = capability);
    this.capabilityService.get('retirement-risk')
      .subscribe(capability => this.retirementRisk = capability);
    this.load();
    this.loadOptions();
  }

  loadOptions(): void {
    this.optionsLoading = true;
    this.optionsError = null;
    this.stockService.getRetirementOptions().subscribe({
      next: (d) => {
        this.optionsData = d;
        if (!d) this.optionsError = 'Failed to load retirement options.';
        this.optionsLoading = false;
      },
      error: () => {
        this.optionsError = 'Failed to load retirement options.';
        this.optionsLoading = false;
      }
    });
  }

  load(): void {
    this.loading = true;
    this.error = null;
    this.stockService.getRetirementPortfolio().subscribe({
      next: (d) => {
        this.applyPortfolioData(d);
        this.loading = false;
      },
      error: () => {
        this.error = 'Failed to load retirement portfolio. Is the API server running?';
        this.loading = false;
      }
    });
  }

  private applyPortfolioData(d: RetirementPortfolioData): void {
    // Additive API fields default cleanly when the UI is briefly paired with an
    // older backend process during a local restart.
    d.gainLossSnapshots ??= [];
    d.holdings.forEach(holding => holding.gainLossSnapshots ??= {});
    this.data = d;
    this.categoryEntries = Object.entries(d.byCategory);
    this.industryEntries = Object.entries(d.byIndustry);
    this.accountEntries = Object.entries(d.byAccountType);
    this.computeWarnings(d);
    this.computeRecommendations(d);
    this.computeCorrelationClusters(d);
    this.computeCharts(d);
    this.applyFilters();
  }

  /** Pull fresh holdings from the broker via SnapTrade, then reload the view. */
  sync(): void {
    this.syncing = true;
    this.error = null;
    this.syncedAt = null;
    this.syncMessage = '';
    this.stockService.syncRetirementHoldings().subscribe({
      next: (report) => {
        this.syncing = false;
        this.syncedAt = new Date();
        this.flashSyncMessage(this.fidelitySyncMessage(report));
        this.load();
        this.loadOptions();
      },
      error: (err) => {
        this.error = 'Sync from Fidelity failed: '
          + (err?.error?.detail ?? err?.message ?? 'unknown error');
        this.syncing = false;
      }
    });
  }

  /** Capture the current column using the broker sync date as its identity. */
  captureGainLossSnapshot(): void {
    this.snapshotting = true;
    this.error = null;
    this.syncMessage = '';
    this.stockService.captureRetirementGainLossSnapshot().subscribe({
      next: ({ snapshot, portfolio }) => {
        this.snapshotting = false;
        this.applyPortfolioData(portfolio);
        const action = snapshot.replaced ? 'replaced' : 'saved';
        this.flashSyncMessage(
          `G/L % snapshot for ${this.snapshotDateLabel(snapshot.syncDate)} ${action}. ` +
          `${snapshot.snapshotCount} of 3 snapshot dates retained.`
        );
      },
      error: (err) => {
        this.error = 'G/L % snapshot failed: '
          + (err?.error?.detail ?? err?.message ?? 'unknown error');
        this.snapshotting = false;
      },
    });
  }

  snapshotDateLabel(syncDate: string): string {
    const parsed = new Date(`${syncDate}T00:00:00Z`);
    return Number.isNaN(parsed.getTime())
      ? syncDate
      : parsed.toLocaleDateString('en-US', {
          month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
        });
  }

  private fidelitySyncMessage(report: { sync?: {
    accounts_synced: number; positions_synced: number; added: number;
    changed: number; unchanged: number; removed: number;
  } } | null): string {
    const sync = report?.sync;
    if (!sync) return 'Fidelity sync complete; portfolio data refreshed.';

    const positionLabel = `${sync.positions_synced} position${sync.positions_synced === 1 ? '' : 's'}`;
    if (sync.positions_synced === 0) {
      return `Fidelity sync complete: no positions returned; ${sync.removed} prior position${sync.removed === 1 ? '' : 's'} removed from the local ledger.`;
    }

    const accountLabel = `${sync.accounts_synced} account${sync.accounts_synced === 1 ? '' : 's'}`;
    return `Fidelity sync complete: ${positionLabel} across ${accountLabel}. ` +
      `${sync.added} added, ${sync.changed} changed, ${sync.unchanged} unchanged, ${sync.removed} removed.`;
  }

  private flashSyncMessage(message: string): void {
    this.syncMessage = message;
    clearTimeout(this.syncMessageTimer);
    this.syncMessageTimer = setTimeout(() => (this.syncMessage = ''), 8000);
  }

  private flashOptionsMessage(message: string): void {
    this.optionsMessage = message;
    clearTimeout(this.optionsMessageTimer);
    this.optionsMessageTimer = setTimeout(() => (this.optionsMessage = ''), 6000);
  }

  // ── Options: trade groups + broker risk positions ────────────────────────

  saveOptionGroup(group: RetirementOptionGroup): void {
    this.optionsMessage = '';
    this.optionGroupMessage = '';
    this.optionsError = null;
    this.optionGroupSaving = true;
    this.stockService.updateRetirementOptionGroup(group.symbol, {
      name: group.name, status: group.status, notes: group.notes,
    }).subscribe({
      next: (saved) => {
        this.optionGroupSaving = false;
        if (!saved) {
          this.optionsError = `Could not save ${group.symbol}.`;
          return;
        }
        group.name = saved.name ?? group.name;
        group.status = saved.status ?? group.status;
        group.notes = saved.notes ?? group.notes;
        const message = `${group.symbol} saved as ${group.status === 'ARCHIVED' ? 'Archived' : 'Active'}.`;
        this.optionGroupMessage = message;
        this.flashOptionsMessage(message);
      },
      error: (err) => {
        this.optionGroupSaving = false;
        this.optionsError = err?.error?.detail ?? `Could not save ${group.symbol}.`;
      },
    });
  }

  optionPositionRisk(row: RetirementOptionRow): RetirementOptionRisk | undefined {
    return this.optionsData?.risk.positions.find(p => p.row_id === row.id);
  }

  /** Open the broker-events drill-down for one trade group. */
  openOptionGroupDetails(group: RetirementOptionGroup): void {
    this.optionGroupMessage = '';
    this.optionsError = null;
    this.detailGroup = group;
  }

  closeOptionGroupDetails(): void {
    if (this.optionGroupSaving) return;
    this.detailGroup = null;
    this.optionGroupMessage = '';
  }

  /** Broker option events for the open detail group (already newest-first). */
  detailOptionEvents(): RetirementOptionEvent[] {
    if (!this.detailGroup) return [];
    return (this.optionsData?.events ?? []).filter(
      e => e.underlying_symbol === this.detailGroup!.symbol
    );
  }

  /** Current underlying price for a group's symbol, from a live leg if present. */
  optionGroupSpot(symbol: string): number | null {
    const pos = this.optionsData?.risk?.positions?.find(p => p.symbol === symbol && p.spot != null);
    if (pos?.spot != null) return pos.spot;
    const row = this.optionsData?.rows?.find(r => r.symbol === symbol && r.current_underlying_price != null);
    return row?.current_underlying_price ?? null;
  }

  // ── Totals (mirror the options ledger's "Filtered Totals" row) ───────────

  private sumGroups(field: 'net_cash_flow' | 'open_market_value' | 'total_pnl'): number {
    return (this.optionsData?.groups ?? []).reduce((total, g) => total + (g[field] ?? 0), 0);
  }

  groupsTotalNetCredit(): number { return this.sumGroups('net_cash_flow'); }
  groupsTotalMarketValue(): number { return this.sumGroups('open_market_value'); }
  groupsTotalPnl(): number { return this.sumGroups('total_pnl'); }

  /** Net beta-weighted delta $ across positions where risk was computable
   *  (null if any open position is missing it, so the total never understates). */
  riskTotalBetaDelta(): number | null {
    const positions = this.optionsData?.risk.positions ?? [];
    if (positions.some(p => p.beta_weighted_delta_dollars == null)) return null;
    return positions.reduce((total, p) => total + (p.beta_weighted_delta_dollars ?? 0), 0);
  }

  /** Net beta-weighted delta $ using smallFish's own computed beta (same gate). */
  riskTotalComputedBetaDelta(): number | null {
    const positions = this.optionsData?.risk.positions ?? [];
    if (positions.some(p => p.computed_beta_weighted_delta_dollars == null)) return null;
    return positions.reduce((total, p) => total + (p.computed_beta_weighted_delta_dollars ?? 0), 0);
  }

  // ── Portfolio Risk (mirrors the options ledger's Portfolio Risk cards) ────
  retirementAccountEntries(): [string, OptionsRiskAccount][] {
    return Object.entries(this.optionsData?.risk.accounts ?? {});
  }

  targetBandDollars(account: OptionsRiskAccount, boundary: 'min' | 'max'): number | null {
    if (account.cash_limit == null) return null;
    const normalized = boundary === 'min' ? account.band.band_min : account.band.band_max;
    return account.cash_limit * normalized;
  }

  ivSourceLabel(source: string | null | undefined): string {
    const labels: Record<string, string> = {
      TASTYTRADE_IV: 'Tastytrade live', CHAIN_IV: 'Chain IV', RV_FALLBACK: 'RV fallback',
    };
    return source ? (labels[source] ?? source) : 'Unavailable';
  }

  riskStatus(position: RetirementOptionRisk | undefined): string {
    if (!position) return 'Unavailable';
    const labels: Record<string, string> = {
      MISSING_BETA: 'Missing beta', MISSING_SPOT: 'Missing spot price',
      MISSING_VOL: 'Missing volatility',
      STALE_VOL: 'Stale volatility', STALE_BETA: 'Stale Tasty Beta',
      PAST_EXPIRY_OPEN: 'Past expiry', UNSUPPORTED_TYPE: 'Unsupported position type',
    };
    return position.beta_weighted_delta_dollars != null
      ? 'Included'
      : (position.unavailable_reasons.map(r => labels[r] ?? r).join(', ') || 'Unavailable');
  }

  toggleStrikeSort(): void {
    this.strikeSortDirection = this.strikeSortDirection === 'asc' ? 'desc' : 'asc';
  }

  sortedRiskRows(): RetirementOptionRow[] {
    const rows = [...(this.optionsData?.rows ?? [])];
    const direction = this.strikeSortDirection === 'asc' ? 1 : -1;
    return rows.sort((left, right) => {
      const ld = this.strikeRiskDistance(left);
      const rd = this.strikeRiskDistance(right);
      if (ld == null && rd == null) return 0;
      if (ld == null) return 1;
      if (rd == null) return -1;
      return direction * (ld - rd);
    });
  }

  strikeRiskDistance(row: RetirementOptionRow): number | null {
    const distance = row.percent_to_strike;
    if (distance == null) return null;
    if (row.trade_type === 'SHORT_PUT') return distance;
    if (row.trade_type === 'SHORT_CALL' || row.trade_type === 'COVERED_CALL') return -distance;
    return Math.abs(distance);
  }

  strikeDistanceLabel(row: RetirementOptionRow): string {
    const distance = this.strikeRiskDistance(row);
    if (distance == null) return '—';
    const isShort = ['SHORT_PUT', 'SHORT_CALL', 'COVERED_CALL'].includes(row.trade_type);
    if (!isShort) return `${Math.abs(distance).toFixed(2)}% from strike`;
    return distance <= 0
      ? `${Math.abs(distance).toFixed(2)}% past strike`
      : `${distance.toFixed(2)}% from breach`;
  }

  isStrikeBreached(row: RetirementOptionRow): boolean {
    const distance = this.strikeRiskDistance(row);
    return ['SHORT_PUT', 'SHORT_CALL', 'COVERED_CALL'].includes(row.trade_type)
      && distance != null && distance <= 0;
  }

  isNearStrike(row: RetirementOptionRow): boolean {
    const distance = this.strikeRiskDistance(row);
    return ['SHORT_PUT', 'SHORT_CALL', 'COVERED_CALL'].includes(row.trade_type)
      && distance != null && distance > 0 && distance <= this.nearStrikeThresholdPercent;
  }

  optionRowClass(row: RetirementOptionRow): string {
    const classes: string[] = [];
    if (row.needs_settlement) classes.push('settlement-row');
    if (this.isStrikeBreached(row)) classes.push('strike-breached-row');
    else if (this.isNearStrike(row)) classes.push('strike-near-row');
    return classes.join(' ');
  }

  money(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  optionPrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 3,
    });
  }

  num(value: number | null | undefined, digits = 2): string {
    return value == null ? '—' : value.toFixed(digits);
  }

  pct(value: number | null | undefined): string {
    return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }

  shortTs(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  /** Open the enrichment editor for a holding (options edit their underlying). */
  openEditor(h: RetirementHolding): void {
    this.editing = {
      symbol: h.enrichmentSymbol || h.symbol,
      category: h.category === 'UNCLASSIFIED' ? '' : h.category,
      industry: h.industry === 'UNCLASSIFIED' ? '' : h.industry,
      note: h.note,
    };
    this.editError = null;
  }

  closeEditor(): void {
    this.editing = null;
    this.editError = null;
  }

  saveEnrichment(): void {
    if (!this.editing) return;
    const { symbol, category, industry, note } = this.editing;
    this.saving = true;
    this.editError = null;
    this.stockService.updateRetirementEnrichment(symbol, { category, industry, note }).subscribe({
      next: () => {
        this.saving = false;
        this.editing = null;
        this.load();
      },
      error: (err) => {
        this.editError = err?.error?.detail ?? err?.message ?? 'save failed';
        this.saving = false;
      }
    });
  }

  uniqueIndustries(): string[] {
    return this.data
      ? [...new Set(this.data.holdings.map(h => h.industry)
          .filter(i => !!i && i !== 'UNCLASSIFIED'))].sort()
      : [];
  }

  applyFilters(): void {
    if (!this.data) return;
    let rows = [...this.data.holdings];
    if (this.filterCategory) rows = rows.filter(h => h.category === this.filterCategory);
    if (this.filterAccount) rows = rows.filter(h => h.accountType === this.filterAccount);
    if (this.filterSymbol) {
      const q = this.filterSymbol.toLowerCase();
      rows = rows.filter(h => h.symbol.toLowerCase().includes(q) || h.industry.toLowerCase().includes(q));
    }
    if (this.showOnlyAlerts) rows = rows.filter(h => h.trend.alert);
    this.filteredHoldings = this.sortRows(rows);
  }

  sortBy(col: keyof RetirementHolding): void {
    if (this.sortCol === col) {
      this.sortAsc = !this.sortAsc;
    } else {
      this.sortCol = col;
      this.sortAsc = col === 'symbol' || col === 'category' || col === 'industry';
    }
    this.filteredHoldings = this.sortRows(this.filteredHoldings);
  }

  private sortRows(rows: RetirementHolding[]): RetirementHolding[] {
    return [...rows].sort((a, b) => {
      const av = a[this.sortCol] as any;
      const bv = b[this.sortCol] as any;
      if (typeof av === 'string') return this.sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return this.sortAsc ? (av ?? -Infinity) - (bv ?? -Infinity) : (bv ?? -Infinity) - (av ?? -Infinity);
    });
  }

  sortIcon(col: keyof RetirementHolding): string {
    if (this.sortCol !== col) return '';
    return this.sortAsc ? ' ▲' : ' ▼';
  }

  bandBadgeClass(inBand: boolean | null | undefined): string {
    if (inBand == null) return 'badge-neutral';
    return inBand ? 'badge-pos' : 'badge-neg';
  }

  /** Announced sort state for the column header. */
  ariaSort(col: keyof RetirementHolding): 'ascending' | 'descending' | 'none' {
    if (this.sortCol !== col) return 'none';
    return this.sortAsc ? 'ascending' : 'descending';
  }

  /** Share of current value sitting in CASH holdings. */
  cashPct(): number {
    const total = this.data?.totalCurrent ?? 0;
    if (!total) return 0;
    const cash = (this.data?.holdings ?? [])
      .filter(h => (h.category || '').toUpperCase() === 'CASH')
      .reduce((sum, h) => sum + (h.currentValue ?? 0), 0);
    return (cash / total) * 100;
  }

  uniqueCategories(): string[] {
    return this.data ? [...new Set(this.data.holdings.map(h => h.category).filter(c => !!c))] : [];
  }

  uniqueAccounts(): string[] {
    return this.data ? [...new Set(this.data.holdings.map(h => h.accountType).filter(a => !!a))] : [];
  }

  clearFilters(): void {
    this.filterCategory = '';
    this.filterAccount = '';
    this.filterSymbol = '';
    this.applyFilters();
  }

  /** Make the allocation legend a shortcut into the existing holdings filters. */
  filterByCategory(category: string): void {
    if (category === 'Other') return;
    this.filterCategory = this.filterCategory === category ? '' : category;
    this.filterSymbol = '';
    this.applyFilters();
  }

  /** Industry is filtered through the existing symbol/industry search field. */
  filterByIndustry(industry: string): void {
    if (industry === 'Other') return;
    this.filterSymbol = this.filterSymbol === industry ? '' : industry;
    this.filterCategory = '';
    this.applyFilters();
  }

  copySymbols(): void {
    if (!this.data) return;
    const seen = new Set<string>();
    const symbols = this.data.holdings
      .filter(h => h.category !== 'CASH' && h.category !== 'FUND' && h.symbol)
      .map(h => h.symbol)
      .filter(s => { if (seen.has(s)) return false; seen.add(s); return true; })
      .join(' ');
    navigator.clipboard.writeText(symbols).then(() => {
      this.copySuccess = true;
      setTimeout(() => this.copySuccess = false, 2000);
    });
  }

  gainLossClass(v: number | null | undefined): string {
    if (v == null) return '';
    return v >= 0 ? 'positive' : 'negative';
  }

  /** Holdings currently flagged as declining (gain shrinking / loss deepening). */
  get alertCount(): number {
    return (this.data?.holdings ?? []).filter(h => h.trend.alert).length;
  }

  /** Human-readable detail for a holding's trend alert. */
  trendTooltip(h: RetirementHolding): string {
    const t = h.trend;
    if (!t.alert || t.fromPct == null || t.toPct == null || t.dropPct == null) return '';
    const sign = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
    const when = t.alertAt ? ' (' + new Date(t.alertAt).toLocaleDateString() + ')' : '';
    const drop = t.dropPct.toFixed(0);
    return t.direction === 'LOSS'
      ? `Loss deepened ${drop}% since the last sync: ${sign(t.fromPct)} → ${sign(t.toPct)}${when}. Consider cutting the loss.`
      : `Gain shrank ${drop}% from its peak: ${sign(t.fromPct)} → ${sign(t.toPct)}${when}. Consider booking profits.`;
  }

  barClass(pct: number, warnAt = 25, critAt = 35): string {
    if (pct > critAt) return 'bar-fill bar-critical';
    if (pct > warnAt) return 'bar-fill bar-warn';
    return 'bar-fill bar-ok';
  }

  categoryColor(cat: string): string {
    const colors: Record<string, string> = {
      CASH: '#90a4ae',
      FUND: '#5c6bc0',
      GROWTH: '#2e7d32',
      DIV: '#00838f',
      SPECULATIVE: '#e65100',
    };
    return colors[cat] || '#757575';
  }

  severityClass(s: string): string {
    if (s === 'critical' || s === 'high') return 'warn-critical';
    if (s === 'warn'     || s === 'medium') return 'warn-yellow';
    return 'warn-info';
  }

  severityIcon(s: string): string {
    if (s === 'critical' || s === 'high') return '🔴';
    if (s === 'warn'     || s === 'medium') return '🟡';
    return 'ℹ️';
  }

  // ── Charts ───────────────────────────────────────────────────────────────

  private colorForCategory(name: string, index: number): string {
    return this.categoryColors[name] ?? this.palette[index % this.palette.length];
  }

  /** Build annular slices from a byX summary map, largest first, small tail
   *  folded into "Other" so no chart carries more than the palette. */
  private buildDonut(
    groups: Record<string, RetirementGroupSummary>,
    colorFn: (name: string, i: number) => string,
  ): DonutSlice[] {
    const total = Object.values(groups).reduce((s, g) => s + g.currentValue, 0);
    if (total <= 0) return [];

    let entries = Object.entries(groups)
      .filter(([, g]) => g.currentValue > 0)
      .sort((a, b) => b[1].currentValue - a[1].currentValue);

    // Fold a long tail (past 7) into a single "Other" wedge.
    if (entries.length > 8) {
      const head = entries.slice(0, 7);
      const tail = entries.slice(7);
      const otherVal = tail.reduce((s, [, g]) => s + g.currentValue, 0);
      entries = [...head, ['Other', { currentValue: otherVal } as RetirementGroupSummary]];
    }

    const cx = 100, cy = 100, rOuter = 92, rInner = 56;
    let angle = -Math.PI / 2;   // start at 12 o'clock
    const slices: DonutSlice[] = [];
    entries.forEach(([label, g], i) => {
      const frac = g.currentValue / total;
      const start = angle;
      const end = angle + frac * 2 * Math.PI;
      angle = end;
      const mid = (start + end) / 2;
      const large = end - start > Math.PI ? 1 : 0;

      const p = (r: number, a: number) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
      const [x0, y0] = p(rOuter, start);
      const [x1, y1] = p(rOuter, end);
      const [x2, y2] = p(rInner, end);
      const [x3, y3] = p(rInner, start);
      const d = `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1} `
              + `L ${x2} ${y2} A ${rInner} ${rInner} 0 ${large} 0 ${x3} ${y3} Z`;

      const [lx, ly] = p(rOuter + 12, mid);
      slices.push({
        label, value: g.currentValue, pct: frac * 100,
        color: colorFn(label, i), d,
        labelX: lx, labelY: ly,
        anchor: Math.cos(mid) >= 0 ? 'start' : 'end',
        showLabel: frac >= 0.05,   // only label slices ≥ 5%
      });
    });
    return slices;
  }

  private computeCharts(d: RetirementPortfolioData): void {
    this.categoryDonut = this.buildDonut(
      d.byCategory, (name, i) => this.colorForCategory(name, i));
    this.industryDonut = this.buildDonut(
      d.byIndustry, (_name, i) => this.palette[i % this.palette.length]);

    // Industry performance: invested vs current, excluding cash/fund, sorted
    // by return so the best-performing sector reads at the top.
    const bars = Object.entries(d.byIndustry)
      .filter(([ind]) => ind !== 'CASH' && ind !== 'FUND')
      .map(([industry, g]) => ({
        industry,
        invested: g.initialValue,
        current: g.currentValue,
        gainLossPct: g.gainLossPct,
      }))
      .filter(b => b.invested > 0 || b.current > 0)
      .sort((a, b) => b.gainLossPct - a.gainLossPct);

    const max = Math.max(1, ...bars.map(b => Math.max(b.invested, b.current)));
    this.industryBarMax = max;
    this.industryBars = bars.map(b => ({
      ...b,
      investedW: (b.invested / max) * 100,
      currentW: (b.current / max) * 100,
    }));
  }

  showTip(evt: MouseEvent, label: string, value: number, pct: number): void {
    const host = (evt.currentTarget as SVGElement).closest('.charts-row') as HTMLElement;
    const rect = host.getBoundingClientRect();
    this.chartTip = {
      x: evt.clientX - rect.left,
      y: evt.clientY - rect.top,
      label, value, pct,
    };
  }

  hideTip(): void {
    this.chartTip = null;
  }

  // ── Analysis computations ────────────────────────────────────────────────

  private computeWarnings(d: RetirementPortfolioData): void {
    const warnings: RiskWarning[] = [];
    for (const [industry, s] of Object.entries(d.byIndustry)) {
      if (industry === 'CASH' || industry === 'FUND') continue;
      if (s.pctOfPortfolio > 20) {
        warnings.push({ level: s.pctOfPortfolio > 30 ? 'critical' : 'warn',
          message: `${industry} is ${s.pctOfPortfolio.toFixed(1)}% of portfolio` });
      }
    }
    const spec = d.byCategory['SPECULATIVE'];
    if (spec?.pctOfPortfolio > 12)
      warnings.push({ level: spec.pctOfPortfolio > 18 ? 'critical' : 'warn',
        message: `SPECULATIVE holdings are ${spec.pctOfPortfolio.toFixed(1)}% of portfolio` });
    const cash = d.byCategory['CASH'];
    if (cash?.pctOfPortfolio > 25)
      warnings.push({ level: 'info', message: `${cash.pctOfPortfolio.toFixed(1)}% is in CASH` });
    for (const h of d.topPositions.slice(0, 5)) {
      if (h.pctOfTotal > 7)
        warnings.push({ level: h.pctOfTotal > 12 ? 'critical' : 'warn',
          message: `${h.symbol} is ${h.pctOfTotal.toFixed(1)}% of total portfolio` });
    }
    this.riskWarnings = warnings;
  }

  private computeRecommendations(d: RetirementPortfolioData): void {
    const recs: Recommendation[] = [];

    // Over-concentrated industries
    for (const [ind, s] of Object.entries(d.byIndustry)) {
      if (ind === 'CASH' || ind === 'FUND') continue;
      if (s.pctOfPortfolio > 20) {
        recs.push({
          severity: s.pctOfPortfolio > 30 ? 'critical' : 'warn',
          area: 'Sector Concentration',
          what: `Trim ${ind} from ${s.pctOfPortfolio.toFixed(1)}% down to 15–20%`,
          why: `${ind} is your dominant sector. A sector-specific shock (regulation, earnings, macro) would disproportionately hit your retirement savings. Spread into uncorrelated sectors or index funds.`
        });
      }
    }

    // Speculative in retirement account
    const spec = d.byCategory['SPECULATIVE'];
    if (spec) {
      const targetPct = 10;
      if (spec.pctOfPortfolio > targetPct) {
        const excessDollars = ((spec.pctOfPortfolio - targetPct) / 100) * d.totalCurrent;
        recs.push({
          severity: spec.pctOfPortfolio > 18 ? 'critical' : 'warn',
          area: 'Risk Category',
          what: `Reduce SPECULATIVE from ${spec.pctOfPortfolio.toFixed(1)}% to < ${targetPct}% (move ~$${Math.round(excessDollars / 1000)}K to GROWTH or FUND)`,
          why: `Speculative holdings have high individual failure rates. In a retirement account you cannot easily recover from a 50–80% loss on multiple bets. Target < 10% speculative for a long-horizon account.`
        });
      }
    }

    // Cash drag
    const cash = d.byCategory['CASH'];
    if (cash?.pctOfPortfolio > 20) {
      recs.push({
        severity: 'info',
        area: 'Cash Drag',
        what: `Deploy some of the $${Math.round(cash.currentValue / 1000)}K cash — consider systematic DCA into index funds`,
        why: `${cash.pctOfPortfolio.toFixed(1)}% in cash earns ~4–5% while your equity portfolio may return 10%+ long-term. Large idle cash is an opportunity cost in a retirement account.`
      });
    }

    // Duplicate symbols across accounts
    const symbolCounts = new Map<string, number>();
    for (const h of d.holdings.filter(h => h.category !== 'CASH' && h.category !== 'FUND')) {
      symbolCounts.set(h.symbol, (symbolCounts.get(h.symbol) ?? 0) + 1);
    }
    for (const [sym, count] of symbolCounts) {
      if (count > 1) {
        recs.push({
          severity: 'info',
          area: 'Duplicate Position',
          what: `${sym} appears in ${count} separate account entries — track as a single position`,
          why: `Seeing ${sym} split across accounts can mask how large your actual exposure is. Aggregate it to evaluate true concentration risk.`
        });
      }
    }

    // Oversized single positions
    for (const h of d.topPositions.slice(0, 5)) {
      if (h.pctOfTotal > 7) {
        const potentialLoss = (h.pctOfTotal * 0.5).toFixed(1);
        recs.push({
          severity: h.pctOfTotal > 12 ? 'critical' : 'warn',
          area: 'Single Stock Risk',
          what: `Trim ${h.symbol} from ${h.pctOfTotal.toFixed(1)}% → target < 5% for retirement`,
          why: `A 50% decline in ${h.symbol} alone would cost your portfolio ${potentialLoss}%. Single-stock risk is uncompensated risk — you're not getting paid extra for it vs. holding an ETF.`
        });
      }
    }

    // No uncorrelated diversifiers
    const hasBonds = d.holdings.some(h => h.industry === 'BOND' || h.industry === 'FIXED INCOME');
    const internationalPct = (d.byIndustry['EM']?.pctOfPortfolio ?? 0);
    if (!hasBonds && d.totalCurrent > 200_000) {
      recs.push({
        severity: 'info',
        area: 'Diversification Gap',
        what: `Consider adding bonds or bond ETFs (TLT, AGG, BND) for negative correlation during equity selloffs`,
        why: `Your portfolio has no fixed-income. Bonds and equities are historically negatively correlated — when stocks fall, bonds often rise, cushioning drawdowns. This matters most for retirement accounts.`
      });
    }
    if (internationalPct < 10) {
      recs.push({
        severity: 'info',
        area: 'Geographic Concentration',
        what: `Increase international/EM allocation beyond ${internationalPct.toFixed(1)}% — consider VEA (developed) or VWO (EM)`,
        why: `Heavy US equity exposure means your returns are highly correlated with US market cycles. International markets often diverge, providing genuine diversification.`
      });
    }

    this.recommendations = recs;
  }

  private computeCorrelationClusters(d: RetirementPortfolioData): void {
    const byIndustry = new Map<string, RetirementHolding[]>();
    for (const h of d.holdings) {
      if (h.category === 'CASH' || h.category === 'FUND' || !h.industry) continue;
      if (!byIndustry.has(h.industry)) byIndustry.set(h.industry, []);
      byIndustry.get(h.industry)!.push(h);
    }

    const clusters: CorrelationCluster[] = [];
    for (const [industry, holdings] of byIndustry) {
      if (holdings.length < 2) continue;
      const totalValue = holdings.reduce((s, h) => s + (h.currentValue ?? 0), 0);
      const totalPct = holdings.reduce((s, h) => s + (h.pctOfTotal ?? 0), 0);
      const specCount = holdings.filter(h => h.category === 'SPECULATIVE').length;
      const severity: 'high' | 'medium' | 'low' =
        specCount >= 2 ? 'high' : totalPct > 5 ? 'medium' : 'low';
      clusters.push({
        industry,
        holdings: [...holdings].sort((a, b) => (b.currentValue ?? 0) - (a.currentValue ?? 0)),
        totalValue,
        totalPct,
        riskNote: this.clusterRiskNote(industry, holdings),
        severity
      });
    }

    clusters.sort((a, b) => b.totalValue - a.totalValue);
    this.correlationClusters = clusters;
  }

  private clusterRiskNote(industry: string, holdings: RetirementHolding[]): string {
    const specCount = holdings.filter(h => h.category === 'SPECULATIVE').length;
    const notes: Record<string, string> = {
      TECH:       'Large-cap and mid-cap tech move together — high correlation to QQQ/Nasdaq',
      AVIATION:   'eVTOL / Urban Air Mobility stocks share the same early-stage regulatory and adoption risk. They will rise and fall together.',
      EM:         'EM ETFs have high cross-country correlation during global risk-off (dollar strengthens, all EM sells off)',
      SEMI:       'Semiconductors are a cyclical sector — all names are driven by the same inventory cycles and China-trade headlines',
      MINING:     'Mining stocks track commodity prices; diversification within the sector is limited',
      NUCLEAR:    'Nuclear energy plays move on the same energy-policy and uranium-supply narrative',
      FINTECH:    'Fintechs share rate sensitivity and regulatory risk — correlated to each other and to banks',
      REIT:       'REITs move together with interest rate changes — all affected by Fed policy simultaneously',
      CONSUMER:   'Consumer staples and discretionary can diverge, but same-sector names still correlate',
      DEFENSE:    'Defense stocks move on geopolitical news and defense budget cycles',
      BIOTECH:    'Biotech is largely binary (FDA trials) — but multi-name biotech exposure means any sector-wide risk-off hits all',
    };
    const base = notes[industry] ?? `Multiple ${industry} stocks will likely move in the same direction`;
    if (specCount >= 2) return base + ` — ${specCount} of these are SPECULATIVE, amplifying downside correlation.`;
    return base;
  }
}
