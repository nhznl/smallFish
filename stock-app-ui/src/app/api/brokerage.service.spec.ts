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
      http.expectOne(`${base}/api/brokerages/${id}/holdings`).flush({});

      service.getOptions(id).subscribe();
      http.expectOne(`${base}/api/brokerages/${id}/options`).flush({});

      service.getOptionAdjustedBasis(id).subscribe();
      http.expectOne(`${base}/api/brokerages/${id}/option-adjusted-basis`).flush({});

      service.listSymbols(id).subscribe();
      http.expectOne(`${base}/api/brokerages/${id}/symbols`).flush({});
    });
  }

  it('omits unset query parameters instead of sending empty values', () => {
    service.listSymbols('tastytrade').subscribe();
    http.expectOne(`${base}/api/brokerages/tastytrade/symbols`).flush({});

    service.listSymbols('tastytrade', { state: 'archived' }).subscribe();
    http.expectOne(`${base}/api/brokerages/tastytrade/symbols?state=archived`).flush({});

    service.listSymbols('tastytrade', { state: 'active', exposure: 'options' }).subscribe();
    http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols?state=active&exposure=options`
    ).flush({});

    service.getSymbolEvents('tastytrade', 'ABC', { period: 'current', cursor: null }).subscribe();
    http.expectOne(
      `${base}/api/brokerages/tastytrade/symbols/ABC/events?period=current`
    ).flush({});
  });

  it('encodes a symbol that contains a dot', () => {
    service.getSymbol('fidelity', 'BRK.B').subscribe();
    const request = http.expectOne(
      req => req.url === `${base}/api/brokerages/fidelity/symbols/BRK.B`
    );
    expect(request.request.method).toBe('GET');
    request.flush({});
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
