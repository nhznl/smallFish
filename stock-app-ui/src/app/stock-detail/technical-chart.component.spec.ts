import { ComponentFixture, TestBed } from '@angular/core/testing';

import { StockDailyBar } from '../model/stock';
import { TechnicalChartComponent } from './technical-chart.component';

describe('TechnicalChartComponent', () => {
  let fixture: ComponentFixture<TechnicalChartComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [TechnicalChartComponent] }).compileComponents();
    fixture = TestBed.createComponent(TechnicalChartComponent);
    fixture.componentRef.setInput('symbol', 'DEMO');
    fixture.componentRef.setInput('bars', bars(300));
    fixture.detectChanges();
  });

  it('shows only EMA periods with 14 and 20 selected by default', () => {
    const element = fixture.nativeElement as HTMLElement;
    const labels = Array.from(element.querySelectorAll<HTMLButtonElement>('.overlay-group button'))
      .map(button => button.textContent?.trim());

    expect(labels).toEqual(['10', '14', '20', '50']);
    expect(element.textContent).not.toContain('SMA · solid');
    expect(element.textContent).toContain('Bollinger 20, 2');
    expect(element.textContent).toContain('S / R zones');
    expect(fixture.componentInstance.isEnabled('ema10')).toBeFalse();
    expect(fixture.componentInstance.isEnabled('ema14')).toBeTrue();
    expect(fixture.componentInstance.isEnabled('ema20')).toBeTrue();
    expect(fixture.componentInstance.isEnabled('ema50')).toBeFalse();
  });

  it('toggles an overlay with an accessible pressed state', () => {
    const button = (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('.overlay-group:first-child button');
    expect(button?.getAttribute('aria-pressed')).toBe('false');

    button?.click();
    fixture.detectChanges();

    expect(button?.getAttribute('aria-pressed')).toBe('true');
    expect(fixture.componentInstance.isEnabled('ema10')).toBeTrue();
  });

  it('offers every technical range and plots its requested session count', () => {
    const element = fixture.nativeElement as HTMLElement;
    const rangeButtons = Array.from(element.querySelectorAll<HTMLButtonElement>('.range-tabs button'));
    expect(rangeButtons.map(button => button.textContent?.trim()))
      .toEqual(['15D', '1M', '3M', '6M', '1Y']);

    const expectedSessions = new Map([
      ['15D', 15],
      ['1M', 21],
      ['3M', 63],
      ['6M', 126],
      ['1Y', 252],
    ]);
    for (const button of rangeButtons) {
      button.click();
      fixture.detectChanges();
      expect(fixture.componentInstance.chart().points.length)
        .withContext(button.textContent?.trim())
        .toBe(expectedSessions.get(button.textContent?.trim() ?? '')!);
      expect(button.getAttribute('aria-pressed')).toBe('true');
      expect(fixture.componentInstance.chart().macdPanel.bars.length)
        .toBe(expectedSessions.get(button.textContent?.trim() ?? '')!);
    }
  });

  it('shows MACD by default and toggles it without changing the price chart', () => {
    const element = fixture.nativeElement as HTMLElement;
    const pricePoints = fixture.componentInstance.chart().closePoints;
    const ticks = fixture.componentInstance.chart().ticks;
    expect(element.querySelector('.macd-svg')).not.toBeNull();
    expect(element.querySelectorAll('.macd-line').length).toBe(1);
    expect(element.querySelectorAll('.macd-signal').length).toBe(1);
    expect(element.querySelector('.macd-bar.above-zero')).not.toBeNull();
    expect(element.querySelector('.macd-bar.below-zero')).not.toBeNull();
    const toggle = element.querySelector<HTMLButtonElement>('.macd-toggle')!;
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(element.querySelector('.macd-svg')).toBeNull();
    expect(fixture.componentInstance.chart().closePoints).toBe(pricePoints);
    expect(fixture.componentInstance.chart().ticks).toEqual(ticks);
  });

  it('synchronizes hover from the MACD panel with price and dated values', () => {
    const element = fixture.nativeElement as HTMLElement;
    const svg = element.querySelector<SVGSVGElement>('.macd-svg')!;
    spyOn(svg, 'getBoundingClientRect').and.returnValue({ left: 0, width: 960 } as DOMRect);
    const point = fixture.componentInstance.chart().points[20];
    svg.dispatchEvent(new MouseEvent('mousemove', { clientX: point.x + 60 }));
    fixture.detectChanges();
    expect(fixture.componentInstance.hoveredPoint()?.tradeDate).toBe(point.tradeDate);
    expect(element.querySelector('.macd-readout')?.textContent).toContain(point.fullLabel);
    expect(element.querySelector('.macd-readout')?.textContent)
      .toContain(fixture.componentInstance.formatMacd(point.histogram));
    expect(element.querySelectorAll('.hover-line').length).toBe(2);
    svg.dispatchEvent(new MouseEvent('mouseleave'));
    fixture.detectChanges();
    expect(fixture.componentInstance.hoveredPoint()).toBeNull();
    expect(fixture.componentInstance.macdReadout()?.tradeDate)
      .toBe(fixture.componentInstance.chart().points.at(-1)?.tradeDate);
  });

  it('explains unavailable MACD signal history instead of fabricating zero', () => {
    fixture.componentRef.setInput('bars', bars(30));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('at least 34 cached sessions');
    expect(element.querySelector('.macd-readout')?.textContent).toContain('Signal —');
    expect(element.querySelectorAll('.macd-bar').length).toBe(0);
    expect(fixture.componentInstance.formatMacd(0.0001)).not.toBe('0.00');
  });
});

function bars(count: number): StockDailyBar[] {
  return Array.from({ length: count }, (_, index) => {
    const close = 100 + Math.sin(index / 5) * 4 + index * 0.1;
    return {
      tradeDate: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
      open: close - 0.4,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1_000 + index,
    };
  });
}
