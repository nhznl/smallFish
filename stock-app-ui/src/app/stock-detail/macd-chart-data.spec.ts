import { buildMacdPanel, calculateMacd } from './macd-chart-data';

describe('MACD chart data', () => {
  it('leaves EMA and signal warm-up unavailable, not zero', () => {
    const values = calculateMacd(Array.from({ length: 40 }, (_, index) => 100 + index));
    expect(values.slice(0, 25).every(value => value.macd === null)).toBeTrue();
    expect(values[25].macd).toBeCloseTo(7, 10);
    expect(values[32].signal).toBeNull();
    expect(values[32].histogram).toBeNull();
    expect(values[33].signal).toBeCloseTo(7, 10);
    expect(values[33].histogram).toBeCloseTo(0, 10);
  });

  it('matches the backend float32 and SMA-seeded MACD on synthetic non-linear prices', () => {
    const closes = Array.from({ length: 60 }, (_, i) => 100 + ((i * 7) % 17) * .37 + (i % 3) * .011);
    const values = calculateMacd(closes);
    // Generated with trend_engine.calc_ema/calc_macd/calc_macd_signal/calc_macd_hist.
    expect(values[25].macd).toBeCloseTo(0.030995506394248196, 11);
    expect(values[32].macd).toBeCloseTo(0.05856898857857118, 11);
    expect(values[33].macd).toBeCloseTo(0.10479449919172623, 11);
    expect(values[33].signal).toBeCloseTo(0.10449029662697423, 11);
    expect(values[33].histogram).toBeCloseTo(0.0003042025647519986, 11);
    expect(values[34].histogram).toBeCloseTo(-0.2071618406726063, 11);
    expect(values[59].macd).toBeCloseTo(-0.007376723910113014, 11);
    expect(values[59].signal).toBeCloseTo(-0.025585367976712292, 11);
    expect(values[59].histogram).toBeCloseTo(0.018208644066599278, 11);
  });

  it('handles flat, empty, short and invalid histories without NaN paths', () => {
    expect(calculateMacd([])).toEqual([]);
    expect(calculateMacd([100, 101]).every(point => point.macd === null)).toBeTrue();
    const flat = calculateMacd(Array(50).fill(100));
    expect(flat[49]).toEqual({ macd: 0, signal: 0, histogram: 0 });
    for (const invalid of [NaN, Infinity, -1, 0]) {
      expect(calculateMacd([...Array(50).fill(100), invalid])
        .every(point => point.macd === null && point.signal === null)).toBeTrue();
    }
    const panel = buildMacdPanel(flat.map((point, i) => ({ ...point, x: 28 + i * 10 })));
    expect(panel.macdLine).not.toContain('NaN');
    expect(panel.bars.every(bar => bar.height === 0)).toBeTrue();
    expect(buildMacdPanel([]).hasSignal).toBeFalse();
  });

  it('keeps zero visible and plots signed histogram bars on the correct side', () => {
    const panel = buildMacdPanel([
      { x: 28, macd: 2, signal: 1, histogram: 1 },
      { x: 80, macd: -2, signal: -1, histogram: -1 },
    ]);
    expect(panel.ticks.some(tick => tick.value === 0 && tick.y === panel.zeroY)).toBeTrue();
    expect(panel.bars[0].y).toBeLessThan(panel.zeroY);
    expect(panel.bars[0].y + panel.bars[0].height).toBeCloseTo(panel.zeroY, 8);
    expect(panel.bars[1].y).toBe(panel.zeroY);
    expect(panel.bars[1].height).toBeGreaterThan(0);
    expect(panel.bars[0].x + panel.bars[0].width / 2).toBe(28);
    expect(panel.bars[1].x + panel.bars[1].width / 2).toBe(80);
  });
});
