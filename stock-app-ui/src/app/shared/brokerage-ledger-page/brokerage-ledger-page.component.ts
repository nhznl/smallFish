import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

import { BrokerageService } from '../../api/brokerage.service';
import { BrokerageId, BrokerageSyncResponse } from '../../model/brokerage';
import { BrokerageLedgerCombinedComponent } from '../brokerage-ledger-combined/brokerage-ledger-combined.component';
import { BrokerageHoldingsComponent } from '../brokerage-holdings/brokerage-holdings.component';
import { SymbolLedgerComponent } from '../symbol-ledger/symbol-ledger.component';
import { PortfolioAnalysisComponent } from '../portfolio-analysis/portfolio-analysis.component';

type LedgerTab = 'holdings' | 'options' | 'basis' | 'analysis';

/**
 * Common brokerage shell. Every tab is now driven by the public brokerage id
 * alone; the shell never branches on a brokerage identity to interpret data.
 */
@Component({
  selector: 'app-brokerage-ledger-page',
  standalone: true,
  imports: [
    CommonModule, BrokerageHoldingsComponent, BrokerageLedgerCombinedComponent,
    SymbolLedgerComponent, PortfolioAnalysisComponent,
  ],
  templateUrl: './brokerage-ledger-page.component.html',
  styleUrl: './brokerage-ledger-page.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageLedgerPageComponent {
  @Input({ required: true }) brokerageId!: BrokerageId;
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
        const sold = this.soldSymbols(report);
        this.syncMessage = failed
          ? `${completed} brokerage resource${completed === 1 ? '' : 's'} refreshed; ${failed} need attention.`
          : `${completed} brokerage resource${completed === 1 ? '' : 's'} refreshed.`;
        if (sold.length) {
          this.syncMessage += ` ${sold.join(', ')} moved to Tracking as Sold Stock.`;
        }
        this.refresh();
      },
      error: err => {
        this.syncing = false;
        this.syncError = this.message(err);
      },
    });
  }

  private soldSymbols(report: BrokerageSyncResponse): string[] {
    const seen = new Set<string>();
    const symbols: string[] = [];
    for (const result of report.results) {
      const raw = result.detail?.['sold_symbols'];
      if (!Array.isArray(raw)) continue;
      for (const item of raw) {
        if (typeof item !== 'string' || !item || seen.has(item)) continue;
        seen.add(item);
        symbols.push(item);
      }
    }
    return symbols;
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
