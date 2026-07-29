import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';

import { Capability } from '../../api/capability.service';
import { OptionsCoverage } from '../../model/options-ledger';
import { CapabilityStateComponent } from '../ui/capability-state.component';

export interface BrokerRiskRow {
  id: string;
  account: string;
  wheel_id: string;
  symbol: string;
  trade_type: string;
  qty: number;
  strike: number | null;
  expiry: string;
  mark_price: number | null;
  mark_retrieved_at: string | null;
  status: string;
  non_standard: boolean;
  dte_remaining?: number | null;
  current_underlying_price?: number | null;
  percent_to_strike?: number | null;
  needs_settlement?: boolean;
  coverage?: OptionsCoverage;
  covered_contracts?: number;
}

export interface BrokerRiskMetric {
  row_id: string;
  vol_annual: number | null;
  vol_as_of: string | null;
  delta_source: string | null;
  delta_shares: number | null;
}

@Component({
  selector: 'app-broker-risk-positions',
  standalone: true,
  imports: [MatTooltipModule, CapabilityStateComponent],
  templateUrl: './broker-risk-positions.component.html',
  styleUrl: './broker-risk-positions.component.css',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class BrokerRiskPositionsComponent {
  @Input() rows: readonly BrokerRiskRow[] = [];
  @Input() metrics: readonly BrokerRiskMetric[] = [];
  @Input() asOf: string | null = null;
  @Input() brokerName = 'broker';
  @Input() capability: Capability | null = null;
  @Input() emptyText = 'No open option positions.';
  @Input() syncLabel = 'Sync brokerage';
  @Input() focusedSymbol: string | null = null;

  strikeSortDirection: 'asc' | 'desc' = 'asc';
  readonly nearStrikeThresholdPercent = 5;
  private readonly optionTradeTypes = new Set([
    'SHORT_PUT', 'COVERED_CALL', 'SHORT_CALL', 'LONG_PUT', 'LONG_CALL',
  ]);

  optionRows(): readonly BrokerRiskRow[] {
    const rows = this.rows.filter(row => this.optionTradeTypes.has(row.trade_type));
    const direction = this.strikeSortDirection === 'asc' ? 1 : -1;
    return [...rows].sort((left, right) => {
      const leftDistance = this.strikeRiskDistance(left);
      const rightDistance = this.strikeRiskDistance(right);
      if (leftDistance == null && rightDistance == null) return 0;
      if (leftDistance == null) return 1;
      if (rightDistance == null) return -1;
      return direction * (leftDistance - rightDistance);
    });
  }

  toggleStrikeSort(): void {
    this.strikeSortDirection = this.strikeSortDirection === 'asc' ? 'desc' : 'asc';
  }

  metric(row: BrokerRiskRow): BrokerRiskMetric | undefined {
    return this.metrics.find(metric => metric.row_id === row.id);
  }

  coverageLabel(row: BrokerRiskRow): string {
    if (row.coverage === 'PARTIAL') {
      return `⚠ ${row.covered_contracts ?? 0} of ${row.qty} share-covered`;
    }
    return row.coverage === 'UNCOVERED' ? 'No share cover' : '';
  }

  strikeRiskDistance(row: BrokerRiskRow): number | null {
    const distance = row.percent_to_strike;
    if (distance == null) return null;
    if (row.trade_type === 'SHORT_PUT') return distance;
    if (row.trade_type === 'SHORT_CALL' || row.trade_type === 'COVERED_CALL') return -distance;
    return Math.abs(distance);
  }

  strikeDistanceLabel(row: BrokerRiskRow): string {
    const distance = this.strikeRiskDistance(row);
    if (distance == null) return '—';
    const isShort = ['SHORT_PUT', 'SHORT_CALL', 'COVERED_CALL'].includes(row.trade_type);
    if (!isShort) return `${Math.abs(distance).toFixed(2)}% from strike`;
    return distance <= 0
      ? `${Math.abs(distance).toFixed(2)}% past strike`
      : `${distance.toFixed(2)}% from breach`;
  }

  rowClass(row: BrokerRiskRow): string {
    const classes = [row.status === 'OPEN' ? 'open-row' : 'closed-row'];
    if (row.needs_settlement) classes.push('settlement-row');
    const distance = this.strikeRiskDistance(row);
    const short = ['SHORT_PUT', 'SHORT_CALL', 'COVERED_CALL'].includes(row.trade_type);
    if (short && distance != null && distance <= 0) classes.push('strike-breached-row');
    else if (short && distance != null && distance <= this.nearStrikeThresholdPercent) {
      classes.push('strike-near-row');
    }
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

  number(value: number | null | undefined, digits = 1): string {
    return value == null ? '—' : value.toFixed(digits);
  }

  percent(value: number | null | undefined): string {
    return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
  }

  shortTimestamp(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  ivSourceLabel(source: string | null | undefined): string {
    const labels: Record<string, string> = {
      TASTYTRADE_IV: 'Tastytrade live', CHAIN_IV: 'Chain IV', RV_FALLBACK: 'RV fallback',
    };
    return source ? (labels[source] ?? source) : 'Unavailable';
  }
}
