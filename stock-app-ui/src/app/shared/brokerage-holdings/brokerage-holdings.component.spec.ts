import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { BrokerageLedgerService } from '../../api/brokerage-ledger.service';
import { BrokerageHoldingsSnapshot } from '../../model/brokerage-holdings';
import { BrokerageHoldingsComponent } from './brokerage-holdings.component';

const LONG_NOTE = 'A deliberately long test note that should remain available while being visually truncated in the holdings table';

const SNAPSHOT: BrokerageHoldingsSnapshot = {
  holdings: [{
    enrichmentSymbol: 'DEMO', category: 'GROWTH', accountType: 'TRADING', industry: 'SOFTWARE',
    symbol: 'DEMO', costPrice: 100, qty: 10, initialInvestment: 1000, marketPrice: 90,
    currentValue: 900, pctOfTotal: 75, gainLossPct: -10, gainLoss: -100,
    gainLossSnapshots: { '2026-07-27': -8 }, note: LONG_NOTE,
    trend: {
      alert: true, peakPct: 5, peakAt: '2026-07-20T16:00:00Z', dropPct: 12,
      fromPct: 5, toPct: -10, alertAt: '2026-07-28T16:00:00Z', direction: 'LOSS',
    },
  }, {
    enrichmentSymbol: 'OTHER', category: 'DIV', accountType: 'IRA', industry: 'ENERGY',
    symbol: 'OTHER', costPrice: 50, qty: 6, initialInvestment: 300, marketPrice: 50,
    currentValue: 300, pctOfTotal: 25, gainLossPct: 0, gainLoss: 0,
    gainLossSnapshots: {}, note: '',
    trend: {
      alert: false, peakPct: 0, peakAt: '2026-07-28T16:00:00Z', dropPct: null,
      fromPct: null, toPct: null, alertAt: null, direction: 'GAIN',
    },
  }],
  totalInitial: 1300, totalCurrent: 1200, totalGainLoss: -100, totalGainLossPct: -7.69,
  byCategory: {}, byIndustry: {}, byAccountType: {}, topPositions: [],
  gainLossSnapshots: [{
    syncDate: '2026-07-27', retrievedAt: '2026-07-27T16:00:00Z', capturedAt: '2026-07-27T17:00:00Z',
  }],
  retrievedAt: '2026-07-28T16:00:00Z', source: 'TASTYTRADE',
};

describe('BrokerageHoldingsComponent', () => {
  it('renders normalized filters, snapshots, declining state, and editable notes', async () => {
    const api = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', [
      'getHoldings', 'captureHoldingSnapshot', 'updateHoldingEnrichment',
    ]);
    api.getHoldings.and.returnValue(of(SNAPSHOT));
    await TestBed.configureTestingModule({
      imports: [BrokerageHoldingsComponent],
      providers: [{ provide: BrokerageLedgerService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageHoldingsComponent);
    fixture.componentRef.setInput('portfolio', 'trading');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('Trading holdings');
    expect(text).toContain('All categories');
    expect(text).toContain('All accounts');
    expect(text).toContain('Declining only (1)');
    expect(text).toContain('Snapshot G/L %');
    expect(text).toContain('Copy Symbols');
    expect(text).toContain('G/L % as of Jul 27, 2026');

    const noteButton = fixture.nativeElement.querySelector('.note-button') as HTMLButtonElement;
    const noteStyles = getComputedStyle(noteButton);
    expect(noteStyles.overflow).toBe('hidden');
    expect(noteStyles.textOverflow).toBe('ellipsis');
    expect(noteStyles.whiteSpace).toBe('nowrap');
    expect(noteButton.textContent?.trim()).toBe(LONG_NOTE);

    noteButton.click();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Classify DEMO');
    expect((fixture.nativeElement.querySelector('.modal-note') as HTMLTextAreaElement).value)
      .toBe(LONG_NOTE);
  });

  it('hides category and account filters when each has only one choice', async () => {
    const api = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', ['getHoldings']);
    api.getHoldings.and.returnValue(of({ ...SNAPSHOT, holdings: [SNAPSHOT.holdings[0]] }));
    await TestBed.configureTestingModule({
      imports: [BrokerageHoldingsComponent],
      providers: [{ provide: BrokerageLedgerService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageHoldingsComponent);
    fixture.componentRef.setInput('portfolio', 'trading');
    fixture.detectChanges();

    const options = Array.from(
      fixture.nativeElement.querySelectorAll('.holdings-filters option') as NodeListOf<HTMLOptionElement>
    ).map(option => option.textContent?.trim());
    expect(options).not.toContain('All categories');
    expect(options).not.toContain('All accounts');
  });
});
