import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { Capability, CapabilityService } from '../api/capability.service';
import { StockService } from '../api/stock.service';
import { MomentumStock } from '../model/stock';
import { MomentumScannerComponent } from './momentum-scanner.component';

function capability(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'core-data',
    label: 'Core market data',
    provides: 'scanner candidates',
    state: 'CONFIGURED',
    available: true,
    reason: 'Price cache is present.',
    action: '',
    provider: 'local',
    docs: '',
    requires: {},
    ...overrides,
  };
}

function stock(code: string, overrides: Partial<MomentumStock> = {}): MomentumStock {
  return {
    code,
    type: 'STOCK',
    lastTradeStats: { tradeDate: '2026-07-28', close: 50 },
    recentWeeks: [],
    yearToDate: { gainLoss: 10, startDate: '2026-01-02', startPrice: 45 },
    midPointToDate: { gainLoss: 5, startDate: '2026-04-01', startPrice: 47 },
    fiveWeeksToDate: { gainLoss: 4, startDate: '2026-06-20', startPrice: 48 },
    fiveDaysToDate: { gainLoss: 2, startDate: '2026-07-21', startPrice: 49 },
    setup: 'BULLISH_CONTINUATION',
    setupScore: 70,
    freshnessStatus: 'FRESH',
    averageDollarVolume20: 20_000_000,
    ...overrides,
  };
}

describe('MomentumScannerComponent', () => {
  let fixture: ComponentFixture<MomentumScannerComponent>;
  let stockService: jasmine.SpyObj<StockService>;
  let capabilityService: jasmine.SpyObj<CapabilityService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(MomentumScannerComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getMomentumStocks',
      'runEarningsScan',
    ]);
    capabilityService = jasmine.createSpyObj<CapabilityService>('CapabilityService', ['get']);
    capabilityService.get.and.returnValue(of(capability()));
    stockService.getMomentumStocks.and.returnValue(of([stock('DEMO')]));
    stockService.runEarningsScan.and.returnValue(of({
      status: 'ok',
      symbolsWithUpcomingEarnings: 1,
      scannerSymbols: 10,
      eventsCoverageEnd: '2026-10-01',
    }));

    await TestBed.configureTestingModule({
      imports: [MomentumScannerComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: stockService },
        { provide: CapabilityService, useValue: capabilityService },
      ],
    }).compileComponents();
  });

  it('shows a transport error instead of an empty filter message', () => {
    stockService.getMomentumStocks.and.returnValue(
      throwError(() => ({ message: 'Unable to load momentum candidates.' }))
    );
    mount();
    expect(text()).toContain('Unable to load momentum candidates.');
    expect(text()).not.toContain('No stocks match the selected setup and filters.');
  });

  it('shows the core-data capability state when the universe is empty and unavailable', () => {
    capabilityService.get.and.returnValue(of(capability({
      available: false,
      state: 'NOT_CONFIGURED',
      reason: 'No price cache has been downloaded yet.',
      action: './commands.sh bootstrap-data',
    })));
    stockService.getMomentumStocks.and.returnValue(of([]));
    mount();
    // Default Bullish filter + empty universe → empty table.
    expect(text()).toContain('No price cache has been downloaded yet.');
    expect(text()).toContain('./commands.sh bootstrap-data');
  });

  it('keeps existing rows and prior-calendar messaging when earnings refresh fails', () => {
    mount();
    expect(text()).toContain('DEMO');

    stockService.runEarningsScan.and.returnValue(of({
      status: 'error',
      message: 'Finnhub key missing',
      symbolsWithUpcomingEarnings: 1,
      scannerSymbols: 10,
    }));
    const beforeCalls = stockService.getMomentumStocks.calls.count();
    fixture.componentInstance.runEarningsScan();
    fixture.detectChanges();

    expect(fixture.componentInstance.earningsStatus).toBe('error');
    expect(fixture.componentInstance.earningsStatusClass()).toBe('job-error');
    expect(text()).toContain('Earnings refresh failed');
    expect(text()).toContain('Showing the previous calendar');
    expect(text()).toContain('DEMO');
    // Failed refresh must not reload-clear the table into a blank earnings state.
    expect(stockService.getMomentumStocks.calls.count()).toBe(beforeCalls);
  });

  it('reloads candidates after a successful earnings scan', () => {
    mount();
    const beforeCalls = stockService.getMomentumStocks.calls.count();
    fixture.componentInstance.runEarningsScan();
    fixture.detectChanges();
    expect(fixture.componentInstance.earningsStatus).toBe('ok');
    expect(stockService.getMomentumStocks.calls.count()).toBeGreaterThan(beforeCalls);
    expect(text()).toContain('Earnings calendar ready');
  });

  function crossover(age: number): MomentumStock['ema14Over20Cross'] {
    return { status: 'ACTIVE', sessionsAgo: age, asOfDate: '2026-07-28' };
  }

  it('places crossover age immediately after Setup Score and formats the inclusive limits', () => {
    stockService.getMomentumStocks.and.returnValue(of([
      stock('LATEST', { ema14Over20Cross: crossover(0) }),
      stock('ONE', { ema14Over20Cross: crossover(1) }),
      stock('LIMIT', { ema14Over20Cross: crossover(60) }),
      stock('NONE', { ema14Over20Cross: { status: 'NONE', sessionsAgo: null, asOfDate: '2026-07-28' } }),
      stock('UNKNOWN'),
    ]));
    mount();
    const headers = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('th'));
    const scoreIndex = headers.findIndex(header => header.textContent?.includes('Setup Score'));
    expect(headers[scoreIndex + 1].textContent).toContain('EMA14 ↑ EMA20');
    expect(text()).toContain('↑ Latest session');
    expect(text()).toContain('↑ 1 session ago');
    expect(text()).toContain('↑ 60 sessions ago');
    const labels = Array.from((fixture.nativeElement as HTMLElement)
      .querySelectorAll('td.mat-column-emaCross')).map(cell => cell.textContent?.trim());
    expect(labels).toEqual(['↑ Latest session', '↑ 60 sessions ago', 'No', '↑ 1 session ago', '—']);
  });

  it('sorts numerically newest first on header click and keeps No/unavailable last both ways', () => {
    stockService.getMomentumStocks.and.returnValue(of([
      stock('TEN', { ema14Over20Cross: crossover(10) }),
      stock('TWO', { ema14Over20Cross: crossover(2) }),
      stock('LATEST', { ema14Over20Cross: crossover(0) }),
      stock('LIMIT', { ema14Over20Cross: crossover(60) }),
      stock('ANONE', { ema14Over20Cross: { status: 'NONE', sessionsAgo: null, asOfDate: '2026-07-28' } }),
      stock('AUNKNOWN'),
    ]));
    mount();
    const header = (fixture.nativeElement as HTMLElement).querySelector('th.mat-column-emaCross') as HTMLElement;
    header.click();
    fixture.detectChanges();
    expect(header.getAttribute('aria-sort')).toBe('ascending');
    expect(fixture.componentInstance.visibleRows.map(row => row.code))
      .toEqual(['LATEST', 'TWO', 'TEN', 'LIMIT', 'ANONE', 'AUNKNOWN']);
    header.click();
    fixture.detectChanges();
    expect(header.getAttribute('aria-sort')).toBe('descending');
    expect(fixture.componentInstance.visibleRows.map(row => row.code))
      .toEqual(['LIMIT', 'TEN', 'TWO', 'LATEST', 'ANONE', 'AUNKNOWN']);
    expect(fixture.componentInstance.visibleRows.every(row => row.setupScore === 70)).toBeTrue();
  });

  it('does not render stale, malformed or missing evidence as No or a fresh crossing', () => {
    mount();
    const component = fixture.componentInstance;
    expect(component.emaCrossLabel(stock('MISSING'))).toBe('—');
    for (const age of [-1, 1.5, 61, NaN]) {
      expect(component.emaCrossLabel(stock('BAD', { ema14Over20Cross: crossover(age) }))).toBe('—');
    }
    const stale = stock('STALE', { freshnessStatus: 'STALE', ema14Over20Cross: crossover(0) });
    expect(component.emaCrossLabel(stale)).toBe('—');
    expect(component.emaCrossTooltip(stale)).toContain('unavailable');
    expect(component.emaCrossTooltip(stock('FRESH', { ema14Over20Cross: crossover(0) })))
      .toContain('2026-07-28');
  });

  it('explains the strict price and dollar-gap gates and original crossover age', () => {
    mount();
    const component = fixture.componentInstance;
    const active = stock('ACTIVE', { ema14Over20Cross: crossover(2) });
    const pending = stock('WAITING', {
      ema14Over20Cross: { status: 'NONE', sessionsAgo: null, asOfDate: '2026-07-28' },
    });
    expect(component.emaCrossLabel(active)).toBe('↑ 2 sessions ago');
    expect(component.emaCrossTooltip(active)).toContain('close is above both EMAs');
    expect(component.emaCrossTooltip(active)).toContain('more than $1');
    expect(component.emaCrossTooltip(active)).toContain('original upward crossover');
    expect(component.emaCrossLabel(pending)).toBe('No');
    expect(component.emaCrossTooltip(pending)).toContain('Waiting does not reset');
  });
});
