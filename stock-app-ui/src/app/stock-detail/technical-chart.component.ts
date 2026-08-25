import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { StockDailyBar } from '../model/stock';
import {
  buildTechnicalChart,
  MovingAverageKey,
  TECHNICAL_PERIODS,
  TechnicalChartData,
  TechnicalChartPoint,
  TechnicalScaleOverlay,
} from './technical-chart-data';

type TechnicalRange = '15D' | '1M' | '3M' | '6M' | '1Y';
type TechnicalOverlay = TechnicalScaleOverlay;

const Y_AXIS_LABEL_SPACE = 60;
const TECHNICAL_RANGES: readonly TechnicalRange[] = ['15D', '1M', '3M', '6M', '1Y'];
const RANGE_SESSIONS: Record<TechnicalRange, number> = {
  '15D': 15,
  '1M': 21,
  '3M': 63,
  '6M': 126,
  '1Y': 252,
};

@Component({
  selector: 'app-technical-chart',
  templateUrl: './technical-chart.component.html',
  styleUrls: ['./technical-chart.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TechnicalChartComponent {
  readonly symbol = input('');
  readonly bars = input<StockDailyBar[]>([]);
  readonly periods = TECHNICAL_PERIODS;
  readonly ranges = TECHNICAL_RANGES;
  readonly range = signal<TechnicalRange>('1Y');
  readonly enabled = signal<ReadonlySet<TechnicalOverlay>>(new Set<TechnicalOverlay>([
    'ema14',
    'ema20',
    'bollinger',
    'levels',
  ]));
  readonly chart = computed<TechnicalChartData>(() =>
    buildTechnicalChart(this.bars(), RANGE_SESSIONS[this.range()], this.enabled()));
  readonly hoveredPoint = signal<TechnicalChartPoint | null>(null);

  private readonly priceFormatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  private readonly axisPriceFormatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  setRange(range: TechnicalRange): void {
    this.range.set(range);
    this.hoveredPoint.set(null);
  }

  isEnabled(overlay: TechnicalOverlay): boolean {
    return this.enabled().has(overlay);
  }

  toggle(overlay: TechnicalOverlay): void {
    const next = new Set(this.enabled());
    if (next.has(overlay)) {
      next.delete(overlay);
    } else {
      next.add(overlay);
    }
    this.enabled.set(next);
  }

  movingAverageKey(period: number): MovingAverageKey {
    return `ema${period}` as MovingAverageKey;
  }

  onChartMove(event: MouseEvent, chart: TechnicalChartData): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !chart.points.length) {
      return;
    }
    const scale = (chart.width + Y_AXIS_LABEL_SPACE) / rect.width;
    const x = (event.clientX - rect.left) * scale - Y_AXIS_LABEL_SPACE;
    let nearest = chart.points[0];
    for (const point of chart.points) {
      if (Math.abs(point.x - x) < Math.abs(nearest.x - x)) {
        nearest = point;
      }
    }
    this.hoveredPoint.set(nearest);
  }

  clearHover(): void {
    this.hoveredPoint.set(null);
  }

  formatPrice(value: number | null | undefined): string {
    return value == null || !Number.isFinite(value) ? '—' : this.priceFormatter.format(value);
  }

  formatAxisPrice(value: number): string {
    return this.axisPriceFormatter.format(value);
  }

  activeMovingAverages(point: TechnicalChartPoint): Array<{ label: string; value: number }> {
    return this.chart().lines
      .filter(line => this.isEnabled(line.key) && point.values[line.key] !== null)
      .map(line => ({ label: line.label, value: point.values[line.key] as number }));
  }
}
