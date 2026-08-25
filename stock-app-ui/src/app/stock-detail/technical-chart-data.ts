import { StockDailyBar } from '../model/stock';

export const TECHNICAL_PERIODS = [10, 14, 20, 50] as const;
export type TechnicalPeriod = typeof TECHNICAL_PERIODS[number];
export type MovingAverageKey = `ema${TechnicalPeriod}`;
export type TechnicalScaleOverlay = MovingAverageKey | 'bollinger' | 'levels';

export interface TechnicalBar extends StockDailyBar {
  open: number;
  high: number;
  low: number;
}

export interface PriceZone {
  kind: 'support' | 'resistance';
  center: number;
  lower: number;
  upper: number;
  touches: number;
  lastTradeDate: string;
}

export interface TechnicalChartPoint extends TechnicalBar {
  fullLabel: string;
  x: number;
  closeY: number;
  values: Record<MovingAverageKey, number | null>;
  bollingerUpper: number | null;
  bollingerMiddle: number | null;
  bollingerLower: number | null;
}

export interface TechnicalLine {
  key: MovingAverageKey;
  label: string;
  kind: 'ema';
  period: TechnicalPeriod;
  points: string;
}

export interface TechnicalChartData {
  points: TechnicalChartPoint[];
  closePoints: string;
  lines: TechnicalLine[];
  bollingerArea: string;
  bollingerUpper: string;
  bollingerMiddle: string;
  bollingerLower: string;
  zones: Array<PriceZone & { y: number; height: number }>;
  ticks: Array<{ value: number; y: number }>;
  xLabels: Array<{ x: number; label: string }>;
  width: number;
  height: number;
  padding: number;
  baselineY: number;
  viewBox: string;
  rangeLabel: string;
}

const WIDTH = 900;
const HEIGHT = 320;
const PADDING = 28;
const Y_AXIS_LABEL_SPACE = 60;
const DATE_LABEL = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});
const SHORT_DATE_LABEL = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
});
const FULL_DATE_LABEL = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
});

export function simpleMovingAverage(values: number[], period: number): Array<number | null> {
  const result = Array<number | null>(values.length).fill(null);
  if (period <= 0) {
    return result;
  }
  let sum = 0;
  for (let index = 0; index < values.length; index += 1) {
    sum += values[index];
    if (index >= period) {
      sum -= values[index - period];
    }
    if (index >= period - 1) {
      result[index] = sum / period;
    }
  }
  return result;
}

/** Match smallFish/pandas ewm(span=period, adjust=False): seed at the first close. */
export function exponentialMovingAverage(values: number[], period: number): Array<number | null> {
  const result = Array<number | null>(values.length).fill(null);
  if (period <= 0 || !values.length) {
    return result;
  }
  result[0] = values[0];
  const alpha = 2 / (period + 1);
  for (let index = 1; index < values.length; index += 1) {
    result[index] = values[index] * alpha + (result[index - 1] as number) * (1 - alpha);
  }
  return result;
}

export function bollingerBands(values: number[], period = 20, deviations = 2): {
  middle: Array<number | null>;
  upper: Array<number | null>;
  lower: Array<number | null>;
} {
  const middle = simpleMovingAverage(values, period);
  const upper = Array<number | null>(values.length).fill(null);
  const lower = Array<number | null>(values.length).fill(null);
  for (let index = period - 1; index < values.length; index += 1) {
    const window = values.slice(index - period + 1, index + 1);
    const mean = middle[index] as number;
    // pandas rolling.std(), used by smallFish shared TA, defaults to sample
    // deviation (ddof=1).
    const divisor = Math.max(period - 1, 1);
    const variance = window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / divisor;
    const width = Math.sqrt(variance) * deviations;
    upper[index] = mean + width;
    lower[index] = mean - width;
  }
  return { middle, upper, lower };
}

/**
 * Descriptive support/resistance zones from confirmed swing pivots.
 *
 * A pivot needs three completed sessions on both sides. Nearby pivots are
 * clustered, and a zone is shown only after at least two touches. This is
 * intentionally historical evidence, not a claim that price will react there.
 */
export function detectPriceZones(bars: TechnicalBar[], maximumPerSide = 3): PriceZone[] {
  if (bars.length < 15) {
    return [];
  }
  const pivotRadius = 3;
  const trueRanges = bars.map((bar, index) => {
    const previousClose = bars[index - 1]?.close ?? bar.close;
    return Math.max(
      bar.high - bar.low,
      Math.abs(bar.high - previousClose),
      Math.abs(bar.low - previousClose),
    );
  }).filter(value => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
  const medianTrueRange = trueRanges.length
    ? trueRanges[Math.floor(trueRanges.length / 2)]
    : 0;
  const latestClose = bars[bars.length - 1].close;
  const clusterTolerance = Math.max(latestClose * 0.012, medianTrueRange * 0.75);
  const pivots: Array<{ kind: PriceZone['kind']; price: number; tradeDate: string }> = [];

  for (let index = pivotRadius; index < bars.length - pivotRadius; index += 1) {
    const neighbours = bars.slice(index - pivotRadius, index + pivotRadius + 1);
    const bar = bars[index];
    const otherBars = neighbours.filter((_, neighbourIndex) => neighbourIndex !== pivotRadius);
    if (otherBars.every(item => bar.low <= item.low) && otherBars.some(item => bar.low < item.low)) {
      pivots.push({ kind: 'support', price: bar.low, tradeDate: bar.tradeDate });
    }
    if (otherBars.every(item => bar.high >= item.high) && otherBars.some(item => bar.high > item.high)) {
      pivots.push({ kind: 'resistance', price: bar.high, tradeDate: bar.tradeDate });
    }
  }

  const zones = (['support', 'resistance'] as const).flatMap(kind => {
    const clusters: Array<Array<{ price: number; tradeDate: string }>> = [];
    const candidates = pivots
      .filter(pivot => pivot.kind === kind)
      .sort((a, b) => a.price - b.price);
    for (const pivot of candidates) {
      const cluster = clusters.find(items => {
        const center = items.reduce((sum, item) => sum + item.price, 0) / items.length;
        return Math.abs(pivot.price - center) <= clusterTolerance;
      });
      if (cluster) {
        cluster.push(pivot);
      } else {
        clusters.push([pivot]);
      }
    }
    return clusters
      .filter(cluster => cluster.length >= 2)
      .map(cluster => {
        const center = cluster.reduce((sum, item) => sum + item.price, 0) / cluster.length;
        const halfWidth = Math.max(center * 0.0035, medianTrueRange * 0.25);
        return {
          kind,
          center,
          lower: center - halfWidth,
          upper: center + halfWidth,
          touches: cluster.length,
          lastTradeDate: cluster.map(item => item.tradeDate).sort().at(-1) as string,
        } satisfies PriceZone;
      });
  });

  const support = zones
    .filter(zone => zone.kind === 'support' && zone.upper < latestClose)
    .sort((a, b) => b.center - a.center)
    .slice(0, maximumPerSide);
  const resistance = zones
    .filter(zone => zone.kind === 'resistance' && zone.lower > latestClose)
    .sort((a, b) => a.center - b.center)
    .slice(0, maximumPerSide);
  return [...support, ...resistance];
}

export function buildTechnicalChart(
  rawBars: StockDailyBar[],
  sessions = 252,
  enabledOverlays: ReadonlySet<TechnicalScaleOverlay> = new Set(),
): TechnicalChartData {
  const empty: TechnicalChartData = {
    points: [], closePoints: '', lines: [], bollingerArea: '', bollingerUpper: '',
    bollingerMiddle: '', bollingerLower: '', zones: [], ticks: [], xLabels: [],
    width: WIDTH, height: HEIGHT, padding: PADDING, baselineY: HEIGHT - PADDING,
    viewBox: `-${Y_AXIS_LABEL_SPACE} 0 ${WIDTH + Y_AXIS_LABEL_SPACE} ${HEIGHT}`,
    rangeLabel: '',
  };
  const bars = rawBars
    .map(toTechnicalBar)
    .filter((bar): bar is TechnicalBar => bar !== null)
    .sort((a, b) => a.tradeDate.localeCompare(b.tradeDate));
  if (bars.length < 2) {
    return empty;
  }

  const closes = bars.map(bar => bar.close);
  const values = {} as Record<MovingAverageKey, Array<number | null>>;
  for (const period of TECHNICAL_PERIODS) {
    values[`ema${period}`] = exponentialMovingAverage(closes, period);
  }
  const bands = bollingerBands(closes);
  const startIndex = Math.max(0, bars.length - sessions);
  const visibleBars = bars.slice(startIndex);
  const visibleZones = detectPriceZones(visibleBars);

  // Closing prices are the base scale, exactly like the Price chart. Only
  // overlays that are currently visible may expand that range; hidden lines,
  // OHLC extremes, and hidden zones must not affect what the user sees.
  const scaleValues = visibleBars.flatMap((bar, visibleIndex) => {
    const sourceIndex = startIndex + visibleIndex;
    return [
      bar.close,
      ...TECHNICAL_PERIODS.flatMap(period => {
        const visibleValues: Array<number | null> = [];
        if (enabledOverlays.has(`ema${period}`)) {
          visibleValues.push(values[`ema${period}`][sourceIndex]);
        }
        return visibleValues;
      }),
      ...(enabledOverlays.has('bollinger')
        ? [bands.upper[sourceIndex], bands.lower[sourceIndex]]
        : []),
    ].filter((value): value is number => value !== null && Number.isFinite(value));
  });
  if (enabledOverlays.has('levels')) {
    scaleValues.push(...visibleZones.flatMap(zone => [zone.lower, zone.upper]));
  }
  const minValue = Math.min(...scaleValues);
  const maxValue = Math.max(...scaleValues);
  const spread = maxValue - minValue;
  const safeSpread = spread === 0 ? 1 : spread;
  const innerWidth = WIDTH - PADDING * 2;
  const innerHeight = HEIGHT - PADDING * 2;
  const baselineY = HEIGHT - PADDING;
  const step = innerWidth / Math.max(visibleBars.length - 1, 1);
  const xFor = (index: number) => PADDING + step * index;
  const yFor = (value: number) => spread === 0
    ? baselineY - innerHeight / 2
    : baselineY - ((value - minValue) / safeSpread) * innerHeight;

  const points: TechnicalChartPoint[] = visibleBars.map((bar, visibleIndex) => {
    const sourceIndex = startIndex + visibleIndex;
    const pointValues = {} as Record<MovingAverageKey, number | null>;
    for (const period of TECHNICAL_PERIODS) {
      pointValues[`ema${period}`] = values[`ema${period}`][sourceIndex];
    }
    return {
      ...bar,
      fullLabel: FULL_DATE_LABEL.format(parseMarketDate(bar.tradeDate)),
      x: xFor(visibleIndex),
      closeY: yFor(bar.close),
      values: pointValues,
      bollingerUpper: bands.upper[sourceIndex],
      bollingerMiddle: bands.middle[sourceIndex],
      bollingerLower: bands.lower[sourceIndex],
    };
  });

  const lines = TECHNICAL_PERIODS.map(period => {
    const key: MovingAverageKey = `ema${period}`;
    return {
      key,
      label: `EMA ${period}`,
      kind: 'ema',
      period,
      points: points
        .filter(point => point.values[key] !== null)
        .map(point => `${point.x},${yFor(point.values[key] as number)}`)
        .join(' '),
    } satisfies TechnicalLine;
  });

  const bandPoints = points.filter(point => point.bollingerUpper !== null && point.bollingerLower !== null);
  const bollingerArea = [
    ...bandPoints.map(point => `${point.x},${yFor(point.bollingerUpper as number)}`),
    ...bandPoints.slice().reverse().map(point => `${point.x},${yFor(point.bollingerLower as number)}`),
  ].join(' ');
  const bandLine = (key: 'bollingerUpper' | 'bollingerMiddle' | 'bollingerLower') => bandPoints
    .map(point => `${point.x},${yFor(point[key] as number)}`)
    .join(' ');

  const xAxisFormatter = sessions <= 63 ? SHORT_DATE_LABEL : DATE_LABEL;
  const labelCount = Math.min(6, points.length);
  const labelStride = Math.max(1, Math.floor((points.length - 1) / Math.max(labelCount - 1, 1)));
  const xLabels = points
    .filter((_, index) => index % labelStride === 0)
    .map(point => ({ x: point.x, label: xAxisFormatter.format(parseMarketDate(point.tradeDate)) }));
  const lastPoint = points[points.length - 1];
  const lastLabel = xAxisFormatter.format(parseMarketDate(lastPoint.tradeDate));
  if (xLabels.at(-1)?.x !== lastPoint.x) {
    if (xLabels.at(-1)?.label === lastLabel || lastPoint.x - (xLabels.at(-1)?.x ?? 0) < step * 8) {
      xLabels.pop();
    }
    xLabels.push({ x: lastPoint.x, label: lastLabel });
  }

  return {
    points,
    closePoints: points.map(point => `${point.x},${point.closeY}`).join(' '),
    lines,
    bollingerArea,
    bollingerUpper: bandLine('bollingerUpper'),
    bollingerMiddle: bandLine('bollingerMiddle'),
    bollingerLower: bandLine('bollingerLower'),
    zones: visibleZones.map(zone => {
      const top = yFor(zone.upper);
      const bottom = yFor(zone.lower);
      return { ...zone, y: top, height: Math.max(bottom - top, 1) };
    }),
    ticks: buildTicks(minValue, maxValue).map(value => ({ value, y: yFor(value) })),
    xLabels,
    width: WIDTH,
    height: HEIGHT,
    padding: PADDING,
    baselineY,
    viewBox: `-${Y_AXIS_LABEL_SPACE} 0 ${WIDTH + Y_AXIS_LABEL_SPACE} ${HEIGHT}`,
    rangeLabel: `${points[0].fullLabel} – ${lastPoint.fullLabel}`,
  };
}

function toTechnicalBar(bar: StockDailyBar): TechnicalBar | null {
  // The backend's market_date_iso may serialize a cached datetime with an
  // offset. Its leading YYYY-MM-DD remains the exchange-session date.
  const tradeDate = bar.tradeDate.slice(0, 10);
  const open = Number(bar.open);
  const high = Number(bar.high);
  const low = Number(bar.low);
  const close = Number(bar.close);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)
      || ![open, high, low, close].every(value => Number.isFinite(value) && value > 0)
      || high < Math.max(open, close, low)
      || low > Math.min(open, close, high)) {
    return null;
  }
  return { ...bar, tradeDate, open, high, low, close };
}

function parseMarketDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function buildTicks(minValue: number, maxValue: number): number[] {
  const spread = maxValue - minValue;
  if (!Number.isFinite(spread) || spread <= 0) {
    return [minValue];
  }
  const roughStep = spread / 4;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  const niceStep = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  const first = Math.ceil(minValue / niceStep) * niceStep;
  const ticks: number[] = [];
  for (let value = first; value <= maxValue + niceStep * 0.001; value += niceStep) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks;
}
