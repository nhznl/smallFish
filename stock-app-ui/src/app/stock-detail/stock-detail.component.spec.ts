import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';

import { StockService } from '../api/stock.service';
import { StockAnalysis } from '../model/stock';
import { StockInfo } from '../model/stock-info';
import { StockDetailComponent } from './stock-detail.component';

function analysis(symbol: string, overrides: Partial<StockAnalysis> = {}): StockAnalysis {
  return {
    code: symbol,
    type: 'STOCK',
    lastTradeStats: {
      tradeDate: '2026-07-28',
      open: 10,
      high: 12,
      low: 9,
      close: 11,
      volume: 1_000_000,
    },
    recentWeeks: [],
    yearToDate: { gainLoss: 8, startDate: '2026-01-02', startPrice: 10 },
    midPointToDate: { gainLoss: 4, startDate: '2026-04-01', startPrice: 10.5 },
    fiveWeeksToDate: { gainLoss: 3, startDate: '2026-06-20', startPrice: 10.7 },
    fiveDaysToDate: { gainLoss: 1, startDate: '2026-07-21', startPrice: 10.9 },
    yearlySlopes: {},
    setup: 'BULLISH_CONTINUATION',
    setupScore: 70,
    ...overrides,
  };
}

function info(symbol: string, overrides: Partial<StockInfo> = {}): StockInfo {
  return {
    ticker: symbol,
    period: '1y',
    retrievedAt: '2026-07-29T12:00:00Z',
    company: {
      longName: `${symbol} Demo Corp`,
      shortName: symbol,
      sector: 'Technology',
      industry: 'Software',
    },
    price: {
      regularMarketPrice: 11,
      regularMarketPreviousClose: 10.5,
      marketCap: 1_000_000_000,
      fiftyTwoWeekHigh: 15,
      fiftyTwoWeekLow: 8,
    },
    valuation: {
      trailingPe: 20,
      forwardPe: 18,
      dividendYield: null,
    },
    news: [],
    ...overrides,
  };
}

describe('StockDetailComponent', () => {
  let fixture: ComponentFixture<StockDetailComponent>;
  let params$: BehaviorSubject<Record<string, string>>;
  let stockService: jasmine.SpyObj<StockService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(StockDetailComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    params$ = new BehaviorSubject<Record<string, string>>({ symbol: 'DEMO' });
    stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getStockAnalysis',
      'getStockInfo',
    ]);
    stockService.getStockAnalysis.and.callFake((symbol: string) => of(analysis(symbol)));
    stockService.getStockInfo.and.callFake((symbol: string) => of(info(symbol)));

    await TestBed.configureTestingModule({
      imports: [StockDetailComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: stockService },
        {
          provide: ActivatedRoute,
          useValue: {
            params: params$.asObservable(),
            snapshot: { params: params$.value },
          },
        },
      ],
    }).compileComponents();
  });

  it('loads analysis and shows the symbol with OHLCV from the fixture', () => {
    mount();
    expect(text()).toContain('DEMO');
    expect(text()).toContain('DEMO Demo Corp');
    expect(text()).toContain('Last cached session');
    expect(text()).toContain('11.00');
    expect(stockService.getStockAnalysis).toHaveBeenCalledWith('DEMO');
    expect(stockService.getStockInfo).toHaveBeenCalledWith('DEMO');
  });

  it('renders cached daily volume in the Price chart and hover readout', () => {
    stockService.getStockAnalysis.and.returnValue(of(analysis('DEMO', {
      dailyBars: Array.from({ length: 5 }, (_, index) => ({
        tradeDate: `2026-07-${String(24 + index).padStart(2, '0')}`,
        open: 10 + index,
        high: 11 + index,
        low: 9 + index,
        close: 10.5 + index,
        volume: (index + 1) * 1_000,
      })),
    })));
    mount();

    const chart = fixture.componentInstance.priceChart();
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.price-chart-svg .volume-bar').length).toBe(5);
    expect(Math.max(...chart.points.map(point => point.volumeHeight)))
      .toBe(chart.volumeBaselineY - chart.volumeTopY);

    const svg = (fixture.nativeElement as HTMLElement).querySelector<SVGSVGElement>('.price-chart-svg')!;
    spyOn(svg, 'getBoundingClientRect').and.returnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 960,
      bottom: 330,
      width: 960,
      height: 330,
      toJSON: () => ({})
    } as DOMRect);
    svg.dispatchEvent(new MouseEvent('mousemove', { clientX: 959, clientY: 100, bubbles: true }));
    fixture.detectChanges();
    expect(text()).toContain('Volume 5K');
  });

  it('surfaces a scanner-cache 404 without pretending the analysis succeeded', () => {
    stockService.getStockAnalysis.and.returnValue(
      throwError(() => ({ status: 404, message: 'Not Found' }))
    );
    stockService.getStockInfo.and.returnValue(of(info('PENY')));
    params$.next({ symbol: 'PENY' });
    mount();

    expect(text()).toContain('PENY');
    expect(text()).toContain('not in the scanner cache');
    expect(text()).not.toContain('Bullish');
  });

  it('keeps successful analysis when company info fails', () => {
    stockService.getStockInfo.and.returnValue(
      throwError(() => ({ message: 'Company info unavailable' }))
    );
    mount();

    expect(text()).toContain('DEMO');
    expect(text()).toContain('Bullish');
    expect(text()).toContain('70');
    expect(text()).toContain('Company info unavailable');
  });

  it('ignores a slower analysis response from a previous symbol', () => {
    const demoAnalysis$ = new Subject<StockAnalysis>();
    const otherAnalysis$ = new Subject<StockAnalysis>();
    stockService.getStockAnalysis.and.callFake((symbol: string) => {
      if (symbol === 'DEMO') {
        return demoAnalysis$.asObservable();
      }
      return otherAnalysis$.asObservable();
    });
    stockService.getStockInfo.and.callFake((symbol: string) => of(info(symbol)));

    mount();
    expect(stockService.getStockAnalysis).toHaveBeenCalledWith('DEMO');

    params$.next({ symbol: 'OTHER' });
    fixture.detectChanges();
    expect(stockService.getStockAnalysis).toHaveBeenCalledWith('OTHER');

    otherAnalysis$.next(analysis('OTHER', { setupScore: 55 }));
    otherAnalysis$.complete();
    fixture.detectChanges();
    expect(text()).toContain('OTHER');
    expect(text()).toContain('55');

    demoAnalysis$.next(analysis('DEMO', { setupScore: 99 }));
    demoAnalysis$.complete();
    fixture.detectChanges();
    expect(text()).toContain('OTHER');
    expect(text()).not.toContain('99');
    expect(fixture.componentInstance.symbol()).toBe('OTHER');
    expect(fixture.componentInstance.stock()?.code).toBe('OTHER');
  });
});
