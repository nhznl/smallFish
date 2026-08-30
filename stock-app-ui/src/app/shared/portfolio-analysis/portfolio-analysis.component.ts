import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BrokerageService } from '../../api/brokerage.service';
import {
  BrokerageId,
  PortfolioAllocationBucket,
  PortfolioAnalysisItem,
  PortfolioAnalysisProfile,
  PortfolioAnalysisProfileUpdate,
  PortfolioAnalysisResponse,
  PortfolioAllocationValue,
  PortfolioFinding,
  PortfolioFindingSeverity,
  PortfolioPreviewRequest,
  PortfolioPreviewResponse,
  PortfolioRole,
} from '../../model/brokerage';
import { formatFixedPercent, formatIsoTimestamp, formatQuantity, formatUsdMoney } from '../format-display';
import { ModalComponent } from '../ui/modal.component';

type ItemSort = 'symbol' | 'account' | 'market_value' | 'weight_pct' | 'allocation_bucket' | 'sector';
type AmountMode = 'QUANTITY' | 'NOTIONAL';

interface ProfileForm extends Required<Omit<
  PortfolioAnalysisProfile, 'objective' | 'reviewed_at' | 'status'
>> {}

interface PreviewForm {
  accountId: string;
  side: 'BUY' | 'SELL';
  symbol: string;
  amountMode: AmountMode;
  amount: number | null;
  assumedPrice: number | null;
  fundingSource: 'ACCOUNT_CASH' | 'NEW_CONTRIBUTION';
  allocationBucket: PortfolioAllocationBucket | '';
}

@Component({
  selector: 'app-portfolio-analysis',
  standalone: true,
  imports: [CommonModule, FormsModule, MatTooltipModule, ModalComponent],
  templateUrl: './portfolio-analysis.component.html',
  styleUrl: './portfolio-analysis.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class PortfolioAnalysisComponent implements OnChanges {
  @Input({ required: true }) brokerageId!: BrokerageId;
  @Input() refreshToken = 0;

  readonly allocationBuckets: PortfolioAllocationBucket[] = [
    'GROWTH', 'SPECULATIVE', 'DEFENSIVE', 'CASH', 'UNKNOWN',
  ];
  readonly ownerAllocationBuckets: PortfolioAllocationBucket[] = [
    'GROWTH', 'SPECULATIVE', 'DEFENSIVE', 'CASH',
  ];
  readonly previewAllocationBuckets: PortfolioAllocationBucket[] = [
    'GROWTH', 'SPECULATIVE', 'DEFENSIVE',
  ];
  readonly skeletonCards = Array.from({ length: 4 });

  data: PortfolioAnalysisResponse | null = null;
  loading = false;
  error = '';
  message = '';
  profileEditorOpen = false;
  profileLoading = false;
  profileSaving = false;
  profileError = '';
  profileForm: ProfileForm = this.emptyProfile();
  classificationEditor: PortfolioAnalysisItem | null = null;
  classificationBucket: PortfolioAllocationBucket | '' = '';
  classificationSaving = false;
  classificationError = '';
  preview: PortfolioPreviewResponse | null = null;
  previewing = false;
  previewError = '';
  previewStatus = '';
  previewForm: PreviewForm = this.emptyPreview();
  itemSort: ItemSort = 'weight_pct';
  itemSortAscending = false;

  private requestSequence = 0;

  constructor(private readonly api: BrokerageService) {}

  ngOnChanges(): void {
    if (this.brokerageId) this.load();
  }

  load(): void {
    const request = ++this.requestSequence;
    this.loading = true;
    this.error = '';
    this.api.getPortfolioAnalysis(this.brokerageId).subscribe({
      next: data => {
        if (request !== this.requestSequence) return;
        this.data = data;
        this.loading = false;
        this.preview = null;
        this.previewStatus = '';
        this.ensurePreviewAccount();
      },
      error: err => {
        if (request !== this.requestSequence) return;
        this.data = null;
        this.loading = false;
        this.error = this.errorMessage(err, 'Portfolio Analysis could not be loaded.');
      },
    });
  }

  get role(): PortfolioRole {
    return this.data?.brokerage.portfolio_role === 'TRADING' ? 'TRADING' : 'RETIREMENT';
  }

  roleObjective(): string {
    return this.role === 'TRADING'
      ? 'Speculative trading risk budget'
      : 'Long-term aggressive growth';
  }

  roleDescription(): string {
    return this.role === 'TRADING'
      ? 'Aggression is allowed; exposure, liquidity, assignment, and stress limits define the guardrails.'
      : 'Growth allocation and concentration are assessed separately so aggressive does not mean unmanaged.';
  }

  findings(): PortfolioFinding[] {
    const rank: Record<PortfolioFindingSeverity, number> = {
      CRITICAL: 0, HIGH: 1, CAUTION: 2, INFO: 3,
    };
    return [...(this.data?.summary.findings ?? [])].sort(
      (left, right) => rank[left.severity] - rank[right.severity]
    );
  }

  sortedItems(): PortfolioAnalysisItem[] {
    return [...(this.data?.items ?? [])].sort((left, right) => {
      const a = left[this.itemSort];
      const b = right[this.itemSort];
      let comparison: number;
      if (typeof a === 'string' && typeof b === 'string') comparison = a.localeCompare(b);
      else comparison = (typeof a === 'number' ? a : Number.NEGATIVE_INFINITY)
        - (typeof b === 'number' ? b : Number.NEGATIVE_INFINITY);
      return this.itemSortAscending ? comparison : -comparison;
    });
  }

  sortItems(column: ItemSort): void {
    if (this.itemSort === column) this.itemSortAscending = !this.itemSortAscending;
    else {
      this.itemSort = column;
      this.itemSortAscending = ['symbol', 'account', 'allocation_bucket', 'sector'].includes(column);
    }
  }

  ariaSort(column: ItemSort): 'ascending' | 'descending' | 'none' {
    if (column !== this.itemSort) return 'none';
    return this.itemSortAscending ? 'ascending' : 'descending';
  }

  sortIcon(column: ItemSort): string {
    if (column !== this.itemSort) return '';
    return this.itemSortAscending ? '▲' : '▼';
  }

  accounts(): Array<{ id: string; label: string }> {
    const accounts = new Map<string, string>();
    for (const account of this.data?.summary.capital.accounts ?? []) {
      if (account.account_id) accounts.set(account.account_id, account.account || account.account_id);
    }
    for (const row of this.data?.items ?? []) {
      if (row.account_id) accounts.set(row.account_id, row.account || row.account_id);
    }
    return [...accounts].map(([id, label]) => ({ id, label }))
      .sort((left, right) => left.label.localeCompare(right.label));
  }

  openProfileEditor(): void {
    this.profileEditorOpen = true;
    this.profileLoading = true;
    this.profileError = '';
    this.profileForm = this.profileFrom(this.data?.summary.profile ?? null);
    this.api.getPortfolioAnalysisProfile(this.brokerageId).subscribe({
      next: result => {
        this.profileLoading = false;
        this.profileForm = this.profileFrom(result.profile);
      },
      error: err => {
        this.profileLoading = false;
        this.profileError = this.errorMessage(err, 'The saved profile could not be loaded.');
      },
    });
  }

  closeProfileEditor(): void {
    if (this.profileSaving) return;
    this.profileEditorOpen = false;
    this.profileError = '';
  }

  saveProfile(): void {
    const common: PortfolioAnalysisProfileUpdate = {
      max_single_issuer_pct: this.numberOrNull(this.profileForm.max_single_issuer_pct),
      max_speculative_pct: this.numberOrNull(this.profileForm.max_speculative_pct),
      max_put_assignment_commitment_pct:
        this.numberOrNull(this.profileForm.max_put_assignment_commitment_pct),
      max_stress_loss_pct: this.numberOrNull(this.profileForm.max_stress_loss_pct),
      minimum_liquid_pct: this.numberOrNull(this.profileForm.minimum_liquid_pct),
      notes: this.profileForm.notes.trim(),
      max_sector_pct: this.numberOrNull(this.profileForm.max_sector_pct),
    };
    const roleFields: PortfolioAnalysisProfileUpdate = this.role === 'TRADING' ? {
      max_gross_exposure_pct: this.numberOrNull(this.profileForm.max_gross_exposure_pct),
      deployment_min_pct: this.numberOrNull(this.profileForm.deployment_min_pct),
      deployment_max_pct: this.numberOrNull(this.profileForm.deployment_max_pct),
    } : {
      growth_min_pct: this.numberOrNull(this.profileForm.growth_min_pct),
      growth_max_pct: this.numberOrNull(this.profileForm.growth_max_pct),
      cash_min_pct: this.numberOrNull(this.profileForm.cash_min_pct),
      cash_max_pct: this.numberOrNull(this.profileForm.cash_max_pct),
      max_top_five_pct: this.numberOrNull(this.profileForm.max_top_five_pct),
      first_expected_withdrawal_date: this.profileForm.first_expected_withdrawal_date || null,
    };
    this.profileSaving = true;
    this.profileError = '';
    this.api.updatePortfolioAnalysisProfile(this.brokerageId, { ...common, ...roleFields }).subscribe({
      next: () => {
        this.profileSaving = false;
        this.profileEditorOpen = false;
        this.message = 'Portfolio profile saved. Analysis has been recalculated.';
        this.load();
      },
      error: err => {
        this.profileSaving = false;
        this.profileError = this.errorMessage(err, 'The profile could not be saved.');
      },
    });
  }

  openClassificationEditor(item: PortfolioAnalysisItem): void {
    this.classificationEditor = item;
    this.classificationBucket = item.allocation_bucket === 'UNKNOWN' ? '' : item.allocation_bucket;
    this.classificationError = '';
  }

  closeClassificationEditor(): void {
    if (this.classificationSaving) return;
    this.classificationEditor = null;
    this.classificationError = '';
  }

  saveClassification(): void {
    if (!this.classificationEditor) return;
    this.classificationSaving = true;
    this.classificationError = '';
    this.api.updatePortfolioClassification(
      this.brokerageId,
      this.classificationEditor.symbol,
      {
        account_id: this.classificationEditor.account_id,
        allocation_bucket: this.classificationBucket || null,
      },
    ).subscribe({
      next: () => {
        this.classificationSaving = false;
        this.classificationEditor = null;
        this.message = 'Allocation classification saved. Analysis has been recalculated.';
        this.load();
      },
      error: err => {
        this.classificationSaving = false;
        this.classificationError = this.errorMessage(err, 'The classification could not be saved.');
      },
    });
  }

  submitPreview(): void {
    const symbol = this.previewForm.symbol.trim().toUpperCase();
    const amount = this.numberOrNull(this.previewForm.amount);
    const price = this.numberOrNull(this.previewForm.assumedPrice);
    if (!this.previewForm.accountId || !symbol || amount == null || amount <= 0) {
      this.previewError = 'Choose an account and enter a symbol and positive quantity or dollar amount.';
      return;
    }
    if (price != null && price <= 0) {
      this.previewError = 'Assumed price must be positive when supplied.';
      return;
    }
    const body: PortfolioPreviewRequest = {
      account_id: this.previewForm.accountId,
      side: this.previewForm.side,
      symbol,
      quantity: this.previewForm.amountMode === 'QUANTITY' ? amount : null,
      notional: this.previewForm.amountMode === 'NOTIONAL' ? amount : null,
      assumed_price: price,
      funding_source: this.previewForm.fundingSource,
      allocation_bucket: this.previewForm.allocationBucket || null,
    };
    this.previewing = true;
    this.previewError = '';
    this.previewStatus = '';
    this.api.previewPortfolioChange(this.brokerageId, body).subscribe({
      next: result => {
        this.previewing = false;
        this.preview = result;
        this.previewStatus = 'Preview calculated. Saved holdings and brokerage data were not changed.';
      },
      error: err => {
        this.previewing = false;
        this.preview = null;
        this.previewError = this.errorMessage(err, 'The proposed change could not be previewed.');
      },
    });
  }

  previewFinding(finding: PortfolioFinding): void {
    const remediation = finding.remediation;
    this.previewForm.side = 'SELL';
    this.previewForm.symbol = finding.symbol ?? '';
    this.previewForm.assumedPrice = remediation.price ?? null;
    this.previewForm.amountMode = 'NOTIONAL';
    this.previewForm.amount = remediation.immediate_trim_amount ?? null;
    document.getElementById('portfolio-what-if')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  clearPreview(): void {
    this.preview = null;
    this.previewError = '';
    this.previewStatus = '';
  }

  previewWarningText(result: PortfolioPreviewResponse): string {
    return [...result.new_findings, ...result.worsened_findings]
      .map(finding => finding.title).join(' ');
  }

  hasPreviewRemediation(finding: PortfolioFinding): boolean {
    return Boolean(finding.symbol && finding.remediation.immediate_trim_amount != null);
  }

  severityClass(severity: PortfolioFindingSeverity): string {
    if (severity === 'CRITICAL' || severity === 'HIGH') return 'badge-neg';
    if (severity === 'CAUTION') return 'badge-warn';
    return 'badge-info';
  }

  verdictClass(value: string): string {
    if (['ALIGNED', 'WELL_CONSTRUCTED', 'IN_RANGE', 'COMPLETE'].includes(value)) return 'verdict-good';
    if (['CRITICAL_RISK', 'FRAGILE', 'UNAVAILABLE'].includes(value)) return 'verdict-bad';
    if (['ABOVE_PROFILE', 'CONCENTRATED', 'ABOVE_RANGE', 'MIXED', 'INDICATIVE'].includes(value)) {
      return 'verdict-warn';
    }
    return 'verdict-neutral';
  }

  words(value: string | null | undefined): string {
    if (!value) return '—';
    return value.toLowerCase().replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
  }

  metric(value: number | null | undefined, unit: string): string {
    if (value == null) return '—';
    if (unit.includes('PERCENT')) return this.percent(value);
    if (unit.includes('DOLLAR') || unit.includes('MONEY')) return this.money(value);
    if (unit.includes('SHARE')) return formatQuantity(value);
    return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }

  money(value: number | null | undefined, signed = false): string {
    return formatUsdMoney(value, signed);
  }

  percent(value: number | null | undefined, signed = false): string {
    return formatFixedPercent(value, signed);
  }

  number(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }

  timestamp(value: string | null | undefined): string {
    return formatIsoTimestamp(value);
  }

  dateRange(start: string | null, end: string | null): string {
    return start && end ? `${start} – ${end}` : '—';
  }

  barWidth(value: number | null): number {
    if (value == null || !Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, value));
  }

  allocationRows(): Array<{ bucket: PortfolioAllocationBucket; value: PortfolioAllocationValue }> {
    if (!this.data) return [];
    return this.allocationBuckets.map(bucket => ({
      bucket, value: this.data!.summary.allocation.buckets[bucket],
    }));
  }

  profileMissingFields(): string[] {
    const profile = this.data?.summary.profile;
    if (!profile) return [];
    const common = [
      'max_single_issuer_pct', 'max_speculative_pct',
      'max_put_assignment_commitment_pct', 'max_stress_loss_pct', 'minimum_liquid_pct',
    ];
    const roleFields = this.role === 'TRADING' ? ['max_gross_exposure_pct'] : [
      'growth_min_pct', 'growth_max_pct', 'cash_min_pct', 'cash_max_pct',
      'max_sector_pct', 'max_top_five_pct', 'first_expected_withdrawal_date',
    ];
    return [...common, ...roleFields].filter(field =>
      profile[field as keyof PortfolioAnalysisProfile] == null
    );
  }

  profileMissingText(): string {
    return this.profileMissingFields().map(field => this.words(field)).join(', ');
  }

  capitalAsOf(): string | null {
    const dates = (this.data?.summary.capital.accounts ?? [])
      .map(account => account.retrieved_at).filter((value): value is string => Boolean(value));
    return dates.sort().at(-1) ?? null;
  }

  cachedPricesAsOf(): string | null {
    return this.data?.summary.historical_risk.date_end ?? null;
  }

  evidenceAt(): string | null {
    return this.data?.as_of.positions ?? null;
  }

  classifiedAllocationPct(): number | null {
    const unknown = this.data?.summary.allocation.buckets.UNKNOWN.pct_of_capital;
    return unknown == null ? null : 100 - unknown;
  }

  stressLabel(shock: number): string {
    return `${Math.abs(shock)}% uniform equity shock`;
  }

  private ensurePreviewAccount(): void {
    const accounts = this.accounts();
    if (!accounts.some(account => account.id === this.previewForm.accountId)) {
      this.previewForm.accountId = accounts[0]?.id ?? '';
    }
  }

  private profileFrom(profile: PortfolioAnalysisProfile | null): ProfileForm {
    if (!profile) return this.emptyProfile();
    const {
      objective: _objective, reviewed_at: _reviewedAt, status: _status, ...form
    } = profile;
    return { ...this.emptyProfile(), ...form };
  }

  private emptyProfile(): ProfileForm {
    return {
      max_single_issuer_pct: null,
      max_speculative_pct: null,
      max_put_assignment_commitment_pct: null,
      max_stress_loss_pct: null,
      minimum_liquid_pct: null,
      notes: '',
      max_gross_exposure_pct: null,
      deployment_min_pct: null,
      deployment_max_pct: null,
      max_sector_pct: null,
      growth_min_pct: null,
      growth_max_pct: null,
      cash_min_pct: null,
      cash_max_pct: null,
      max_top_five_pct: null,
      first_expected_withdrawal_date: null,
    };
  }

  private emptyPreview(): PreviewForm {
    return {
      accountId: '', side: 'BUY', symbol: '', amountMode: 'NOTIONAL', amount: null,
      assumedPrice: null, fundingSource: 'ACCOUNT_CASH', allocationBucket: '',
    };
  }

  private numberOrNull(value: number | string | null | undefined): number | null {
    if (value === '' || value == null) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  private errorMessage(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } })?.error?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message);
    }
    return typeof detail === 'string' ? detail : fallback;
  }
}
