import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { StockService } from '../api/stock.service';
import { CapabilityService } from '../api/capability.service';
import { RetirementPortfolioData } from '../model/retirement';
import { RetirementOptionsData } from '../model/retirement-options';
import { RetirementPortfolioComponent } from './retirement-portfolio.component';

const PORTFOLIO: RetirementPortfolioData = {
  holdings: [],
  totalInitial: 0,
  totalCurrent: 0,
  totalGainLoss: 0,
  totalGainLossPct: 0,
  byCategory: {},
  byIndustry: {},
  byAccountType: {},
  topPositions: [],
  gainLossSnapshots: [{
    syncDate: '2026-07-27',
    retrievedAt: '2026-07-27T17:00:00+00:00',
    capturedAt: '2026-07-27T17:01:00+00:00',
  }],
  retrievedAt: '2026-07-27T17:00:00+00:00',
};

describe('RetirementPortfolioComponent G/L snapshots', () => {
  it('captures and reports replacement for the current sync date', () => {
    const stockService = jasmine.createSpyObj<StockService>('StockService', [
      'captureRetirementGainLossSnapshot',
    ]);
    stockService.captureRetirementGainLossSnapshot.and.returnValue(of({
      snapshot: {
        ...PORTFOLIO.gainLossSnapshots[0], replaced: true, snapshotCount: 3,
      },
      portfolio: PORTFOLIO,
    }));

    TestBed.configureTestingModule({
      providers: [
        { provide: StockService, useValue: stockService },
        { provide: CapabilityService, useValue: jasmine.createSpyObj('CapabilityService', ['get']) },
      ],
    });
    const component = TestBed.runInInjectionContext(() => new RetirementPortfolioComponent());

    component.captureGainLossSnapshot();

    expect(stockService.captureRetirementGainLossSnapshot).toHaveBeenCalledOnceWith();
    expect(component.data).toBe(PORTFOLIO);
    expect(component.snapshotting).toBeFalse();
    expect(component.syncMessage).toContain('Jul 27, 2026 replaced');
    expect(component.syncMessage).toContain('3 of 3 snapshot dates retained');
  });

  it('renders one dated column and its captured value', async () => {
    const displayPortfolio: RetirementPortfolioData = {
      ...PORTFOLIO,
      holdings: [{
        enrichmentSymbol: 'DEMO',
        category: 'GROWTH',
        accountType: 'PRE TAX',
        industry: 'SOFTWARE',
        symbol: 'DEMO',
        costPrice: 100,
        qty: 1,
        initialInvestment: 100,
        marketPrice: 87.66,
        currentValue: 87.66,
        pctOfTotal: 100,
        gainLossPct: -12.34,
        gainLoss: -12.34,
        gainLossSnapshots: { '2026-07-27': -12.34 },
        note: '',
        trend: {
          alert: false, peakPct: -12.34, peakAt: '2026-07-27T17:00:00+00:00',
          dropPct: null, fromPct: null, toPct: null, alertAt: null, direction: 'LOSS',
        },
      }],
      totalInitial: 100,
      totalCurrent: 87.66,
      totalGainLoss: -12.34,
      totalGainLossPct: -12.34,
      byCategory: {},
      byIndustry: {},
      byAccountType: {},
    };
    const stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getRetirementPortfolio', 'getRetirementOptions',
    ]);
    stockService.getRetirementPortfolio.and.returnValue(of(displayPortfolio));
    stockService.getRetirementOptions.and.returnValue(of({} as RetirementOptionsData));
    const capabilityService = jasmine.createSpyObj<CapabilityService>('CapabilityService', ['get']);
    capabilityService.get.and.returnValue(of(null));

    await TestBed.configureTestingModule({
      imports: [RetirementPortfolioComponent],
      providers: [
        { provide: StockService, useValue: stockService },
        { provide: CapabilityService, useValue: capabilityService },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(RetirementPortfolioComponent);
    fixture.detectChanges();

    const columns = Array.from(
      fixture.nativeElement.querySelectorAll('.snapshot-col') as NodeListOf<HTMLElement>
    ).map(element => element.textContent?.replace(/\s+/g, ' ').trim());
    expect(columns).toEqual([
      'G/L % as of Jul 27, 2026',
      '-12.3%',
    ]);

    const headers = Array.from(
      fixture.nativeElement.querySelectorAll('.holdings-table th') as NodeListOf<HTMLElement>
    ).map(header => header.textContent?.replace(/[▲▼]/g, '').replace(/\s+/g, ' ').trim());
    expect(headers.slice(8, 13)).toEqual([
      'Current', '% Port', 'G/L $', 'G/L %', 'G/L % as of Jul 27, 2026',
    ]);

    const toolbarButtons = Array.from(
      fixture.nativeElement.querySelectorAll('.filter-bar button') as NodeListOf<HTMLButtonElement>
    ).map(button => button.textContent?.replace(/\s+/g, ' ').trim());
    expect(toolbarButtons).toEqual(['Snapshot G/L %', 'Copy Symbols']);
  });
});
