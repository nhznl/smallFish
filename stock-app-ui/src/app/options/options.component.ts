
import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Observable } from 'rxjs';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ModalComponent } from '../shared/ui/modal.component';
import { Capability, CapabilityService } from '../api/capability.service';
import { BrokerageLedgerService } from '../api/brokerage-ledger.service';
import { StockService } from '../api/stock.service';
import { BrokerageLedgerCombinedComponent } from '../shared/brokerage-ledger-combined/brokerage-ledger-combined.component';
import { BrokerageHoldingsComponent } from '../shared/brokerage-holdings/brokerage-holdings.component';
import { BrokerageOptionGroupsComponent } from '../shared/brokerage-option-groups/brokerage-option-groups.component';
import { BrokerRiskPositionsComponent } from '../shared/broker-risk-positions/broker-risk-positions.component';
import { BrokerageOptionGroup } from '../model/brokerage-option-groups';
import { BrokerageHolding, BrokerageHoldingsSnapshot } from '../model/brokerage-holdings';
import {
  OptionsActivityEvent, OptionsActivitySnapshot, OptionsActivitySyncReport,
  OptionsGroupPosition, OptionsReconciliationIssue,
  OptionsSnapshot, OptionsTradeGroup
} from '../model/options-ledger';

/**
 * Context for the manual reconciliation dialog. Both entry points fill this in
 * — a mismatch row being corrected, or an existing manual row being edited —
 * so the form itself does not care which one opened it.
 */
interface ManualReconcileForm {
  mode: 'create' | 'edit';
  /** Set only when editing an existing manual row. */
  eventId: string | null;
  account: string;
  contractKey: string;
  underlyingSymbol: string;
  instrumentType: string | null;
  groupId: string | null;
  groupName: string | null;
  /** Mismatch context behind the seeded quantity; absent when editing. */
  ledgerQuantity: number | null;
  brokerQuantity: number | null;
}

@Component({
  selector: 'app-options',
  standalone: true,
  imports: [
    DatePipe, FormsModule, MatTooltipModule, ModalComponent,
    BrokerageLedgerCombinedComponent, BrokerageHoldingsComponent, BrokerageOptionGroupsComponent,
    BrokerRiskPositionsComponent,
  ],
  templateUrl: './options.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./options.component.css']
})
export class OptionsComponent implements OnInit {
  private stockService = inject(StockService);
  private capabilityService = inject(CapabilityService);
  private brokerageLedgerService = inject(BrokerageLedgerService);

  /** Drives the optional-integration state instead of inferring it from an
   *  empty ledger: an unconfigured broker and a flat account differ. */
  tastytrade: Capability | null = null;

  data: OptionsSnapshot | null = null;
  loading = false;
  error: string | null = null;
  readonly account = 'TRADING';
  tab: 'holdings' | 'options' | 'basis' = 'holdings';
  combinedRefreshToken = 0;
  holdingsCount = 0;
  holdingsContext: BrokerageHoldingsSnapshot | null = null;
  activity: OptionsActivitySnapshot | null = null;
  activityLoading = false;
  activityError: string | null = null;
  activitySyncing = false;
  activityMessage = '';
  syncedAt: string | null = null;

  detailGroup: OptionsTradeGroup | null = null;
  detailSaving = false;
  detailSavedFlash = false;
  detailError: string | null = null;
  /** Serialized editable fields as last saved; drives the dirty check. */
  private detailBaseline = '';
  private detailFlashTimer?: ReturnType<typeof setTimeout>;
  reconciliationOpen = false;

  manualForm: ManualReconcileForm | null = null;
  manualSaving = false;
  manualError: string | null = null;
  manualQuantity = 0;
  manualDate = '';
  manualPrice: number | null = null;
  manualNetCash: number | null = null;
  manualFees: number | null = null;
  manualDescription = '';

  /** Symbol highlighted after clicking a warning; cleared on a timer. */
  focusedSymbol: string | null = null;
  private focusTimer?: ReturnType<typeof setTimeout>;
  private toastTimer?: ReturnType<typeof setTimeout>;

  ngOnInit(): void {
    this.capabilityService.get('tastytrade')
      .subscribe(capability => this.tastytrade = capability);
    this.load();
  }

  load(): void {
    this.combinedRefreshToken++;
    this.loadHoldingsContext();
    this.loadActivity();
    this.loading = true;
    this.error = null;
    this.stockService.getOptions(this.account).subscribe({
      next: data => {
        if (!data) {
          this.error = 'Failed to load the options ledger.';
        } else {
          this.data = data;
        }
        this.loading = false;
      },
      error: () => {
        this.error = 'Failed to load the options ledger.';
        this.loading = false;
      }
    });
  }

  private loadHoldingsContext(): void {
    this.brokerageLedgerService.getHoldings('trading').subscribe({
      next: snapshot => {
        this.holdingsContext = snapshot;
        this.holdingsCount = snapshot.holdings.length;
      },
      error: () => this.holdingsContext = null,
    });
  }

  loadActivity(): void {
    this.activityLoading = true;
    this.activityError = null;
    this.stockService.getOptionsActivity(this.account).subscribe({
      next: activity => {
        this.activity = activity ?? null;
        if (!activity) this.activityError = 'Failed to load broker activity.';
        this.refreshDetailGroup(activity?.groups ?? []);
        this.activityLoading = false;
      },
      error: () => {
        this.activityError = 'Failed to load broker activity.';
        this.activityLoading = false;
      }
    });
  }

  syncActivity(): void {
    this.activitySyncing = true;
    this.activityMessage = '';
    this.activityError = null;
    this.stockService.syncOptionsActivity().subscribe({
      next: report => {
        this.activitySyncing = false;
        if (!report) {
          this.activityError = 'Tastytrade sync failed.';
          return;
        }
        this.syncedAt = report.retrieved_at;
        this.flashMessage(this.activitySyncMessage(report));
        this.load();
      },
      error: err => {
        this.activitySyncing = false;
        this.activityError = err?.error?.detail ?? 'Tastytrade sync failed.';
      }
    });
  }

  private activitySyncMessage(report: OptionsActivitySyncReport): string {
    const ivMessage = report.greeks_error
      ? ` Live IV unavailable; ${report.greeks_missing} open contract(s) missing.`
      : ` ${report.greeks_observed} live IV observation(s); ` +
        `${report.greeks_retained} prior observation(s) retained, ` +
        `${report.greeks_missing} missing.`;
    const reactivatedMessage = report.groups_reactivated > 0
      ? ` Updated ${report.groups_reactivated} archived ` +
        `group${report.groups_reactivated === 1 ? '' : 's'} back to Active.`
      : '';
    return `${report.option_events_selected} option events synced; ` +
      `${report.events_inserted} new, ${report.events_updated} refreshed.` +
      reactivatedMessage + ivMessage;
  }

  openSharedGroup(group: BrokerageOptionGroup): void {
    this.openGroupDetails(group as OptionsTradeGroup);
  }

  saveGroup(group: OptionsTradeGroup): void {
    if (!this.groupDirty() || this.detailSaving) return;
    this.detailSaving = true;
    this.detailError = null;
    this.detailSavedFlash = false;
    this.stockService.updateOptionsGroup(group.group_id, {
      name: group.name, notes: group.notes, status: group.status
    }).subscribe({
      next: saved => {
        this.detailSaving = false;
        if (!saved) {
          this.detailError = `Could not save ${group.name}.`;
          return;
        }
        // The bound group already contains the saved editable fields. Avoid
        // tearing down and rebuilding the entire group grid, which moves the
        // user away from the card they just saved.
        this.detailBaseline = this.groupFingerprint(group);
        // Confirmation has to live inside the modal: the activity-panel toast
        // renders behind it, so a save looked like it did nothing.
        this.detailSavedFlash = true;
        clearTimeout(this.detailFlashTimer);
        this.detailFlashTimer = setTimeout(() => (this.detailSavedFlash = false), 4000);
        this.flashMessage(`${saved.name} saved.`);
      },
      error: err => {
        this.detailSaving = false;
        this.detailError = err?.error?.detail ?? `Could not save ${group.name}.`;
      }
    });
  }

  criticalWarningCount(): number {
    return (this.data?.warnings.break_even.length ?? 0) +
      (this.data?.risk.warnings.needs_settlement.length ?? 0);
  }

  cautionWarningCount(): number {
    return (this.data?.warnings.ex_dividend.length ?? 0) +
      (this.data?.risk.warnings.short_gamma.length ?? 0);
  }

  infoWarningCount(): number {
    return this.data?.warnings.event_concentration.length ?? 0;
  }

  /** Scroll the first risk row for this symbol into view and flag it briefly. */
  focusSymbol(symbol: string): void {
    this.focusedSymbol = symbol;
    clearTimeout(this.focusTimer);
    setTimeout(() => {
      document.querySelector(`[data-symbol="${symbol}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    this.focusTimer = setTimeout(() => (this.focusedSymbol = null), 4000);
  }

  /** Sync/save confirmations read as toasts, so they clear themselves. */
  private flashMessage(message: string): void {
    this.activityMessage = message;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => (this.activityMessage = ''), 6000);
  }

  openGroupDetails(group: OptionsTradeGroup): void {
    this.detailGroup = group;
    this.detailBaseline = this.groupFingerprint(group);
    this.detailSavedFlash = false;
    this.detailError = null;
  }

  closeGroupDetails(): void {
    this.detailGroup = null;
    this.detailSavedFlash = false;
    this.detailError = null;
    clearTimeout(this.detailFlashTimer);
  }

  /** Only the three editable fields decide whether Save Group has work to do;
   *  everything else on the group is computed and cannot be edited here. */
  private groupFingerprint(group: OptionsTradeGroup): string {
    return JSON.stringify([group.name.trim(), group.notes.trim(), group.status]);
  }

  groupDirty(): boolean {
    return !!this.detailGroup
      && this.groupFingerprint(this.detailGroup) !== this.detailBaseline;
  }

  /** Jumps from a reconciliation issue straight to its trade group, if the
   *  mismatched contract is linked to one. */
  openGroupFromReconciliation(issue: OptionsReconciliationIssue): void {
    const group = (this.activity?.groups ?? []).find(g => g.group_id === issue.group_id);
    if (!group) return;
    this.reconciliationOpen = false;
    this.openGroupDetails(group);
  }

  manualEvents(): OptionsActivityEvent[] {
    return this.activity?.manual_events ?? [];
  }

  /** After a reload, repoint an open details modal at the refreshed group so a
   *  manual edit made from inside it shows the new P/L. Name, notes, and status
   *  are left alone — they are bound to the inputs and may hold unsaved edits. */
  private refreshDetailGroup(groups: OptionsTradeGroup[]): void {
    if (!this.detailGroup) return;
    const fresh = groups.find(group => group.group_id === this.detailGroup!.group_id);
    if (!fresh) return;
    const { name, notes, status } = this.detailGroup;
    Object.assign(this.detailGroup, fresh, { name, notes, status });
  }

  /** Seeds the entry form with the correction that closes the gap: applying a
   *  delta of (broker − ledger) makes the ledger agree with the broker. */
  openManualReconcile(issue: OptionsReconciliationIssue): void {
    this.manualForm = {
      mode: 'create', eventId: null,
      account: issue.account ?? this.account,
      contractKey: issue.contract_key,
      underlyingSymbol: issue.underlying_symbol,
      instrumentType: issue.instrument_type,
      groupId: issue.group_id, groupName: issue.group_name,
      ledgerQuantity: issue.activity_quantity, brokerQuantity: issue.broker_quantity,
    };
    this.manualError = null;
    this.manualQuantity = issue.broker_quantity - issue.activity_quantity;
    this.manualDate = '';
    this.manualPrice = null;
    this.manualNetCash = null;
    this.manualFees = null;
    this.manualDescription = '';
  }

  /** Reopens the same form over an existing manual row's stored values. */
  openManualEdit(event: OptionsActivityEvent): void {
    this.manualForm = {
      mode: 'edit', eventId: event.id,
      account: event.account,
      contractKey: event.contract_key,
      underlyingSymbol: event.underlying_symbol,
      instrumentType: event.instrument_type,
      groupId: event.group_id, groupName: event.group_name,
      ledgerQuantity: null, brokerQuantity: null,
    };
    this.manualError = null;
    this.manualQuantity = event.position_delta ?? 0;
    this.manualDate = event.transaction_date;
    this.manualPrice = event.price;
    this.manualNetCash = event.net_value;
    this.manualFees = event.fee_effect;
    this.manualDescription = event.description;
  }

  saveManualReconcile(): void {
    const form = this.manualForm;
    if (!form) return;
    if (!this.manualDate) {
      this.manualError = 'Date is required.';
      return;
    }
    if (!this.manualQuantity) {
      this.manualError = 'Quantity must be a non-zero position change.';
      return;
    }
    const values = {
      quantity: this.manualQuantity,
      transaction_date: this.manualDate,
      price: this.manualPrice,
      net_cash: this.manualNetCash,
      fees: this.manualFees,
      description: this.manualDescription.trim(),
    };
    // Widened because create and edit resolve to differently-shaped payloads;
    // neither response is used beyond signalling success.
    const request$: Observable<unknown> = form.mode === 'edit'
      ? this.stockService.updateManualOptionsEvent(form.eventId!, values)
      : this.stockService.createManualOptionsEvent({
          ...values,
          account: form.account,
          contract_key: form.contractKey,
          underlying_symbol: form.underlyingSymbol,
          instrument_type: form.instrumentType ?? undefined,
          group_id: form.groupId,
        });
    this.manualSaving = true;
    this.manualError = null;
    request$.subscribe({
      next: () => {
        this.manualSaving = false;
        this.manualForm = null;
        this.flashMessage(form.mode === 'edit'
          ? `Manual reconciliation updated for ${form.underlyingSymbol}.`
          : `Manual reconciliation added for ${form.underlyingSymbol}.`);
        this.loadActivity();
      },
      error: (err: any) => {
        this.manualSaving = false;
        this.manualError = err?.error?.detail
          ?? 'Reconciliation could not be saved.';
      }
    });
  }

  deleteManualEvent(event: OptionsActivityEvent): void {
    this.stockService.deleteManualOptionsEvent(event.id).subscribe({
      next: () => {
        this.flashMessage(`Manual reconciliation removed for ${event.underlying_symbol}.`);
        this.loadActivity();
      },
      error: err => {
        this.activityError = err?.error?.detail ?? 'Manual row could not be removed.';
      }
    });
  }

  detailEvents(): OptionsActivityEvent[] {
    if (!this.detailGroup) return [];
    return (this.activity?.events ?? []).filter(event =>
      event.group_id === this.detailGroup?.group_id && this.isOptionEvent(event)
    );
  }

  private isOptionEvent(event: OptionsActivityEvent): boolean {
    return event.option_type === 'PUT' || event.option_type === 'CALL'
      || event.instrument_type.includes('Option');
  }

  /** Current signed position value for an event's contract. Closed contracts
   *  have no entry in open_positions and deliberately render as missing. */
  openMarketValueForEvent(event: OptionsActivityEvent): number | null {
    return this.detailGroup?.open_positions.find(
      position => position.contract_key === event.contract_key
    )?.market_value ?? null;
  }

  equityHoldings(symbol: string): BrokerageHolding[] {
    return (this.holdingsContext?.holdings ?? []).filter(
      holding => holding.symbol === symbol
    );
  }

  equityShareTotal(symbol: string): number {
    return this.equityHoldings(symbol).reduce((total, holding) => total + holding.qty, 0);
  }

  shortCallContracts(group: OptionsTradeGroup): number {
    return group.open_positions.reduce((total, position) =>
      total + (position.option_type === 'CALL' && position.quantity < 0
        ? Math.abs(position.quantity) : 0), 0);
  }

  coveredCallContracts(group: OptionsTradeGroup): number {
    return Math.min(
      this.shortCallContracts(group),
      Math.floor(this.equityShareTotal(group.symbol) / 100),
    );
  }

  /** Share counts: whole lots stay whole, fractional lots keep their remainder. */
  shares(value: number | null | undefined): string {
    if (value == null) return '—';
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }

  /** Current underlying price for a group's symbol, from a live risk leg. */
  groupSpot(symbol: string): number | null {
    const pos = this.data?.risk?.positions?.find(p => p.symbol === symbol && p.spot != null);
    if (pos?.spot != null) return pos.spot;
    const row = this.data?.rows?.find(r => r.symbol === symbol && r.current_underlying_price != null);
    return row?.current_underlying_price ?? null;
  }

  shortTimestamp(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  money(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', { style: 'currency', currency: 'USD',
      minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  optionPrice(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 3
    });
  }

  openPositionLabel(position: OptionsGroupPosition): string {
    if (!position.option_type || !position.expiry || position.strike == null) {
      return position.contract_key;
    }
    const expiry = new Date(`${position.expiry}T00:00:00`).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric'
    });
    return `${position.option_type === 'CALL' ? 'Call' : 'Put'} ${expiry} $${position.strike}`;
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }

}
