/** Match stock-app/app/trend_engine.py, not the first-close-seeded EMA overlays. */
export interface MacdValue {
  macd: number | null;
  signal: number | null;
  histogram: number | null;
}

export interface MacdPanel {
  macdLine: string;
  signalLine: string;
  bars: Array<{ x: number; y: number; width: number; height: number; value: number }>;
  ticks: Array<{ value: number; y: number }>;
  zeroY: number;
  height: number;
  viewBox: string;
  hasSignal: boolean;
}

function seededEma(values: number[], period: number): Array<number | null> {
  const result = Array<number | null>(values.length).fill(null);
  if (values.length < period) return result;
  let sum = 0;
  for (let index = 0; index < period; index++) sum += values[index];
  result[period - 1] = sum / period;
  const alpha = 2 / (period + 1);
  for (let index = period; index < values.length; index++) {
    result[index] = values[index] * alpha + (result[index - 1] as number) * (1 - alpha);
  }
  return result;
}

export function calculateMacd(closes: number[]): MacdValue[] {
  // Restore the backend's float32 closes from its concise JSON serialization.
  const values = closes.map(value => Math.fround(value));
  const unavailable = () => values.map(() => ({ macd: null, signal: null, histogram: null }));
  if (values.some(value => !Number.isFinite(value) || value <= 0)) return unavailable();
  const fast = seededEma(values, 12);
  const slow = seededEma(values, 26);
  const macd = values.map((_, index) => fast[index] !== null && slow[index] !== null
    ? (fast[index] as number) - (slow[index] as number) : null);
  const firstValid = macd.findIndex(value => value !== null);
  if (firstValid < 0) return unavailable();
  const signal = seededEma(macd.slice(firstValid) as number[], 9);
  return macd.map((value, index) => {
    const signalValue = index < firstValid ? null : signal[index - firstValid];
    return {
      macd: value,
      signal: signalValue,
      histogram: value !== null && signalValue !== null ? value - signalValue : null,
    };
  });
}

/** Independent symmetric oscillator scale; use the price chart's exact x positions. */
export function buildMacdPanel(points: Array<MacdValue & { x: number }>, width = 900, padding = 28): MacdPanel {
  const height = 180;
  const zeroY = height / 2;
  const defined = points.flatMap(point => [point.macd, point.signal, point.histogram])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const limit = Math.max(...defined.map(Math.abs), 0) * 1.1 || 1;
  const yFor = (value: number) => zeroY - value / limit * (height / 2 - padding);
  const line = (key: 'macd' | 'signal') => points.filter(point => point[key] !== null)
    .map(point => `${point.x},${yFor(point[key] as number)}`).join(' ');
  const barWidth = Math.min(12, ((points[1]?.x ?? padding + 10) - (points[0]?.x ?? padding)) * 0.7);
  return {
    macdLine: line('macd'),
    signalLine: line('signal'),
    bars: points.filter(point => point.histogram !== null).map(point => {
      const value = point.histogram as number;
      const y = yFor(value);
      return { x: point.x - barWidth / 2, y: Math.min(y, zeroY), width: barWidth, height: Math.abs(y - zeroY), value };
    }),
    ticks: [-limit, -limit / 2, 0, limit / 2, limit].map(value => ({ value, y: yFor(value) })),
    zeroY,
    height,
    viewBox: `-60 0 ${width + 60} ${height}`,
    hasSignal: points.some(point => point.signal !== null),
  };
}
