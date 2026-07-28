import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient, withXhr } from '@angular/common/http';
import { CapabilityService, CapabilitySnapshot } from './capability.service';

const SNAPSHOT: CapabilitySnapshot = {
  schemaName: 'smallfish.capabilities',
  schemaVersion: 1,
  capabilities: [
    {
      id: 'tastytrade', label: 'Tastytrade', provides: 'the options ledger',
      state: 'NOT_CONFIGURED', available: false, reason: 'Not connected.',
      action: './setup-brokerages.sh setup tastytrade', provider: 'Tastytrade',
      docs: 'docs/BROKERAGES.md', requires: { TT_CLIENT_SECRET: false }
    }
  ],
  unavailable: ['tastytrade']
};

describe('CapabilityService', () => {
  let service: CapabilityService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [CapabilityService, provideHttpClient(withXhr()), provideHttpClientTesting()]
    });
    service = TestBed.inject(CapabilityService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('returns the requested capability', done => {
    service.get('tastytrade').subscribe(capability => {
      expect(capability?.state).toBe('NOT_CONFIGURED');
      expect(capability?.action).toBe('./setup-brokerages.sh setup tastytrade');
      done();
    });
    http.expectOne(request => request.url.endsWith('/capabilities')).flush(SNAPSHOT);
  });

  it('returns null for a capability the backend does not report', done => {
    service.get('nonexistent').subscribe(capability => {
      expect(capability).toBeNull();
      done();
    });
    http.expectOne(request => request.url.endsWith('/capabilities')).flush(SNAPSHOT);
  });

  it('fetches once and shares the result across subscribers', done => {
    service.get('tastytrade').subscribe(() => {
      service.all().subscribe(snapshot => {
        expect(snapshot.unavailable).toEqual(['tastytrade']);
        done();
      });
    });
    // A second request would fail http.verify().
    http.expectOne(request => request.url.endsWith('/capabilities')).flush(SNAPSHOT);
  });

  it('degrades to unknown rather than breaking the page it decorates', done => {
    service.get('tastytrade').subscribe(capability => {
      expect(capability).toBeNull();
      done();
    });
    http.expectOne(request => request.url.endsWith('/capabilities'))
      .flush('boom', { status: 500, statusText: 'Server Error' });
  });
});
