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
  running = false;
  runStatus: 'idle' | 'ok' | 'error' = 'idle';
  runMessage = '';
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

  run(): void {
    if (this.loading || this.reloading || this.running) return;
    this.running = true;
    this.runStatus = 'idle';
    this.runMessage = 'Checking today’s market calendar. The last successful snapshot remains visible.';
    this.reloadError = '';
    this.calendar.runMarketCalendar().subscribe({
      next: result => {
        this.running = false;
        if (result.status === 'ok') {
          this.runStatus = 'ok';
          this.runMessage = result.message ?? (
            result.reused
              ? 'Today’s market calendar was already generated; the published data was reused.'
              : 'Today’s market calendar was generated successfully.'
          );
          this.load(false);
        } else {
          this.runStatus = 'error';
          this.runMessage = result.message || result.output || 'The market-calendar job failed.';
        }
      },
      error: err => {
        this.running = false;
        this.runStatus = 'error';
        this.runMessage = err?.error?.detail ?? 'The market-calendar job could not be started.';
      }
    });
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

  secondaryEarnings(day: RiskDay): MarketEvent[] {
    return this.visibleSecondary(day).filter(event =>
      event.eventType === 'EARNINGS' && event.portfolioRelevance === 'SINGLE_STOCK'
    );
  }

  secondaryDetails(day: RiskDay): MarketEvent[] {
    return this.visibleSecondary(day).filter(event =>
      event.eventType !== 'EARNINGS' || event.portfolioRelevance !== 'SINGLE_STOCK'
    );
  }

  earningsSymbol(event: MarketEvent): string {
    return event.title.replace(/\s+earnings$/i, '').trim();
  }

  earningsImportance(day: RiskDay): string {
    return [...new Set(this.secondaryEarnings(day).map(event => event.importance.label))].join(', ');
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

  coverageLabel(status: string): string {
    const labels: Record<string, string> = {
      fresh: 'Complete',
      covered: 'Complete',
      covered_empty: 'Complete',
      insufficient: 'Partial',
      stale: 'Stale',
      failed: 'Failed',
      unavailable: 'Unavailable',
      not_configured: 'Not configured',
      unknown: 'Incomplete'
    };
    return labels[status] ?? status.replaceAll('_', ' ');
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
    const labels: Record<string, string> = {
      before_entry: 'Before entry',
      during_typical_hold: 'During the typical hold',
      after_typical_exit_before_hard_exit: 'Between the usual 11:30 AM exit and noon ET',
      after_planned_exit: 'After the planned exit',
      all_day_exposure: 'All-day exposure'
    };
    return labels[value] ?? value.replaceAll('_', ' ');
  }

  displayBenefits(assessment: StrategyEventAssessment): string[] {
    return this.displayStrategyMessages(assessment.potentialBenefits);
  }

  displayRisks(assessment: StrategyEventAssessment): string[] {
    return this.displayStrategyMessages(assessment.potentialRisks);
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
    const labels: Record<string, string> = {
      direct_underlying: 'Direct',
      sector_equities: 'Sector',
      industry_equities: 'Industry',
      rate_sensitive: 'Rate sensitive',
      input_cost_exposure: 'Input cost exposure',
      defensive_or_hedge_proxy: 'Defensive or hedge proxy'
    };
    return labels[value] ?? value.replaceAll('_', ' ');
  }

  exposureRelevanceLabel(value: string): string {
    const labels: Record<string, string> = {
      primary: 'Primary',
      secondary: 'Secondary',
      indirect: 'Indirect'
    };
    return labels[value] ?? value;
  }

  strategyName(id: string): string {
    const names: Record<string, string> = {
      'short-premium-20d-0dte': 'Short premium, 20 delta, 0 DTE',
      'long-premium-20d-0-or-1dte': 'Long premium, 20 delta, 0 or 1 DTE',
      'long-wings-repeated-0dte-premium': 'Long wings with repeated 0 DTE premium'
    };
    return names[id] ?? id;
  }

  private displayStrategyMessages(messages: string[]): string[] {
    const hidden = new Set([
      'Movement is not profit.',
      "Multiple scheduled releases overlap this strategy's configured exposure window, which can concentrate movement opportunity.",
      "Multiple scheduled releases overlap this strategy's configured exposure window; their combined effect is not a directional forecast."
    ]);
    const replacements: Record<string, string> = {
      'The configured hard exit is before this release, so a position closed on that rule is no longer in the typical same-day hold.':
        'This release is scheduled after the noon ET exit. If you follow the strategy’s exit rule, the trade should already be closed.',
      'A position still open after the hard exit remains exposed to the release.':
        'If you keep the trade open past noon ET, it can still be affected by this release.',
      'Option premium may be elevated around the release. Elevated premium is not an edge.':
        'Option premiums may be higher around the release.',
      'Option premium may be elevated. Elevated premium is not an edge.':
        'Option premiums may be higher around the release.'
    };
    return messages
      .filter(message => !hidden.has(message))
      .map(message => replacements[message] ?? message);
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
