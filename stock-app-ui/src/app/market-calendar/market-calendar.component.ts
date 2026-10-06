import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { DatePipe } from '@angular/common';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MarketCalendarService } from '../api/market-calendar.service';
import { DrawerComponent } from '../shared/ui/drawer.component';
import {
  CalendarSource,
  EventEtfExposure,
  EventMeasurement,
  MarketEvent,
  MarketEventCollection,
  RiskDay,
  StrategyEventAssessment
} from '../model/market-event';

type ViewId = 'upcoming' | 'all';
type RangeId = 'today' | 'tomorrow' | 'week' | 'month';
type PriorityId = 'all' | 'primary' | 'secondary';

@Component({
  selector: 'app-market-calendar',
  standalone: true,
  imports: [DatePipe, MatTooltipModule, DrawerComponent],
  templateUrl: './market-calendar.component.html',
  styleUrl: './market-calendar.component.css',
  changeDetection: ChangeDetectionStrategy.Eager
})
export class MarketCalendarComponent implements OnInit {
  private readonly calendar = inject(MarketCalendarService);

  snapshot: MarketEventCollection | null = null;
  loading = false;
  reloading = false;
  error = '';
  reloadError = '';
  view: ViewId = 'upcoming';
  range: RangeId = 'month';
  priority: PriorityId = 'all';
  selected: MarketEvent | null = null;

  readonly ranges: Array<{ id: RangeId; label: string }> = [
    { id: 'today', label: 'Today' },
    { id: 'tomorrow', label: 'Tomorrow' },
    { id: 'week', label: 'This Week' },
    { id: 'month', label: 'Month' }
  ];
  readonly priorities: Array<{ id: PriorityId; label: string }> = [
    { id: 'all', label: 'All priorities' },
    { id: 'primary', label: 'Primary' },
    { id: 'secondary', label: 'Secondary' }
  ];

  ngOnInit(): void {
    this.load(true);
  }

  load(initial: boolean): void {
    if (initial) {
      this.loading = true;
      this.error = '';
    } else {
      this.reloading = true;
      this.reloadError = '';
    }
    this.calendar.getCollection().subscribe({
      next: snapshot => {
        this.snapshot = snapshot;
        this.loading = false;
        this.reloading = false;
      },
      error: err => {
        this.loading = false;
        this.reloading = false;
        const message = err?.error?.detail ?? 'The published event-risk scan could not be read.';
        if (this.snapshot) {
          this.reloadError = message;
        } else {
          this.error = message;
        }
      }
    });
  }

  requiredProblems() {
    return (this.snapshot?.sources ?? []).filter(source => source.required && source.status !== 'fresh');
  }

  optionalGaps() {
    return (this.snapshot?.sources ?? []).filter(source => !source.required && source.status !== 'fresh');
  }

  visibleDays(): RiskDay[] {
    const days = (this.snapshot?.days ?? []).filter(day => this.inRange(day.date));
    if (this.view === 'upcoming') {
      return days.filter(day => this.visibleEventCount(day) > 0 && (
        this.priority === 'secondary' || day.riskLabel === 'EXTREME' ||
        day.riskLabel === 'HIGH' || day.riskLabel === 'MODERATE'
      ));
    }
    return days.filter(day => this.visibleEventCount(day) > 0);
  }

  visiblePrimary(day: RiskDay): MarketEvent[] {
    return this.priority === 'secondary' ? [] : day.primaryEvents;
  }

  visibleSecondary(day: RiskDay): MarketEvent[] {
    return this.priority === 'primary' ? [] : day.secondaryEvents;
  }

  visibleEventCount(day: RiskDay): number {
    return this.visiblePrimary(day).length + this.visibleSecondary(day).length;
  }

  coveredEmptyCount(): number {
    return (this.snapshot?.days ?? []).filter(day => this.inRange(day.date) && day.coverage === 'covered_empty').length;
  }

  unknownCount(): number {
    return (this.snapshot?.days ?? []).filter(day => this.inRange(day.date) && (day.coverage === 'unknown' || day.coverage === 'stale')).length;
  }

  stat(value: number | null | undefined): string {
    return value == null ? '—' : String(value);
  }

  dash(value: string | null | undefined): string {
    return value == null || value === '' ? '—' : value;
  }

  formatSpan(iso: string): string {
    if (!iso.includes('-')) return '—';
    const [year, month, day] = iso.split('-').map(Number);
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric'
    }).format(new Date(year, month - 1, day));
  }

  formatDay(iso: string): string {
    if (!iso.includes('-')) return '—';
    const [year, month, day] = iso.split('-').map(Number);
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'long', month: 'long', day: 'numeric'
    }).format(new Date(year, month - 1, day));
  }

  sessionLabel(state: string): string {
    if (state === 'regular') return 'Regular session';
    if (state === 'early_close') return 'Early close';
    return 'Market closed';
  }

  relevanceLabel(value: string): string {
    const labels: Record<string, string> = {
      DIRECT_BROAD_INDEX: 'Direct SPY/QQQ',
      INDIRECT_BROAD_INDEX: 'Indirect SPY/QQQ',
      SECTOR_OR_COMMODITY: 'Sector or commodity',
      SINGLE_STOCK: 'Single stock',
      LOW_RELEVANCE: 'Low direct relevance'
    };
    return labels[value] ?? value;
  }

  assessmentLabel(value: string): string {
    return value.replaceAll('_', ' ');
  }

  timingLabel(value: string): string {
    return value.replaceAll('_', ' ');
  }

  riskClass(label: string): string {
    if (label === 'EXTREME' || label === 'HIGH') return 'badge badge-neg';
    if (label === 'MODERATE') return 'badge badge-warn';
    return 'badge badge-neutral';
  }

  assessmentClass(value: string): string {
    if (value === 'avoid' || value === 'high_risk') return 'badge badge-neg';
    if (value === 'mixed_opportunity') return 'badge badge-info';
    if (value === 'conditional') return 'badge badge-warn';
    return 'badge badge-neutral';
  }

  priceClass(state: string): string {
    return state === 'current' ? 'badge badge-neutral' : 'badge badge-warn';
  }

  strongest(event: MarketEvent): StrategyEventAssessment | null {
    const rank: Record<string, number> = {
      avoid: 5, high_risk: 4, mixed_opportunity: 3, conditional: 2, low_direct_relevance: 1
    };
    return [...event.strategyAssessments].sort(
      (left, right) => (rank[right.assessment] ?? 0) - (rank[left.assessment] ?? 0)
    )[0] ?? null;
  }

  measurementValue(event: MarketEvent, metric: string, kind: string): string | null {
    const found = event.measurements.find(item => item.metric === metric && item.valueKind === kind);
    return found?.value ?? null;
  }

  metrics(event: MarketEvent): EventMeasurement[] {
    const seen = new Set<string>();
    return event.measurements.filter(item => {
      if (seen.has(item.metric)) return false;
      seen.add(item.metric);
      return true;
    });
  }

  exposureGroups(event: MarketEvent): Array<{ relationship: string; items: EventEtfExposure[] }> {
    const order = [
      'direct_underlying', 'sector_equities', 'industry_equities',
      'rate_sensitive', 'input_cost_exposure', 'defensive_or_hedge_proxy'
    ];
    const groups = new Map<string, EventEtfExposure[]>();
    for (const exposure of event.etfExposures) {
      const list = groups.get(exposure.relationship) ?? [];
      list.push(exposure);
      groups.set(exposure.relationship, list);
    }
    return [...groups.entries()]
      .sort((left, right) => order.indexOf(left[0]) - order.indexOf(right[0]))
      .map(([relationship, items]) => ({ relationship, items }));
  }

  sourceState(provider: string): CalendarSource | undefined {
    return this.snapshot?.sources.find(source => source.provider === provider);
  }

  relationshipLabel(value: string): string {
    return value.replaceAll('_', ' ');
  }

  strategyName(id: string): string {
    const names: Record<string, string> = {
      'short-premium-20d-0dte': 'Short premium, 20 delta, 0 DTE',
      'long-premium-20d-0-or-1dte': 'Long premium, 20 delta, 0 or 1 DTE',
      'long-wings-repeated-0dte-premium': 'Long wings with repeated 0 DTE premium'
    };
    return names[id] ?? id;
  }

  private inRange(iso: string): boolean {
    const asOf = this.snapshot?.asOfDate;
    if (!asOf || this.range === 'month') return true;
    if (this.range === 'today') return iso === asOf;
    if (this.range === 'tomorrow') return iso === this.addDays(asOf, 1);
    const start = this.weekStart(asOf);
    const end = this.addDays(start, 6);
    return iso >= start && iso <= end;
  }

  private addDays(iso: string, days: number): string {
    const [year, month, day] = iso.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    date.setDate(date.getDate() + days);
    const monthText = String(date.getMonth() + 1).padStart(2, '0');
    const dayText = String(date.getDate()).padStart(2, '0');
    return `${date.getFullYear()}-${monthText}-${dayText}`;
  }

  private weekStart(iso: string): string {
    const [year, month, day] = iso.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    return this.addDays(iso, -date.getDay());
  }
}
