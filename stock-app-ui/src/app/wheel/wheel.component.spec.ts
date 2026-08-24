import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { StockService } from '../api/stock.service';
import { BrokerageService } from '../api/brokerage.service';
import { SymbolFilterService } from '../services/symbol-filter.service';
import { HoldingsResponse } from '../model/brokerage';
import { WheelCandidate } from '../model/wheel-candidate';
import { WheelComponent } from './wheel.component';
import { OptionQuotesTabComponent } from './option-quotes-tab.component';
import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-option-quotes-tab',
  standalone: true,
  template: '',
})
class StubOptionQuotesTabComponent {
  @Input() refreshToken = 0;
}

function candidate(symbol: string): WheelCandidate {
  return {
    type: 'STOCK',
    trendAvailable: true,
    trendDirection: 'BULLISH',
    wheel: {
      schemaVersion: 1,
      runMode: 'full',
      symbol,
      asOf: '2026-07-28',
      horizonDte: 37,
      horizonSessions: 26,
      priceAsOf: '2026-07-28',
      lastClose: 50,
      rvRank252: 0.5,
      rvPercentile252: 80,
      atr14Pct: 0.04,
      sampleCount: 1001,
      dataQuality: 'OK',
      rvWindowSessions: 21,
    },
  };
}

function holdingsResponse(symbols: string[] = []): HoldingsResponse {
  return {
    availability: { status: 'AVAILABLE', reasons: [] },
    items: symbols.map(symbol => ({ symbol })),
  } as unknown as HoldingsResponse;
}

describe('WheelComponent', () => {
  let fixture: ComponentFixture<WheelComponent>;
  let stockService: jasmine.SpyObj<StockService>;
  let brokerageService: jasmine.SpyObj<BrokerageService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(WheelComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getWheelCandidates',
      'getWheelRvDetail',
      'runWheel',
      'runChains',
    ]);
    brokerageService = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['getHoldings']);
    stockService.getWheelCandidates.and.returnValue(of([candidate('DEMO')]));
    stockService.getWheelRvDetail.and.returnValue(of({
      rv_window_sessions: 21,
      lookback_sessions: 3,
      current_rv: 0.12,
      percentile: 2 / 3,
      rank: 1,
      low_rv: 0.08,
      high_rv: 0.12,
      price_as_of: '2026-07-28',
      observations: [
        { date: '2026-07-24', rv: 0.08 },
        { date: '2026-07-25', rv: 0.12 },
        { date: '2026-07-28', rv: 0.12 },
      ],
    }));
    stockService.runWheel.and.returnValue(of({ status: 'ok', durationMs: 1200 }));
    stockService.runChains.and.returnValue(of({ status: 'ok', durationMs: 800 }));
    brokerageService.getHoldings.and.returnValue(of(holdingsResponse()));

    await TestBed.configureTestingModule({
      imports: [WheelComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: stockService },
        { provide: BrokerageService, useValue: brokerageService },
        SymbolFilterService,
      ],
    })
      .overrideComponent(WheelComponent, {
        remove: { imports: [OptionQuotesTabComponent] },
        add: { imports: [StubOptionQuotesTabComponent] },
      })
      .compileComponents();
  });

  it('renders an empty-state when the service returns no candidates', () => {
    stockService.getWheelCandidates.and.returnValue(of([]));
    mount();
    expect(text()).toContain('No wheel candidates');
    expect(text()).not.toContain('Could not load wheel candidates');
  });

  it('opens the RV evidence drawer from the clickable percentile', () => {
    mount();
    (fixture.nativeElement.querySelector('.rv-detail-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(stockService.getWheelRvDetail).toHaveBeenCalledWith('DEMO');
    expect(fixture.componentInstance.rvDetailSymbol).toBe('DEMO');
    expect(fixture.componentInstance.rvDetail?.current_rv).toBe(0.12);
    expect(fixture.componentInstance.rvObservationsAtOrBelowCurrent()).toBe(3);
    expect(text()).toContain('Trailing low RV');
    expect(text()).toContain('RV Rank');
    expect(text()).toContain('Trailing low');
    expect(text()).toContain('Trailing high');
  });

  it('places ATR14% immediately after the 1σ move', () => {
    mount();
    const columns = fixture.componentInstance.displayedColumns;
    expect(columns.indexOf('atr14Pct')).toBe(columns.indexOf('sigmaMovePct') + 1);
    expect(text()).toContain('ATR14%');
    expect(text()).toContain('4.0%');
  });

  it('places RV Rank immediately before the clickable RV percentile', () => {
    mount();
    const columns = fixture.componentInstance.displayedColumns;
    expect(columns.indexOf('rvRank252')).toBe(columns.indexOf('rvPercentile252') - 1);
    expect(text()).toContain('RV Rank');
    expect(text()).toContain('50.00');
  });

  it('keeps only rows whose overlapping sample count exceeds the configured minimum', () => {
    const lowSample = candidate('LOW');
    lowSample.wheel.sampleCount = 1000;
    stockService.getWheelCandidates.and.returnValue(of([candidate('DEMO'), lowSample]));
    mount();

    expect(fixture.componentInstance.dataSource.data.map(row => row.wheel.symbol)).toEqual(['DEMO']);

    fixture.componentInstance.minSamples = 0;
    fixture.componentInstance.applyFilters();
    expect(fixture.componentInstance.dataSource.data.map(row => row.wheel.symbol)).toEqual(['DEMO', 'LOW']);
  });

  it('filters to the union of Trading and Retirement holdings', () => {
    stockService.getWheelCandidates.and.returnValue(of([candidate('TRADING'), candidate('RETIREMENT'), candidate('OTHER')]));
    brokerageService.getHoldings.and.callFake(id =>
      of(holdingsResponse([id === 'tastytrade' ? 'TRADING' : 'RETIREMENT']))
    );
    mount();

    fixture.componentInstance.toggleHoldingsFilter();

    expect(brokerageService.getHoldings).toHaveBeenCalledWith('tastytrade');
    expect(brokerageService.getHoldings).toHaveBeenCalledWith('fidelity');
    expect(fixture.componentInstance.holdingsOnly).toBeTrue();
    expect(fixture.componentInstance.dataSource.data.map(row => row.wheel.symbol))
      .toEqual(['TRADING', 'RETIREMENT']);

    fixture.componentInstance.toggleHoldingsFilter();
    expect(fixture.componentInstance.dataSource.data.map(row => row.wheel.symbol))
      .toEqual(['TRADING', 'RETIREMENT', 'OTHER']);
  });

  it('leaves the table unchanged when either holdings source cannot be loaded', () => {
    brokerageService.getHoldings.and.returnValue(throwError(() => new Error('Unavailable')));
    mount();

    fixture.componentInstance.toggleHoldingsFilter();
    fixture.detectChanges();

    expect(fixture.componentInstance.holdingsOnly).toBeFalse();
    expect(fixture.componentInstance.dataSource.data.map(row => row.wheel.symbol)).toEqual(['DEMO']);
    expect(text()).toContain('Could not load both Trading and Retirement holdings');
  });

  it('shows a transport error instead of the empty-state when the load fails', () => {
    stockService.getWheelCandidates.and.returnValue(
      throwError(() => new Error('Network down'))
    );
    mount();
    expect(text()).toContain('Could not load wheel candidates');
    expect(text()).not.toContain('No wheel candidates');
    expect(fixture.componentInstance.loadError).toBeTrue();
  });

  it('shows job-error messaging when runWheel fails without clearing the table', () => {
    mount();
    expect(text()).toContain('DEMO');

    stockService.runWheel.and.returnValue(of({
      status: 'error',
      message: 'Wheel scan failed hard',
    }));
    fixture.componentInstance.runWheel();
    fixture.detectChanges();

    expect(fixture.componentInstance.wheelStatus).toBe('error');
    expect(fixture.componentInstance.jobStatusClass(
      fixture.componentInstance.wheelStatus
    )).toBe('job-error');
    expect(text()).toContain('Options stats computation failed');
    expect(text()).toContain('DEMO');
  });

  it('reloads candidates after a successful runWheel', () => {
    mount();
    const before = stockService.getWheelCandidates.calls.count();
    fixture.componentInstance.runWheel();
    fixture.detectChanges();
    expect(fixture.componentInstance.wheelStatus).toBe('ok');
    expect(stockService.getWheelCandidates.calls.count()).toBeGreaterThan(before);
  });

  it('shows job-error messaging when runChains fails', () => {
    mount();
    stockService.runChains.and.returnValue(of({
      status: 'error',
      message: 'Invalid collection scope',
    }));
    fixture.componentInstance.runChains();
    fixture.detectChanges();
    expect(fixture.componentInstance.chainsStatus).toBe('error');
    expect(text()).toContain('Invalid collection scope');
  });

  it('always collects the symbols currently in view', () => {
    mount();

    fixture.componentInstance.runChains();

    expect(stockService.runChains).toHaveBeenCalledWith({
      horizonDte: 37,
      symbols: ['DEMO'],
      minOtmPct: 5,
    });
    expect(text()).not.toContain('Scope collection to this view');
  });

  it('allows quote collection at every Wheel horizon', () => {
    mount();
    for (const horizon of fixture.componentInstance.horizons) {
      fixture.componentInstance.horizon = horizon;
      expect(fixture.componentInstance.scopeBlockReason()).toBe('');
    }
  });
});
