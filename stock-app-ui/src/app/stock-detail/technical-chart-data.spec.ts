import { StockDailyBar } from '../model/stock';
import {
  bollingerBands,
  buildTechnicalChart,
  detectPriceZones,
  exponentialMovingAverage,
  simpleMovingAverage,
  TechnicalBar,
} from './technical-chart-data';

describe('technical chart data', () => {
  it('calculates SMA and the same adjust-false EMA used by smallFish', () => {
    const values = [1, 2, 3, 4, 5];

    expect(simpleMovingAverage(values, 3)).toEqual([null, null, 2, 3, 4]);
    expect(exponentialMovingAverage(values, 3)).toEqual([1, 1.5, 2.25, 3.125, 4.0625]);
  });

  it('calculates 20-session Bollinger Bands with smallFish sample deviation', () => {
    const bands = bollingerBands(Array.from({ length: 20 }, (_, index) => index + 1));

    expect(bands.middle[19]).toBeCloseTo(10.5, 8);
    expect(bands.upper[19]).toBeCloseTo(22.3322, 4);
    expect(bands.lower[19]).toBeCloseTo(-1.3322, 4);
    expect(bands.upper[18]).toBeNull();
  });

  it('requires repeated, confirmed pivots before emitting support and resistance zones', () => {
    const bars: TechnicalBar[] = Array.from({ length: 20 }, (_, index) => ({
      tradeDate: `2026-01-${String(index + 1).padStart(2, '0')}`,
      open: 100,
      high: 103,
      low: 97,
      close: 100,
      volume: 1_000,
    }));
    bars[3] = { ...bars[3], low: 90 };
    bars[6] = { ...bars[6], high: 110 };
    bars[10] = { ...bars[10], low: 90.4 };
    bars[14] = { ...bars[14], high: 109.7 };

    const zones = detectPriceZones(bars);

    expect(zones.length).toBe(2);
    expect(zones.find(zone => zone.kind === 'support')?.touches).toBe(2);
    expect(zones.find(zone => zone.kind === 'resistance')?.touches).toBe(2);
  });

  it('builds all requested EMA paths without truncating warm-up history', () => {
    const bars: StockDailyBar[] = Array.from({ length: 80 }, (_, index) => {
      const close = 100 + index * 0.25;
      return {
        tradeDate: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
        open: close - 0.2,
        high: close + 1,
        low: close - 1,
        close,
        volume: 1_000 + index,
      };
    });

    const chart = buildTechnicalChart(bars, 30);

    expect(chart.points.length).toBe(30);
    expect(chart.lines.map(line => line.key)).toEqual([
      'ema10', 'ema14', 'ema20', 'ema50',
    ]);
    expect(chart.lines.every(line => line.points.length > 0)).toBeTrue();
    expect(chart.bollingerArea.length).toBeGreaterThan(0);
    expect(chart.rangeLabel).toContain('2026');
    const shortChart = buildTechnicalChart(bars, 15);
    expect(shortChart.points[0].macd).toBe(chart.points.at(-15)?.macd ?? null);
    expect(shortChart.points[0].signal).toBe(chart.points.at(-15)?.signal ?? null);
    expect(shortChart.macdPanel.hasSignal).toBeTrue();
    expect(shortChart.macdPanel.macdLine.split(' ')[0].split(',')[0])
      .toBe(String(shortChart.points[0].x));
  });

  it('normalizes ISO cache timestamps to their exchange-session date', () => {
    const chart = buildTechnicalChart([
      { tradeDate: '2026-07-27T07:00:00.000+00:00', open: 99, high: 101, low: 98, close: 100, volume: 1_000 },
      { tradeDate: '2026-07-28T07:00:00.000+00:00', open: 100, high: 102, low: 99, close: 101, volume: 1_100 },
    ]);

    expect(chart.points.map(point => point.tradeDate)).toEqual(['2026-07-27', '2026-07-28']);
    expect(chart.rangeLabel).toBe('Jul 27, 2026 – Jul 28, 2026');
  });

  it('uses distinct month-day labels for short technical ranges', () => {
    const chart = buildTechnicalChart(Array.from({ length: 21 }, (_, index) => {
      const close = 100 + index;
      return {
        tradeDate: `2026-07-${String(index + 1).padStart(2, '0')}`,
        open: close,
        high: close + 1,
        low: close - 1,
        close,
        volume: 1_000,
      };
    }), 15);

    expect(chart.xLabels[0].label).toMatch(/^Jul \d+$/);
    expect(new Set(chart.xLabels.map(label => label.label)).size).toBe(chart.xLabels.length);
  });

  it('uses close-only scaling until a visible overlay needs more space', () => {
    const bars: StockDailyBar[] = Array.from({ length: 60 }, (_, index) => {
      const close = index % 2 === 0 ? 40 : 42;
      return {
        tradeDate: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
        open: close,
        high: 80,
        low: 5,
        close,
        volume: 1_000,
      };
    });

    const closeOnly = buildTechnicalChart(bars, 30, new Set());
    const withBands = buildTechnicalChart(bars, 30, new Set(['bollinger']));
    const minimumClose = closeOnly.points.find(point => point.close === 40);
    const maximumClose = closeOnly.points.find(point => point.close === 42);

    expect(minimumClose?.closeY).toBe(closeOnly.baselineY);
    expect(maximumClose?.closeY).toBe(closeOnly.padding);
    expect(closeOnly.ticks.map(tick => tick.value)).toEqual([40, 40.5, 41, 41.5, 42]);
    expect(withBands.points.find(point => point.close === 40)?.closeY)
      .toBeLessThan(withBands.baselineY);
  });
});
