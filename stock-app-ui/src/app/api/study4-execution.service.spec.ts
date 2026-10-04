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
      protocolId: null,
      operationalId: 'study7-bc-live-v1',
      selectedArm: null,
      strategyVersionId: null,
      availableArms: ['stocks-spy-control', 'stocks-spxl-spy-control'],
      canChangeArm: true,
      exploratoryWarning: 'Study 7 B/C remains NO_VERDICT / EXPLORATORY.',
      setupRequirements: [],
      capital: null,
      configuredTargetBucket: null,
      recommendedPilotCap: null,
      cycle: null,
      nextAction: 'configure_execution_mode',
      submissionsAllowed: false
    });
  });

  it('selects a Study 7 strategy arm without exposing an order payload', () => {
    service.selectStrategy('stocks-spxl-spy-control').subscribe();
    const req = http.expectOne(r => r.url.endsWith('/api/execution/study4/strategy'));
    expect(req.request.body).toEqual({ arm: 'stocks-spxl-spy-control' });
    expect(req.request.body.symbol).toBeUndefined();
    req.flush({ id: 2 });
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
