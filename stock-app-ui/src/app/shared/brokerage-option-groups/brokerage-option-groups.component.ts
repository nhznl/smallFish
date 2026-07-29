import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatTooltipModule } from '@angular/material/tooltip';

import { Capability } from '../../api/capability.service';
import { BrokerageOptionGroup } from '../../model/brokerage-option-groups';
import { CapabilityStateComponent } from '../ui/capability-state.component';

@Component({
  selector: 'app-brokerage-option-groups',
  standalone: true,
  imports: [FormsModule, MatTooltipModule, CapabilityStateComponent],
  templateUrl: './brokerage-option-groups.component.html',
  styleUrl: './brokerage-option-groups.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerageOptionGroupsComponent {
  @Input() groups: readonly BrokerageOptionGroup[] = [];
  @Input() loading = false;
  @Input() error = '';
  @Input() message = '';
  @Input() timestamp: string | null = null;
  @Input() timestampLabel = 'Synced';
  @Input() eventCount = 0;
  @Input() ungroupedCount = 0;
  @Input() eventColumnLabel = 'Broker events';
  @Input() reconciliationCount = 0;
  @Input() manualReconciliationCount = 0;
  @Input() capability: Capability | null = null;
  @Input() emptyText = 'No option trade groups are available.';
  @Input() syncLabel = 'Sync brokerage';
  @Input() caveat = '';

  @Output() editGroup = new EventEmitter<BrokerageOptionGroup>();
  @Output() reviewReconciliation = new EventEmitter<void>();

  showActive = true;
  showArchived = true;

  filteredGroups(): readonly BrokerageOptionGroup[] {
    return this.groups.filter(group =>
      group.status === 'ARCHIVED' ? this.showArchived : this.showActive
    );
  }

  filteredNetCredit(): number {
    return this.filteredGroups().reduce((total, group) => total + group.net_cash_flow, 0);
  }

  filteredOpenMarketValue(): number | null {
    const groups = this.filteredGroups();
    if (!groups.length || groups.some(group => group.open_market_value == null)) return null;
    return groups.reduce((total, group) => total + (group.open_market_value ?? 0), 0);
  }

  filteredTotalPnl(): number | null {
    const groups = this.filteredGroups();
    if (!groups.length || groups.some(group => group.total_pnl == null)) return null;
    return groups.reduce((total, group) => total + (group.total_pnl ?? 0), 0);
  }

  hasReconciliationDetail(): boolean {
    return this.reconciliationCount > 0 || this.manualReconciliationCount > 0;
  }

  money(value: number | null | undefined): string {
    if (value == null) return '—';
    return value.toLocaleString('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  pnlClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'positive' : 'negative';
  }
}
