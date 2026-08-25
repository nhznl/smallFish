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
    }
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
