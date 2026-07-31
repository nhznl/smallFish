import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';

import { BrokerageService } from './brokerage.service';
import { BrokerageId } from '../model/brokerage';

/** The whole point of this client is that the brokerage id only ever changes
 *  the URL. These tests pin that down. */
describe('BrokerageService', () => {
  let service: BrokerageService;
  let http: HttpTestingController;
  const base = window.location.origin;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), BrokerageService],
    });
    service = TestBed.inject(BrokerageService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  const ids: BrokerageId[] = ['tastytrade', 'fidelity'];

  for (const id of ids) {
    it(`builds identical resource paths for ${id}`, () => {
      service.getHoldings(id).subscribe();
      const holdings = http.expectOne(`${base}/api/brokerages/${id}/holdings`);
      expect(holdings.request.method).toBe('GET');
      holdings.flush({});

      service.getOptions(id).subscribe();
      const options = http.expectOne(`${base}/api/brokerages/${id}/options`);
      expect(options.request.method).toBe('GET');
      options.flush({});

      service.getOptionAdjustedBasis(id).subscribe();
      const basis = http.expectOne(`${base}/api/brokerages/${id}/option-adjusted-basis`);
      expect(basis.request.method).toBe('GET');
      basis.flush({});

      service.listSymbols(id).subscribe();
      const symbols = http.expectOne(`${base}/api/brokerages/${id}/symbols`);
      expect(symbols.request.method).toBe('GET');
      symbols.flush({});
    });
  }

  it('omits unset query parameters instead of sending empty values', () => {
    service.listSymbols('tastytrade').subscribe();
    const allSymbols = http.expectOne(`${base}/api/brokerages/tastytrade/symbols`);
    expect(allSymbols.request.method).toBe('GET');
    allSymbols.flush({});

    service.listSymbols('tastytrade', { state: 'closed' }).subscribe();
    const closed = http.expectOne(`${base}/api/brokerages/tastytrade/symbols?state=closed`);
    expect(closed.request.method).toBe('GET');
    closed.flush({});

    service.listSymbols('tastytrade', { state: 'active', exposure: 'options' }).subscribe();
    const filtered = http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols?state=active&exposure=options`
    );
    expect(filtered.request.method).toBe('GET');
    filtered.flush({});

    service.getSymbolEvents('tastytrade', 'ABC', { period: 'current', cursor: null }).subscribe();
    const events = http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols/ABC/events?period=current`
    );
    expect(events.request.method).toBe('GET');
    events.flush({});
  });

  it('encodes a symbol that contains a dot', () => {
    service.getSymbol('fidelity', 'BRK.B').subscribe();
    const request = http.expectOne(
      req => req.url === `${base}/api/brokerages/fidelity/symbols/BRK.B`
    );
    expect(request.request.method).toBe('GET');
    request.flush({});
  });

  it('keeps a leading slash in the path for futures roots', () => {
    service.getSymbol('tastytrade', '/ESU6').subscribe();
    const request = http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols//ESU6`
    );
    expect(request.request.method).toBe('GET');
    request.flush({});

    service.getSymbolEvents('tastytrade', '/ESU6', { period: 'current' }).subscribe();
    const events = http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols//ESU6/events?period=current`
    );
    expect(events.request.method).toBe('GET');
    events.flush({});
  });

  it('patches only notes on a symbol', () => {
    service.updateSymbolNotes('fidelity', 'ABC', 'watch assignment').subscribe();
    const request = http.expectOne(`${base}/api/brokerages/fidelity/symbols/ABC`);
    expect(request.request.method).toBe('PATCH');
    expect(request.request.body).toEqual({ notes: 'watch assignment' });
    request.flush({});
  });

  it('sends the idempotency key and expected version when archiving', () => {
    service.createArchive('tastytrade', 'ABC', {
      request_id: 'req-1', expected_period_version: 'v1:abc', note: 'done',
    }).subscribe();
    const request = http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols/ABC/archives`
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      request_id: 'req-1', expected_period_version: 'v1:abc', note: 'done',
    });
    request.flush({});
  });
});
