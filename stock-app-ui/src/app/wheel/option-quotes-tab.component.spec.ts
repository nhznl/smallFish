import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, Subject, throwError } from 'rxjs';

import { BrokerageService } from '../api/brokerage.service';
import { StockService } from '../api/stock.service';
import { HoldingItem, HoldingsResponse } from '../model/brokerage';
import { OptionQuoteRow, OptionQuoteSnapshot } from '../model/option-quotes';
import { StockInfo } from '../model/stock-info';
import { OptionQuotesTabComponent } from './option-quotes-tab.component';

function quote(overrides: Partial<OptionQuoteRow> = {}): OptionQuoteRow {
  return {
    contractId: 'DEMO-P-90', providerContractSymbol: 'DEMO-P-90', symbol: 'DEMO',
    contractQuality: 'OK', asOf: '2026-07-16', requestedDte: 37,
    expiry: '2026-08-21', actualDte: 36, dteDeviation: -1, side: 'PUT', strike: 90,
    moneyness: 'OTM', analysisView: 'ENTRY', strategyRole: 'CSP_ENTRY',
    bid: 1.8, ask: 2, mid: 1.9, openInterest: 200, volume: 10, spreadPct: 0.105,
    quoteSource: 'TASTYTRADE_DXLINK', quoteProviderStatus: 'RECEIVED',
    bidTimestamp: null, askTimestamp: null, quoteEventTimestamp: null,
    retrievedAt: '2026-07-16T14:00:00Z', marketSession: 'RTH', quoteAgeSeconds: null,
    quoteQuality: 'UNKNOWN', quoteQualityReasons: 'quote_timestamp_unavailable',
    liquidityOk: false, gateReason: 'spread_above_max', entryEligible: false,
    entryReason: 'quote_not_ok', ...overrides,
  };
}

function snapshot(rows: OptionQuoteRow[]): OptionQuoteSnapshot {
  return {
    available: true, runId: '20260716T140000000000Z', asOf: '2026-07-16',
    quoteProvider: { source: 'TASTYTRADE_DXLINK', status: 'COMPLETE', retrieved_at: '2026-07-16T14:00:00Z' },
    summary: {
      contracts: rows.length, symbols: new Set(rows.map(row => row.symbol)).size,
      entryViewContracts: rows.filter(row => row.analysisView === 'ENTRY').length,
      rollExitViewContracts: rows.filter(row => row.analysisView === 'ROLL_EXIT').length,
      entryEligibleContracts: rows.filter(row => row.entryEligible).length,
      quoteQualityCounts: rows.reduce((counts, row) => {
        const quality = row.quoteQuality || 'UNKNOWN';
        counts[quality] = (counts[quality] ?? 0) + 1;
        return counts;
      }, {} as Record<string, number>),
      quoteSourceCounts: { TASTYTRADE_DXLINK: rows.length },
      providerStatusCounts: { RECEIVED: rows.length },
    },
    rows,
  };
}

function stockInfo(price: number | null = 101.25): StockInfo {
  return {
    ticker: 'DEMO', period: 'info', retrievedAt: '2026-07-16T14:05:00Z',
    company: {}, price: { regularMarketPrice: price, currency: 'USD' }, valuation: {}, news: [],
  };
}

/** The option quote header only consumes these projection fields. */
function holding(overrides: Partial<HoldingItem> = {}): HoldingItem {
  return {
    symbol: 'DEMO', instrument: 'EQUITY', side: 'LONG', state: 'OPEN', quantity: 10,
    cost_basis: 800, ...overrides,
  } as HoldingItem;
}

function holdingsResponse(
  items: HoldingItem[] = [], availability: 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE' = 'AVAILABLE',
): HoldingsResponse {
  return {
    availability: { status: availability, reasons: [] },
    as_of: { positions: '2026-07-16T14:02:00Z', activity: null, market: null },
    items,
  } as unknown as HoldingsResponse;
}

describe('OptionQuotesTabComponent', () => {
  let fixture: ComponentFixture<OptionQuotesTabComponent>;
  let service: jasmine.SpyObj<StockService>;
  let brokerageService: jasmine.SpyObj<BrokerageService>;

  function mount(rows: OptionQuoteRow[]): HTMLElement {
    service.getOptionQuotes.and.returnValue(of(snapshot(rows)));
    service.getOptionQuoteReports.and.returnValue(of([snapshot(rows)]));
    fixture = TestBed.createComponent(OptionQuotesTabComponent);
    fixture.componentRef.setInput('refreshToken', 0);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  beforeEach(async () => {
    service = jasmine.createSpyObj<StockService>('StockService', [
      'getOptionQuotes', 'getOptionQuoteReports', 'getStockInfo', 'runChains',
    ]);
    brokerageService = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['getHoldings']);
    service.getStockInfo.and.returnValue(of(stockInfo()));
    brokerageService.getHoldings.and.returnValue(of(holdingsResponse()));
    await TestBed.configureTestingModule({
      imports: [OptionQuotesTabComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: service },
        { provide: BrokerageService, useValue: brokerageService },
      ],
    }).compileComponents();
  });

  it('groups by symbol and actual expiry with independent, numerically sorted strikes', () => {
    const rows = [
      quote({ symbol: 'ZETA' }),
      quote({ expiry: '2026-09-18', actualDte: 64, requestedDte: 65 }),
      quote({ strike: 100, contractId: 'DEMO-P-100' }),
      quote({ side: 'CALL', strike: 110, contractId: 'DEMO-C-110' }),
      quote({ strike: 9, contractId: 'DEMO-P-9' }),
      quote({ strike: 100, contractId: 'DEMO-P-100', requestedDte: 30 }),
    ];
    const original = JSON.stringify(rows);
    const element = mount(rows);
    const groups = fixture.componentInstance.groupRows(rows);

    expect(groups.map(group => group.symbol)).toEqual(['DEMO', 'ZETA']);
    expect(groups[0].expiries.map(group => group.expiry)).toEqual(['2026-08-21', '2026-09-18']);
    const expiry = groups[0].expiries[0];
    expect(expiry.sides[0].rows.map(row => row.strike)).toEqual([9, 100, 100]);
    expect(expiry.sides[1].rows.map(row => row.strike)).toEqual([110]);
    expect(expiry.requestedDtes).toBe('30, 37');
    expect(element.querySelectorAll('tbody tr').length).toBe(rows.length);
    expect(element.textContent).toContain('Target 30 DTE');
    expect(element.textContent).toContain('Target 37 DTE');
    expect(JSON.stringify(rows)).toBe(original);
  });

  it('keeps only strike/view, bid/ask, OI/volume, and spread in both tables', () => {
    const element = mount([quote(), quote({
      side: 'CALL', strike: 110, contractId: 'DEMO-C-110', analysisView: 'ROLL_EXIT',
    })]);
    for (const table of Array.from(element.querySelectorAll('table'))) {
      const headers = Array.from(table.querySelectorAll('thead th')).map(node => node.textContent?.trim());
      expect(headers).toEqual(['Strike / view', 'Bid / Ask', 'IV', 'OI / volume', 'Spread']);
      expect(table.textContent).not.toMatch(/Timestamp|Liquidity|Reasons|Quote quality|Entry status|Not eligible/i);
    }
    const put = element.querySelector('.quote-side')!;
    expect(put.textContent).toContain('$1.80 / $2.00');
    expect(put.textContent).toContain('200 / 10');
    expect(put.textContent).toContain('10.5%');
    expect(element.textContent).toContain('Roll/exit');
    expect(element.querySelector('.archive-meta')?.textContent).toContain('2026-07-16T14:00:00Z');
    expect(element.querySelector('.symbol-heading a')?.getAttribute('href')).toBe('/stockDetail/DEMO');
    expect(element.textContent).not.toMatch(/ENTRY view|ROLL_EXIT view|Entry eligible|does not mean it is trade-eligible/i);
  });

  it('shows holdings and trend filters in both new report labels and scoped collection detail', () => {
    const report = snapshot([quote()]);
    report.reportName = '2026-08-28__horizon37_cushion5_filterholdings(T)_etfOnly(F)_rvRank40-80_trendBEARISH';
    report.collectionScope = { scoped: true, requestedDtes: [37], symbolCount: 1, minOtmPct: 0.05 };
    service.getOptionQuotes.and.returnValue(of(report));
    service.getOptionQuoteReports.and.returnValue(of([report]));
    fixture = TestBed.createComponent(OptionQuotesTabComponent);
    fixture.componentRef.setInput('refreshToken', 0);
    fixture.detectChanges();

    const content = fixture.nativeElement.textContent;
    expect(content).toContain('Holdings: Yes');
    expect(content).toContain('Trend Bearish');
    expect(content).toContain('Trend: Bearish');
    expect(content).not.toContain('(configured:');
  });

  it('keeps a missing call side explicitly empty and never fabricates a paired contract', () => {
    const element = mount([quote()]);
    const sides = element.querySelectorAll('.quote-side');
    expect(sides.length).toBe(2);
    expect(sides[0].querySelectorAll('tbody tr').length).toBe(1);
    expect(sides[1].querySelector('table')).toBeNull();
    expect(sides[1].textContent).toContain('No calls match the current filters in this archive.');
  });

  it('preserves incomplete rows and distinguishes missing numbers from zero', () => {
    const element = mount([quote({
      symbol: null, expiry: null, side: null, strike: null, bid: 0, ask: null,
      openInterest: 0, volume: null, spreadPct: null, entryEligible: null,
      actualDte: null, requestedDte: null,
    })]);
    expect(element.textContent).toContain('Unknown symbol');
    expect(element.textContent).toContain('Expiry unavailable');
    expect(element.textContent).toContain('Other / unknown side');
    const row = element.querySelector('tbody tr')!;
    expect(row.textContent).toContain('$0.00 / —');
    expect(row.textContent).toContain('0 / —');
    expect(row.querySelector('td:last-child')?.textContent?.trim()).toBe('—');
    expect(element.querySelectorAll('tbody tr').length).toBe(1);
  });

  it('applies the symbol filter before grouping without fetching again', async () => {
    const element = mount([
      quote(),
      quote({ side: 'CALL', analysisView: 'ROLL_EXIT', quoteQuality: 'INVALID' }),
      quote({ symbol: 'ZETA', quoteQuality: 'INVALID' }),
    ]);
    await fixture.whenStable();
    const input = element.querySelector('input')!;
    input.value = ' demo ';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(element.querySelectorAll('.quote-symbol').length).toBe(1);
    expect(element.querySelectorAll('tbody tr').length).toBe(2);
    expect(element.querySelector('.result-count')?.textContent).toContain('2 contracts · 1 symbol shown');
    expect(element.querySelectorAll('select').length).toBe(0);
    expect(service.getOptionQuotes).toHaveBeenCalledTimes(1);
    expect(service.getStockInfo).toHaveBeenCalledTimes(2);
    expect(service.runChains).not.toHaveBeenCalled();

    input.value = 'NOT_IN_ARCHIVE';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(element.querySelectorAll('.quote-symbol').length).toBe(0);
    expect(element.textContent).toContain('No archived contracts match these filters.');
    expect(service.getStockInfo).toHaveBeenCalledTimes(2);
  });

  it('shows a separately fetched market price once per symbol instead of the archived spot', () => {
    const archived = { ...quote(), spot: 80 };
    const element = mount([
      archived,
      quote({ side: 'CALL', strike: 110 }),
      quote({ expiry: '2026-09-18' }),
    ]);
    expect(element.querySelector('.symbol-heading .symbol-price')?.textContent).toContain('$101.25');
    expect(element.querySelector('.symbol-heading')?.textContent).not.toContain('$80.00');
    expect(service.getStockInfo).toHaveBeenCalledOnceWith('DEMO', { refresh: true });
    expect(fixture.componentInstance.marketPriceTooltip(fixture.componentInstance.marketPrice('DEMO')))
      .toContain('Retrieved: 2026-07-16T14:05:00Z');
  });

  it('loads prices independently and shows unavailable without hiding contract rows', () => {
    const pending = new Subject<StockInfo>();
    service.getStockInfo.and.callFake(symbol => symbol === 'DEMO'
      ? pending : throwError(() => new Error('Provider unavailable')));
    const element = mount([quote(), quote({ symbol: 'ZETA' })]);
    expect(element.querySelector('.symbol-price')?.textContent).toContain('Loading…');
    expect(element.querySelectorAll('tbody tr').length).toBe(2);
    expect(fixture.componentInstance.marketPrice('ZETA')?.price).toBeNull();
    expect(fixture.componentInstance.marketPrice('ZETA')?.loading).toBeFalse();

    pending.next(stockInfo(null));
    pending.complete();
    fixture.detectChanges();
    expect(element.querySelector('.symbol-price')?.textContent).toContain('—');
    expect(element.querySelectorAll('tbody tr').length).toBe(2);
    expect(service.runChains).not.toHaveBeenCalled();
  });

  it('bounds price requests and cancels pending prices when refreshed or destroyed', () => {
    const pending = Array.from({ length: 7 }, () => new Subject<StockInfo>());
    service.getStockInfo.and.returnValues(...pending);
    const element = mount(['ALFA', 'BETA', 'GAMMA', 'DELTA'].map(symbol => quote({ symbol })));
    expect(service.getStockInfo).toHaveBeenCalledTimes(3);
    pending[0].next(stockInfo(100));
    pending[0].complete();
    expect(service.getStockInfo).toHaveBeenCalledTimes(4);

    (element.querySelector('.panel-heading button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(pending[1].observed).toBeFalse();
    expect(pending[2].observed).toBeFalse();
    expect(pending[3].observed).toBeFalse();
    expect(service.getStockInfo).toHaveBeenCalledTimes(7);
    pending[4].next(stockInfo(105));
    fixture.detectChanges();
    expect(element.querySelector('.symbol-price')?.textContent).toContain('$105.00');
    fixture.destroy();
    expect(pending[4].observed).toBeFalse();
    expect(pending[5].observed).toBeFalse();
    expect(pending[6].observed).toBeFalse();
  });

  it('replaces groups when collection refreshes the archive and on manual archive refresh', () => {
    const element = mount([quote()]);
    service.getOptionQuotes.and.returnValue(of(snapshot([quote({ symbol: 'NEXT' })])));
    fixture.componentRef.setInput('refreshToken', 1);
    fixture.detectChanges();
    expect(element.querySelector('.symbol-heading')?.textContent).toContain('NEXT');
    expect(element.querySelector('.symbol-heading')?.textContent).not.toContain('DEMO');

    service.getOptionQuotes.and.returnValue(of(snapshot([])));
    (element.querySelector('.panel-heading button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(element.querySelectorAll('.quote-symbol').length).toBe(0);
    expect(service.getOptionQuotes).toHaveBeenCalledTimes(3);
    expect(service.runChains).not.toHaveBeenCalled();
  });

  it('shows the newest four reports and loads rows only for the opened report', () => {
    const reports = ['20260828T160000000000Z', '20260828T150000000000Z', '20260828T140000000000Z', '20260828T130000000000Z']
      .map((runId, index) => ({ ...snapshot([]), runId, reportName: `2026-08-28__horizon37_cushion5_filterholdings(${index ? 'F' : 'T'})_etfOnly(F)_rvRank40-80_trendBULLISH` }));
    service.getOptionQuoteReports.and.returnValue(of(reports));
    service.getOptionQuotes.and.callFake(runId => of(snapshot([quote({ symbol: runId === reports[1].runId ? 'OLDER' : 'NEWER' })])));
    fixture = TestBed.createComponent(OptionQuotesTabComponent);
    fixture.componentRef.setInput('refreshToken', 0);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelectorAll('.report-item').length).toBe(4);
    expect(element.textContent).toContain('Aug 28, 2026 · 37 DTE · 5% OTM · Holdings: Yes · ETFs all · RV Rank 40-80 · Trend Bullish');
    expect(service.getOptionQuotes).toHaveBeenCalledWith(reports[0].runId);
    (element.querySelectorAll('.report-item')[1] as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(service.getOptionQuotes).toHaveBeenCalledWith(reports[1].runId);
    expect(element.querySelector('.symbol-heading')?.textContent).toContain('OLDER');
  });

  it('shows combined open equity shares and a share-weighted average cost beside market price', () => {
    brokerageService.getHoldings.and.callFake(id => of(holdingsResponse(
      id === 'tastytrade'
        ? [holding({ quantity: 10, cost_basis: 800 }), holding({ account_id: 'second', quantity: 0.125, cost_basis: 15 })]
        : [holding({ account_id: 'ira', quantity: 20, cost_basis: 2400 })],
    )));
    const element = mount([quote()]);
    const header = element.querySelector('.symbol-heading')!;

    expect(header.textContent).toContain('Market price');
    expect(header.textContent).toContain('Shares owned 30.125');
    expect(header.textContent).toContain('Avg cost/share $106.72');
    expect(header.textContent).not.toMatch(/\d+ contracts\s*·\s*\d+ exp/);
    expect(brokerageService.getHoldings).toHaveBeenCalledTimes(2);
    expect(brokerageService.getHoldings).toHaveBeenCalledWith('tastytrade');
    expect(brokerageService.getHoldings).toHaveBeenCalledWith('fidelity');
    expect(fixture.componentInstance.holdingsTooltip(fixture.componentInstance.holding('DEMO')!))
      .toContain('Trading: positions as of 2026-07-16T14:02:00Z');
  });

  it('excludes option, short, flat, and zero-quantity holdings', () => {
    brokerageService.getHoldings.and.callFake(id => of(holdingsResponse(id === 'tastytrade' ? [
      holding({ quantity: 7, cost_basis: 700 }),
      holding({ instrument: 'OPTION', quantity: 2, cost_basis: 200 }),
      holding({ side: 'SHORT', quantity: 3, cost_basis: 300 }),
      holding({ state: 'FLAT', quantity: 4, cost_basis: 400 }),
      holding({ quantity: 0, cost_basis: 0 }),
    ] : [])));
    const element = mount([quote()]);
    const header = element.querySelector('.symbol-heading')!;

    expect(header.textContent).toContain('Shares owned 7');
    expect(header.textContent).toContain('Avg cost/share $100.00');
  });

  it('leaves combined cost unavailable when any held account has no basis', () => {
    brokerageService.getHoldings.and.callFake(id => of(holdingsResponse(id === 'tastytrade'
      ? [holding({ quantity: 10, cost_basis: 0 })]
      : [holding({ account_id: 'ira', quantity: 10, cost_basis: null })],
    )));
    const element = mount([quote()]);
    const header = element.querySelector('.symbol-heading')!;

    expect(header.textContent).toContain('Shares owned 20');
    expect(header.textContent).toContain('Avg cost/share —');
    expect(fixture.componentInstance.holdingsTooltip(fixture.componentInstance.holding('DEMO')!))
      .toContain('at least one holding has no cost basis');
  });

  it('labels figures as known holdings when a brokerage is unavailable without hiding quotes', () => {
    brokerageService.getHoldings.and.callFake(id => id === 'tastytrade'
      ? of(holdingsResponse([holding({ quantity: 5, cost_basis: 500 })], 'PARTIAL'))
      : throwError(() => new Error('not configured')),
    );
    const element = mount([quote()]);
    const header = element.querySelector('.symbol-heading')!;

    expect(header.textContent).toContain('Known shares 5');
    expect(header.textContent).toContain('Known avg cost/share $100.00');
    expect(element.textContent).toContain('Trading holdings incomplete. Retirement holdings unavailable.');
    expect(element.querySelectorAll('tbody tr').length).toBe(1);
  });

  it('lays out independent sides in columns when wide and stacks them in a narrow container', () => {
    const element = mount([quote(), quote({ side: 'CALL', strike: 110 })]);
    element.style.width = '1440px';
    const sides = element.querySelectorAll('.quote-side');
    let put = sides[0].getBoundingClientRect();
    let call = sides[1].getBoundingClientRect();
    expect(call.left).toBeGreaterThanOrEqual(put.right);
    expect(call.top).toBe(put.top);

    element.style.width = '390px';
    put = sides[0].getBoundingClientRect();
    call = sides[1].getBoundingClientRect();
    expect(call.left).toBe(put.left);
    expect(call.top).toBeGreaterThanOrEqual(put.bottom);
    const grid = element.querySelector('.quote-sides')!.getBoundingClientRect();
    expect(call.right).toBeLessThanOrEqual(grid.right + 1);
    const shell = element.querySelector('.table-shell') as HTMLElement;
    expect(shell.scrollWidth).toBeGreaterThan(shell.clientWidth);
    expect(shell.getAttribute('tabindex')).toBe('0');
  });
});
