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
});
