import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Study4ExecutionService } from './study4-execution.service';

describe('Study4ExecutionService', () => {
  let service: Study4ExecutionService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [Study4ExecutionService, provideHttpClient(withXhr()), provideHttpClientTesting()]
    });
    service = TestBed.inject(Study4ExecutionService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loads status from the execution namespace', () => {
    service.status().subscribe(status => {
      expect(status.configured).toBeFalse();
      expect(status.exploratoryWarning).toContain('EXPLORATORY');
    });
    http.expectOne(req => req.url.endsWith('/api/execution/study4/status')).flush({
      configured: false,
      environment: null,
      productionEnabled: false,
      killSwitch: false,
      accountAlias: 'study4',
      accountFingerprint: null,
      protocolId: 'pre-earnings-post-event-weekly-batch-risk-on-v1',
      operationalId: 'study4-live-v1',
      exploratoryWarning: 'Study 4 remains NO_VERDICT / EXPLORATORY.',
      setupRequirements: [],
      capital: null,
      configuredTargetBucket: null,
      recommendedPilotCap: null,
      cycle: null,
      nextAction: 'configure_execution_mode',
      submissionsAllowed: false
    });
  });

  it('resets the local ledger only with an explicit confirmation', () => {
    service.resetLedger('RESET').subscribe();
    const req = http.expectOne(r => r.url.endsWith('/api/execution/study4/reset'));
    expect(req.request.body).toEqual({ confirmation: 'RESET' });
    req.flush({ ok: true });
  });

  it('does not expose a generic order-entry payload', () => {
    service.execute(3).subscribe();
    const req = http.expectOne(r => r.url.endsWith('/api/execution/study4/cycles/current/execute'));
    expect(req.request.body).toEqual({ planId: 3 });
    expect(req.request.body.symbol).toBeUndefined();
    req.flush({ results: [] });
  });
});
