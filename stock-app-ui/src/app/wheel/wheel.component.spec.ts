import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { StockService } from '../api/stock.service';
import { SymbolFilterService } from '../services/symbol-filter.service';
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
      rvPercentile252: 80,
      sampleCount: 1001,
      dataQuality: 'OK',
      rvWindowSessions: 21,
    },
  };
}

describe('WheelComponent', () => {
  let fixture: ComponentFixture<WheelComponent>;
  let stockService: jasmine.SpyObj<StockService>;

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
    stockService.getWheelCandidates.and.returnValue(of([candidate('DEMO')]));
    stockService.getWheelRvDetail.and.returnValue(of({
      rv_window_sessions: 21,
      lookback_sessions: 3,
      current_rv: 0.12,
      percentile: 2 / 3,
      price_as_of: '2026-07-28',
      observations: [
        { date: '2026-07-24', rv: 0.08 },
        { date: '2026-07-25', rv: 0.12 },
        { date: '2026-07-28', rv: 0.12 },
      ],
    }));
    stockService.runWheel.and.returnValue(of({ status: 'ok', durationMs: 1200 }));
    stockService.runChains.and.returnValue(of({ status: 'ok', durationMs: 800 }));

    await TestBed.configureTestingModule({
      imports: [WheelComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: stockService },
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
    expect(text()).toContain('Wheel scan failed');
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
});
