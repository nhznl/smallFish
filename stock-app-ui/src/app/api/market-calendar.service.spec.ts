import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient, withXhr } from '@angular/common/http';
import { MarketCalendarService } from './market-calendar.service';
import { MarketEventCollection } from '../model/market-event';

describe('MarketCalendarService', () => {
  let service: MarketCalendarService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withXhr()), provideHttpClientTesting()]
    });
    service = TestBed.inject(MarketCalendarService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('reads the published scan without posting a refresh', () => {
    let body: MarketEventCollection | undefined;
    service.getCollection({ from: '2026-10-04', to: '2026-11-04' }).subscribe(response => body = response);
    const request = http.expectOne(item => item.url.includes('/api/market-events'));
    expect(request.request.method).toBe('GET');
    expect(request.request.params.get('from')).toBe('2026-10-04');
    expect(request.request.params.get('to')).toBe('2026-11-04');
    request.flush({ available: false, coverageStatus: 'unavailable', summary: { primaryRiskDays: null } } as MarketEventCollection);
    expect(body?.coverageStatus).toBe('unavailable');
    expect(body?.summary.primaryRiskDays).toBeNull();
  });
});
