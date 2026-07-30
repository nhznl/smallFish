import { Component, OnInit, inject, signal, computed, ChangeDetectionStrategy, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { EMPTY, merge } from 'rxjs';
import { catchError, distinctUntilChanged, map, switchMap, tap } from 'rxjs/operators';

import { StockService } from '../api/stock.service';
import { StockAnalysis, YearlySlope } from '../model/stock';
import { StockInfo } from '../model/stock-info';

const WEEKLY_CHART_HEIGHT = 240;
const WEEKLY_CHART_MIN_WIDTH = 320;
const WEEKLY_CHART_POINT_GAP = 56;
const WEEKLY_CHART_MAX_POINTS = 52;
const WEEKLY_CHART_PADDING = 28;
const WEEKLY_CHART_Y_AXIS_LABEL_SPACE = 60; // Space for Y-axis price labels on the left
const WEEK_LABEL_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });

type WeeklyClosePoint = {
  label: string;
  value: number;
  x: number;
  y: number;
};

type WeeklyCloseTick = {
  value: number;
  y: number;
};

type WeeklyCloseBoundary = {
  x: number;
  year: number;
};

type WeeklyCloseChart = {
  points: WeeklyClosePoint[];
  polyline: string;
  area: string;
  ticks: WeeklyCloseTick[];
  width: number;
  height: number;
  padding: number;
  innerWidth: number;
  baselineY: number;
  viewBox: string;
  year: number;
  rangeLabel: string;
  boundaries: WeeklyCloseBoundary[];
};

// Price chart. Unlike the weekly chart this one scales to its container rather
// than scrolling: a 1Y range is ~250 points, far too many to scroll through.
const PRICE_CHART_HEIGHT = 260;
const PRICE_CHART_WIDTH = 900;
const PRICE_CHART_PADDING = 28;
const PRICE_CHART_Y_AXIS_LABEL_SPACE = 60;
const PRICE_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
// A range spanning years labels by month + full year. A 2-digit year reads as a
// day of the month ("Jul 25 – Jul 26" looks like two days, not two years).
const PRICE_DATE_YEAR_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric' });
// Hover always shows the exact session, whatever the axis labels are grouped by.
const PRICE_DATE_FULL_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric'
});

/**
 * Selectable chart ranges. `sessions` is a trailing count of cached daily bars;
 * `ytd` is calendar-anchored instead. There is deliberately no 1D option: the
 * price cache stores one bar per session, so the shortest honest range is 5D.
 */
type PriceRangeKey = '5D' | '1M' | '6M' | 'YTD' | '1Y';

const PRICE_RANGES: { key: PriceRangeKey; label: string; sessions?: number; ytd?: boolean }[] = [
  { key: '5D', label: '5D', sessions: 5 },
  { key: '1M', label: '1M', sessions: 21 },
  { key: '6M', label: '6M', sessions: 126 },
  { key: 'YTD', label: 'YTD', ytd: true },
  { key: '1Y', label: '1Y', sessions: 252 }
];

type PricePoint = {
  /** Axis label; grouped by month once a range spans years. */
  label: string;
  /** Exact session date, shown on hover. */
  fullLabel: string;
  value: number;
  x: number;
  y: number;
};

type PriceChart = {
  points: PricePoint[];
  polyline: string;
  area: string;
  ticks: WeeklyCloseTick[];
  xLabels: { x: number; label: string }[];
  width: number;
  height: number;
  padding: number;
  baselineY: number;
  viewBox: string;
  rangeLabel: string;
  first: number | null;
  last: number | null;
  changePct: number | null;
  rising: boolean;
};

type HeatmapCell = {
  week: number;
  value: number | null;
};

type HeatmapRow = {
  year: number;
  cells: HeatmapCell[];
};

type HeatmapData = {
  rows: HeatmapRow[];
  extreme: number;
};

@Component({
  selector: 'app-stock-detail',
  templateUrl: './stock-detail.component.html',
  styleUrls: ['./stock-detail.component.css'],
  imports: [RouterLink, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class StockDetailComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly stockService = inject(StockService);
  private readonly destroyRef = inject(DestroyRef);

  // Signals for reactive state management
  private readonly _symbol = signal<string>('');
  private readonly _stock = signal<StockAnalysis | null>(null);
  private readonly _stockError = signal<string | null>(null);
  private readonly _stockInfo = signal<StockInfo | null>(null);
  private readonly _stockInfoLoading = signal(false);
  private readonly _stockInfoError = signal<string | null>(null);
  private readonly slopeFormatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  private readonly priceFormatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  private readonly numberFormatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  });
  private readonly percentFormatter = new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  private readonly compactNumberFormatter = new Intl.NumberFormat('en-US', {
    notation: 'compact',
    compactDisplay: 'short',
    maximumFractionDigits: 2
  });
  private readonly newsDateFormatter = new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short'
  });
  private readonly shortDateFormatter = new Intl.DateTimeFormat('en-US', {
    month: '2-digit',
    day: '2-digit',
    year: 'numeric'
  });

  // Computed values
  readonly symbol = this._symbol.asReadonly();
  readonly stock = this._stock.asReadonly();
  readonly stockError = this._stockError.asReadonly();
  readonly stockInfo = this._stockInfo.asReadonly();
  readonly stockInfoLoading = this._stockInfoLoading.asReadonly();
  readonly stockInfoError = this._stockInfoError.asReadonly();
  readonly slopeYears = computed<YearlySlope[]>(() => {
    const current = this._stock();
    if (!current?.yearlySlopes) {
      return [];
    }
    return Object.values(current.yearlySlopes)
      .filter((slope): slope is YearlySlope => !!slope)
      .sort((a, b) => a.year - b.year);
  });
  readonly weeklyColumns = computed<number[]>(() => {
    const years = this.slopeYears();
    if (!years.length) {
      return [];
    }
    let maxWeek = 0;
    for (const slope of years) {
      for (const key of Object.keys(slope.weeklySlopes ?? {})) {
        const weekNumber = Number(key);
        if (!Number.isNaN(weekNumber)) {
          maxWeek = Math.max(maxWeek, weekNumber);
        }
      }
    }
    return Array.from({ length: maxWeek }, (_, index) => index + 1);
  });
  readonly weeklyCloseChart = computed<WeeklyCloseChart>(() => {
    const fallback: WeeklyCloseChart = {
      points: [],
      polyline: '',
      area: '',
      ticks: [],
      width: WEEKLY_CHART_MIN_WIDTH,
      height: WEEKLY_CHART_HEIGHT,
      padding: WEEKLY_CHART_PADDING,
      innerWidth: WEEKLY_CHART_MIN_WIDTH - WEEKLY_CHART_PADDING * 2,
      baselineY: WEEKLY_CHART_HEIGHT - WEEKLY_CHART_PADDING,
      viewBox: `-${WEEKLY_CHART_Y_AXIS_LABEL_SPACE} 0 ${WEEKLY_CHART_MIN_WIDTH + WEEKLY_CHART_Y_AXIS_LABEL_SPACE} ${WEEKLY_CHART_HEIGHT}`,
      year: new Date().getFullYear(),
      rangeLabel: '',
      boundaries: []
    };

    const current = this._stock();
    if (!current?.recentWeeks?.length) {
      return fallback;
    }

    const weeks = current.recentWeeks
      .map(week => {
        const close = Number(week.avgClose);
        if (!Number.isFinite(close)) {
          return null;
        }
        const start = this.parseDate(week.startDate);
        const end = this.parseDate(week.endDate);
        const year = end?.getFullYear() ?? start?.getFullYear();
        return {
          start,
          end,
          year,
          close
        };
      })
      .filter((week): week is { start: Date | null; end: Date | null; year: number | undefined; close: number } => !!week)
      .sort((a, b) => {
        const aTime = a.start?.getTime() ?? a.end?.getTime() ?? 0;
        const bTime = b.start?.getTime() ?? b.end?.getTime() ?? 0;
        return aTime - bTime;
      })
      // Rolling window, not the current calendar year — filtering by year left
      // the chart nearly empty every January.
      .slice(-WEEKLY_CHART_MAX_POINTS);

    if (!weeks.length) {
      return fallback;
    }

    const minValue = Math.min(...weeks.map(week => week.close));
    const maxValue = Math.max(...weeks.map(week => week.close));
    const padding = WEEKLY_CHART_PADDING;
    const height = WEEKLY_CHART_HEIGHT;
    const width = Math.max(
      WEEKLY_CHART_MIN_WIDTH,
      padding * 2 + WEEKLY_CHART_POINT_GAP * Math.max(weeks.length - 1, 0)
    );
    const innerHeight = height - padding * 2;
    const innerWidth = width - padding * 2;
    const baselineY = height - padding;
    const range = maxValue - minValue;
    const safeRange = range === 0 ? 1 : range;
    const step = weeks.length > 1 ? innerWidth / (weeks.length - 1) : 0;

    const points: WeeklyClosePoint[] = weeks.map((week, index) => {
      const label = week.end ? WEEK_LABEL_FORMATTER.format(week.end) : `Week ${index + 1}`;
      const ratio = range === 0 ? 0.5 : (week.close - minValue) / safeRange;
      const y = baselineY - ratio * innerHeight;
      const x = padding + step * index;
      return {
        label,
        value: week.close,
        x,
        y
      };
    });

    const polyline = points.map(point => `${point.x},${point.y}`).join(' ');
    const areaPoints = [
      `${padding},${baselineY}`,
      ...points.map(point => `${point.x},${point.y}`),
      `${padding + step * Math.max(points.length - 1, 0)},${baselineY}`
    ].join(' ');

    const ticks: WeeklyCloseTick[] = this.buildLineChartTicks(minValue, maxValue).map(value => {
      const ratio = range === 0 ? 0.5 : (value - minValue) / safeRange;
      const y = baselineY - ratio * innerHeight;
      return { value, y };
    });

    return {
      points,
      polyline,
      area: areaPoints,
      ticks,
      width,
      height,
      padding,
      innerWidth,
      baselineY,
      viewBox: `-${WEEKLY_CHART_Y_AXIS_LABEL_SPACE} 0 ${width + WEEKLY_CHART_Y_AXIS_LABEL_SPACE} ${height}`,
      year: weeks[weeks.length - 1]?.year ?? new Date().getFullYear(),
      rangeLabel: points.length ? `${points[0].label} – ${points[points.length - 1].label}` : '',
      boundaries: weeks.reduce<WeeklyCloseBoundary[]>((acc, week, index) => {
        const previous = weeks[index - 1];
        if (previous && week.year && previous.year && week.year !== previous.year) {
          acc.push({ x: padding + step * index, year: week.year });
        }
        return acc;
      }, [])
    };
  });
  // ── Price chart ─────────────────────────────────────────────────────────
  readonly priceRanges = PRICE_RANGES;
  readonly priceRange = signal<PriceRangeKey>('6M');

  setPriceRange(key: PriceRangeKey): void {
    this.priceRange.set(key);
    this.hoveredPricePoint = null;
  }

  readonly priceChart = computed<PriceChart>(() => {
    const empty: PriceChart = {
      points: [], polyline: '', area: '', ticks: [], xLabels: [],
      width: PRICE_CHART_WIDTH, height: PRICE_CHART_HEIGHT,
      padding: PRICE_CHART_PADDING,
      baselineY: PRICE_CHART_HEIGHT - PRICE_CHART_PADDING,
      viewBox: `-${PRICE_CHART_Y_AXIS_LABEL_SPACE} 0 ${PRICE_CHART_WIDTH + PRICE_CHART_Y_AXIS_LABEL_SPACE} ${PRICE_CHART_HEIGHT}`,
      rangeLabel: '', first: null, last: null, changePct: null, rising: true
    };

    const bars = (this._stock()?.dailyBars ?? [])
      .map(bar => ({ date: this.parseDate(bar.tradeDate), close: Number(bar.close) }))
      .filter((bar): bar is { date: Date; close: number } =>
        !!bar.date && Number.isFinite(bar.close))
      .sort((a, b) => a.date.getTime() - b.date.getTime());
    if (!bars.length) {
      return empty;
    }

    const range = PRICE_RANGES.find(item => item.key === this.priceRange()) ?? PRICE_RANGES[2];
    let selected: typeof bars;
    if (range.ytd) {
      // Anchored to the latest cached session's calendar year, not "today", so
      // the range stays correct when the cache lags the wall clock.
      const year = bars[bars.length - 1].date.getFullYear();
      selected = bars.filter(bar => bar.date.getFullYear() === year);
    } else {
      selected = bars.slice(-(range.sessions ?? 126));
    }
    if (selected.length < 2) {
      // One point cannot draw a line; fall back to the widest available window.
      selected = bars.slice(-Math.max(2, Math.min(bars.length, 21)));
    }
    if (selected.length < 2) {
      return empty;
    }

    const closes = selected.map(bar => bar.close);
    const minValue = Math.min(...closes);
    const maxValue = Math.max(...closes);
    const spread = maxValue - minValue;
    const safeSpread = spread === 0 ? 1 : spread;
    const padding = PRICE_CHART_PADDING;
    const width = PRICE_CHART_WIDTH;
    const height = PRICE_CHART_HEIGHT;
    const innerHeight = height - padding * 2;
    const innerWidth = width - padding * 2;
    const baselineY = height - padding;
    const step = innerWidth / (selected.length - 1);
    const multiYear = selected[0].date.getFullYear() !== selected[selected.length - 1].date.getFullYear();

    const points: PricePoint[] = selected.map((bar, index) => {
      const ratio = spread === 0 ? 0.5 : (bar.close - minValue) / safeSpread;
      return {
        label: (multiYear ? PRICE_DATE_YEAR_FORMATTER : PRICE_DATE_FORMATTER).format(bar.date),
        fullLabel: PRICE_DATE_FULL_FORMATTER.format(bar.date),
        value: bar.close,
        x: padding + step * index,
        y: baselineY - ratio * innerHeight
      };
    });

    const ticks: WeeklyCloseTick[] = this.buildLineChartTicks(minValue, maxValue).map(value => {
      const ratio = spread === 0 ? 0.5 : (value - minValue) / safeSpread;
      return { value, y: baselineY - ratio * innerHeight };
    });

    // At most six evenly spaced date labels, so a 1Y range stays readable.
    const labelCount = Math.min(6, points.length);
    const labelStride = Math.max(1, Math.floor((points.length - 1) / Math.max(labelCount - 1, 1)));
    const xLabels: { x: number; label: string }[] = [];
    for (let index = 0; index < points.length; index += labelStride) {
      xLabels.push({ x: points[index].x, label: points[index].label });
    }
    // Always end on the final session. Drop a trailing stride label that would
    // crowd or duplicate it (a monthly label can repeat within the same month).
    const lastPoint = points[points.length - 1];
    const previous = xLabels[xLabels.length - 1];
    if (previous && (lastPoint.x - previous.x < step * 8 || previous.label === lastPoint.label)) {
      xLabels.pop();
    }
    if (xLabels[xLabels.length - 1]?.x !== lastPoint.x) {
      xLabels.push({ x: lastPoint.x, label: lastPoint.label });
    }

    const first = closes[0];
    const last = closes[closes.length - 1];
    const changePct = first === 0 ? null : (last - first) / first;

    return {
      points,
      polyline: points.map(point => `${point.x},${point.y}`).join(' '),
      area: [
        `${padding},${baselineY}`,
        ...points.map(point => `${point.x},${point.y}`),
        `${padding + step * (points.length - 1)},${baselineY}`
      ].join(' '),
      ticks,
      xLabels,
      width,
      height,
      padding,
      baselineY,
      viewBox: `-${PRICE_CHART_Y_AXIS_LABEL_SPACE} 0 ${width + PRICE_CHART_Y_AXIS_LABEL_SPACE} ${height}`,
      rangeLabel: `${points[0].fullLabel} – ${lastPoint.fullLabel}`,
      first,
      last,
      changePct,
      rising: changePct === null ? true : changePct >= 0
    };
  });

  hoveredPricePoint: PricePoint | null = null;

  onPriceChartMove(event: MouseEvent, chart: PriceChart): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !chart.points.length) {
      return;
    }
    const scale = (chart.width + PRICE_CHART_Y_AXIS_LABEL_SPACE) / rect.width;
    const x = (event.clientX - rect.left) * scale - PRICE_CHART_Y_AXIS_LABEL_SPACE;
    let nearest = chart.points[0];
    for (const point of chart.points) {
      if (Math.abs(point.x - x) < Math.abs(nearest.x - x)) {
        nearest = point;
      }
    }
    this.hoveredPricePoint = nearest;
  }

  clearPriceChartHover(): void {
    this.hoveredPricePoint = null;
  }

  /** Yahoo Finance quote page for the current symbol. */
  yahooFinanceUrl(): string {
    return `https://finance.yahoo.com/quote/${encodeURIComponent(this.symbol().toUpperCase())}`;
  }

  readonly slopeHeatmap = computed<HeatmapData>(() => {
    const years = this.slopeYears();
    const weeks = this.weeklyColumns();
    if (!years.length || !weeks.length) {
      return { rows: [], extreme: 0 };
    }

    let extreme = 0;
    const rows: HeatmapRow[] = years.map(yearSlope => {
      const cells: HeatmapCell[] = weeks.map(week => {
        const slope = this.getWeeklySlope(yearSlope, week);
        if (slope === undefined || slope === null || Number.isNaN(slope)) {
          return { week, value: null };
        }
        const numeric = Number(slope);
        if (!Number.isFinite(numeric)) {
          return { week, value: null };
        }
        extreme = Math.max(extreme, Math.abs(numeric));
        return { week, value: numeric };
      });
      return { year: yearSlope.year, cells };
    });

    return { rows, extreme };
  });

  ngOnInit(): void {
    // switchMap cancels in-flight analysis/info when the route symbol changes so
    // a slower response for A cannot overwrite a newer load for B.
    this.route.params.pipe(
      map(params => (params['symbol'] ?? '').toString().toUpperCase()),
      distinctUntilChanged(),
      tap(symbol => this._symbol.set(symbol)),
      switchMap(symbol => this.loadForSymbol(symbol)),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe();
  }

  private loadForSymbol(symbol: string) {
    if (!symbol) {
      this._stock.set(null);
      this._stockError.set(null);
      this._stockInfo.set(null);
      this._stockInfoError.set(null);
      this._stockInfoLoading.set(false);
      return EMPTY;
    }

    this._stockError.set(null);
    this._stockInfoError.set(null);
    this._stockInfoLoading.set(true);
    this._stockInfo.set(null);

    const analysis$ = this.stockService.getStockAnalysis(symbol).pipe(
      tap(stock => {
        this._stock.set(stock);
        this._stockError.set(null);
      }),
      catchError(error => {
        console.error('Error fetching stock details:', error);
        this._stock.set(null);
        // A 404 here is nearly always the scanner cache's sub-$6 exclusion
        // rather than a bad ticker. Only the cache-derived sections are
        // affected; the live yfinance blocks render regardless.
        this._stockError.set(error?.status === 404
          ? `${symbol.toUpperCase()} is not in the scanner cache, which excludes symbols `
            + `trading under $6. Live company data above is unaffected.`
          : this.resolveErrorMessage(error));
        return EMPTY;
      }),
    );

    const info$ = this.stockService.getStockInfo(symbol).pipe(
      tap(info => {
        this._stockInfo.set(info);
        this._stockInfoLoading.set(false);
      }),
      catchError(error => {
        console.error('Error fetching stock information:', error);
        this._stockInfoError.set(this.resolveErrorMessage(error));
        this._stockInfoLoading.set(false);
        return EMPTY;
      }),
    );

    return merge(analysis$, info$);
  }

  getWeeklySlope(slope: YearlySlope, week: number): number | undefined {
    const key = String(week);
    return slope.weeklySlopes?.[key];
  }


  formatSlope(value: number | undefined | null): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '-';
    }
    const scaled = value * 100;
    if (!Number.isFinite(scaled)) {
      return '-';
    }
    return this.slopeFormatter.format(scaled);
  }

  formatPrice(value: number | undefined | null): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '-';
    }
    return this.priceFormatter.format(value);
  }

  formatNumber(value: number | undefined | null, fractionDigits = 2): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '-';
    }
    if (fractionDigits !== 2) {
      const formatter = new Intl.NumberFormat('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: fractionDigits
      });
      return formatter.format(value);
    }
    return this.numberFormatter.format(value);
  }

  formatPercent(value: number | undefined | null): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '-';
    }
    const normalized = value > 1 ? value / 100 : value;
    return this.percentFormatter.format(normalized);
  }

  formatCompactNumber(value: number | undefined | null): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return '-';
    }
    return this.compactNumberFormatter.format(value);
  }

  formatNewsDate(value: string | undefined | null): string {
    if (!value) {
      return '';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return '';
    }
    return this.newsDateFormatter.format(parsed);
  }

  formatRetrievedDate(value: string | undefined | null): string {
    if (!value) {
      return '';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return '';
    }
    return this.shortDateFormatter.format(parsed);
  }

  formatDate(value: string | undefined | null): string {
    if (!value) {
      return '';
    }
    const dateObj = new Date(value);
    if (Number.isNaN(dateObj.getTime())) {
      return '';
    }
    return this.shortDateFormatter.format(dateObj);
  }

  getCompanyName(info: StockInfo | null): string {
    if (!info) {
      return '';
    }
    return info.company.longName ?? info.company.shortName ?? info.ticker;
  }

  getWebsiteDisplay(url: string | null | undefined): string | null {
    if (!url) {
      return null;
    }
    try {
      const hostname = new URL(url).hostname;
      return hostname.startsWith('www.') ? hostname.slice(4) : hostname;
    } catch (error) {
      return url;
    }
  }

  getCompanyLocation(info: StockInfo | null): string {
    if (!info) {
      return '';
    }
    const { city, state, country } = info.company;
    return [city, state, country].filter((part): part is string => !!part && part.length > 0).join(', ');
  }

  getHeatmapColor(value: number | null | undefined, extreme: number): string {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return 'var(--heatmap-empty, #f3f4f6)';
    }
    if (!Number.isFinite(extreme) || extreme === 0) {
      return 'var(--heatmap-zero, #e5e7eb)';
    }
    // Diverging scale anchored at zero: red → white → green, matching the
    // app-wide convention that positive is green.
    const normalized = Math.min(1, Math.abs(value) / extreme);
    const lightness = 96 - normalized * 42;
    const saturation = 18 + normalized * 42;
    const hue = value >= 0 ? 142 : 2;
    return `hsl(${hue}deg, ${saturation}%, ${lightness}%)`;
  }

  private readonly router = inject(Router);

  abs(value: number): number {
    return Math.abs(value);
  }

  /** The page opens in a new tab, so it carries its own symbol switcher. */
  jumpToSymbol(event: Event): void {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const input = form.elements.namedItem('symbol') as HTMLInputElement | null;
    const next = (input?.value ?? '').trim().toUpperCase();
    if (!next) {
      return;
    }
    form.reset();
    this.router.navigate(['/stockDetail', next]);
  }

  /** Cached-analysis metrics already present on the loaded Stock Detail payload. */
  setupLabel(setup?: string): string {
    switch (setup) {
      case 'BULLISH_CONTINUATION': return 'Bullish';
      case 'BEARISH_CONTINUATION': return 'Bearish';
      case 'BULLISH_REVERSAL': return 'Bullish Reversal';
      case 'BEARISH_REVERSAL': return 'Bearish Reversal';
      case 'NOT_EVALUATED': return 'Not Evaluated';
      default: return 'Watch';
    }
  }

  setupBadgeClass(setup?: string): string {
    switch (setup) {
      case 'BULLISH_CONTINUATION': return 'badge-pos';
      case 'BEARISH_CONTINUATION': return 'badge-neg';
      case 'BULLISH_REVERSAL': return 'badge-warn';
      case 'BEARISH_REVERSAL': return 'badge-info';
      default: return 'badge-neutral';
    }
  }

  /** Signed percent with the app-wide +/− and colour convention. */
  formatSigned(value: number | null | undefined, digits = 1): string {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return '—';
    }
    const numeric = Number(value);
    return `${numeric >= 0 ? '+' : '−'}${Math.abs(numeric).toFixed(digits)}%`;
  }

  /** ATR and similar always-positive percentages take no sign. */
  formatPercentValue(value: number | null | undefined, digits = 1): string {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return '—';
    }
    return `${Number(value).toFixed(digits)}%`;
  }

  signClass(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '';
    return Number(value) >= 0 ? 'pos-value' : 'neg-value';
  }

  /** Price change against the previous close, for the ticker hero. */
  priceDelta(): { abs: number; pct: number } | null {
    const price = this.stockInfo()?.price;
    const last = Number(price?.regularMarketPrice);
    const previous = Number(price?.regularMarketPreviousClose);
    if (!Number.isFinite(last) || !Number.isFinite(previous) || previous === 0) {
      return null;
    }
    return { abs: last - previous, pct: ((last - previous) / previous) * 100 };
  }

  /** Current price's position along the 52-week range, as a percentage. */
  fiftyTwoWeekPosition(): number | null {
    const price = this.stockInfo()?.price;
    const low = Number(price?.fiftyTwoWeekLow);
    const high = Number(price?.fiftyTwoWeekHigh);
    const last = Number(price?.regularMarketPrice);
    if (![low, high, last].every(Number.isFinite) || high <= low) {
      return null;
    }
    return Math.min(100, Math.max(0, ((last - low) / (high - low)) * 100));
  }

  /** Swatches for the heat-map legend: −extreme … 0 … +extreme. */
  heatmapLegend(extreme: number): string[] {
    return [-1, -0.5, 0, 0.5, 1].map(stop => this.getHeatmapColor(stop * extreme, extreme));
  }

  /** Heat-map week columns are dense, so only every fourth carries a label. */
  isLabelledWeek(week: number): boolean {
    return week === 1 || week % 4 === 0;
  }

  /** Line-chart x labels: every point when the series is short, else thinned. */
  isLabelledPoint(index: number, total: number): boolean {
    if (total <= 14) return true;
    const stride = Math.ceil(total / 12);
    return index === 0 || index === total - 1 || index % stride === 0;
  }

  /** Hover state for the line chart's nearest-point crosshair. */
  hoveredPoint: WeeklyClosePoint | null = null;

  onChartMove(event: MouseEvent, chart: WeeklyCloseChart): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !chart.points.length) {
      return;
    }
    const scale = (chart.width + WEEKLY_CHART_Y_AXIS_LABEL_SPACE) / rect.width;
    const x = (event.clientX - rect.left) * scale - WEEKLY_CHART_Y_AXIS_LABEL_SPACE;
    let nearest = chart.points[0];
    for (const point of chart.points) {
      if (Math.abs(point.x - x) < Math.abs(nearest.x - x)) {
        nearest = point;
      }
    }
    this.hoveredPoint = nearest;
  }

  clearChartHover(): void {
    this.hoveredPoint = null;
  }

  private resolveErrorMessage(error: unknown): string {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    if (typeof error === 'string') {
      return error;
    }
    if (error && typeof error === 'object' && 'message' in error) {
      const maybeMessage = (error as { message?: unknown }).message;
      if (typeof maybeMessage === 'string' && maybeMessage.trim().length) {
        return maybeMessage;
      }
    }
    return 'Failed to load stock information.';
  }

  private parseDate(value: string | null | undefined): Date | null {
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  private buildLineChartTicks(min: number, max: number): number[] {
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return [];
    }
    if (Math.abs(max - min) < Number.EPSILON) {
      return [min];
    }
    // "Nice" rounded ticks (1/2/5 × 10^n) instead of raw max/mid/min, which
    // produced labels like $173.42.
    const target = 5;
    const rawStep = (max - min) / (target - 1);
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / magnitude;
    const niceStep = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
    const ticks: number[] = [];
    for (let tick = Math.ceil(min / niceStep) * niceStep; tick <= max + Number.EPSILON; tick += niceStep) {
      ticks.push(Number(tick.toFixed(6)));
    }
    return ticks.length >= 2 ? ticks : [min, max];
  }
}
