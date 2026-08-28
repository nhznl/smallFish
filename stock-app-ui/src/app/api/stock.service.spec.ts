import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { StockInfo } from '../model/stock-info';
import { API_BASE_URL } from './api-base';
import { StockService } from './stock.service';

function info(price: number): StockInfo {
  return {
    ticker: 'DEMO', period: 'info', retrievedAt: '2026-07-16T14:00:00Z',
    company: {}, price: { regularMarketPrice: price, currency: 'USD' }, valuation: {}, news: [],
  };
}

describe('StockService stock info refresh', () => {
  let service: StockService;
  let http: HttpTestingController;
  const url = '/stocks/DEMO/info';

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), { provide: API_BASE_URL, useValue: '' }],
    });
    service = TestBed.inject(StockService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('keeps default caching but explicitly refreshes the market price when requested', () => {
    service.getStockInfo(' demo ').subscribe();
    http.expectOne(url).flush(info(100));
    service.getStockInfo('DEMO').subscribe(value => expect(value.price.regularMarketPrice).toBe(100));
    http.expectNone(url);

    service.getStockInfo('DEMO', { refresh: true }).subscribe(value => expect(value.price.regularMarketPrice).toBe(105));
    http.expectOne(url).flush(info(105));
    service.getStockInfo('DEMO').subscribe(value => expect(value.price.regularMarketPrice).toBe(105));
    http.expectNone(url);
  });

  it('shares an in-flight refresh and does not substitute cached data if that refresh fails', () => {
    service.getStockInfo('DEMO').subscribe();
    http.expectOne(url).flush(info(100));
    const errors: number[] = [];
    service.getStockInfo('DEMO', { refresh: true }).subscribe({
      next: () => fail('A failed fresh request must not return the cached price'),
      error: error => errors.push(error.status),
    });
    service.getStockInfo('DEMO', { refresh: true }).subscribe({ error: error => errors.push(error.status) });
    http.expectOne(url).flush({}, { status: 503, statusText: 'Unavailable' });
    expect(errors).toEqual([503, 503]);

    service.getStockInfo('DEMO', { refresh: true }).subscribe(value => expect(value.price.regularMarketPrice).toBe(106));
    http.expectOne(url).flush(info(106));
  });
});

describe('StockService option quote reports', () => {
  let service: StockService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), { provide: API_BASE_URL, useValue: '' }],
    });
    service = TestBed.inject(StockService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists report summaries before fetching one immutable run', () => {
    service.getOptionQuoteReports().subscribe(reports => expect(reports.length).toBe(1));
    http.expectOne('/optionQuoteReports').flush({ reports: [{ runId: '20260828T160000000000Z' }] });
    service.getOptionQuotes('20260828T160000000000Z').subscribe(snapshot => expect(snapshot.runId).toBe('20260828T160000000000Z'));
    http.expectOne('/optionQuotes?runId=20260828T160000000000Z').flush({
      available: true, runId: '20260828T160000000000Z', rows: [],
    });
  });
});
