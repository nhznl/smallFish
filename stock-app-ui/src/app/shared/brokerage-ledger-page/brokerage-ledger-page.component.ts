import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

import { BrokerageService } from '../../api/brokerage.service';
import { BrokerageId } from '../../model/brokerage';
import { BrokerageLedgerPortfolioSlug } from '../../model/brokerage-ledger';
import { BrokerageLedgerCombinedComponent } from '../brokerage-ledger-combined/brokerage-ledger-combined.component';
import { BrokerageHoldingsComponent } from '../brokerage-holdings/brokerage-holdings.component';
import { SymbolLedgerComponent } from '../symbol-ledger/symbol-ledger.component';

type LedgerTab = 'holdings' | 'options' | 'basis';

/**
 * Common brokerage shell. The only temporary compatibility input is the
 * legacy portfolio slug consumed by the retained holdings/basis projections;
 * the shell itself never branches on a brokerage identity.
 */
@Component({
  selector: 'app-brokerage-ledger-page',
  standalone: true,
  imports: [
    CommonModule, BrokerageHoldingsComponent, BrokerageLedgerCombinedComponent,
    SymbolLedgerComponent,
  ],
  templateUrl: './brokerage-ledger-page.component.html',
  styleUrl: './brokerage-ledger-page.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageLedgerPageComponent {
  @Input({ required: true }) brokerageId!: BrokerageId;
  @Input({ required: true }) portfolio!: BrokerageLedgerPortfolioSlug;
  @Input({ required: true }) title = '';

  tab: LedgerTab = 'holdings';
  holdingsCount = 0;
  refreshToken = 0;
  syncing = false;
  syncMessage = '';
  syncError = '';

  constructor(private readonly api: BrokerageService) {}

  refresh(): void {
    this.refreshToken++;
    this.syncError = '';
  }

  sync(): void {
    if (this.syncing) return;
    this.syncing = true;
    this.syncError = '';
    this.syncMessage = '';
    this.api.runSync(this.brokerageId).subscribe({
      next: report => {
        this.syncing = false;
        const completed = report.results.filter(result => result.status === 'OK').length;
        const failed = report.results.filter(result => result.status === 'FAILED').length;
        this.syncMessage = failed
          ? `${completed} brokerage resource${completed === 1 ? '' : 's'} refreshed; ${failed} need attention.`
          : `${completed} brokerage resource${completed === 1 ? '' : 's'} refreshed.`;
        this.refresh();
      },
      error: err => {
        this.syncing = false;
        this.syncError = this.message(err);
      },
    });
  }

  private message(err: unknown): string {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message);
    }
    return typeof detail === 'string'
      ? detail
      : 'The brokerage sync could not be completed.';
  }
}
