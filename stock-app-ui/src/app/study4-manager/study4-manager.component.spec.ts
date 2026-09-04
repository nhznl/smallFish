import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { Study4ManagerComponent } from './study4-manager.component';
import { Study4ExecutionService } from '../api/study4-execution.service';

const configuredStatus = {
  configured: true,
  environment: 'sandbox',
  productionEnabled: false,
  killSwitch: false,
  accountAlias: 'study4-sandbox',
  accountFingerprint: 'abc',
  protocolId: 'pre-earnings-post-event-weekly-batch-risk-on-v1',
  operationalId: 'study4-live-v1',
  exploratoryWarning: 'Study 4 remains NO_VERDICT / EXPLORATORY.',
  setupRequirements: [
    { id: 'execution_mode', label: 'Execution mode', ready: true, action: 'Set SFP_STUDY4_EXECUTION_MODE=sandbox' }
  ],
  capital: { id: 1, target_bucket: 10000, active_bucket: 10000, production_cap: null, cash_flow_status: 'funded' },
  configuredTargetBucket: 10000,
  recommendedPilotCap: 10000,
  cycle: { id: 1, iso_week: '2026-W36', cutoff_session: '2026-09-03', execution_session: '2026-09-04', state: 'PlanFinalized', environment: 'sandbox', plan_hash: 'hash' },
  nextAction: 'preflight',
  submissionsAllowed: true
};

function apiStub(status: Record<string, unknown> = configuredStatus) {
  return {
    status: () => of(status),
    cycle: () => of({
      cycle: (status as { cycle?: unknown }).cycle ?? null,
      snapshots: [{ session: '2026-09-03', payload: { scanRows: [{ ticker: 'AAA', state: 'selected', setup_score: 70, decision_close: 10, shares: 10 }] } }],
      plan: { plan_hash: 'hash', payload: { planItems: [{ priority: 1, kind: 'stock_entry', symbol: 'AAA', side: 'buy', deltaShares: 10, limitPrice: 10.3, timeInForce: 'IOC' }] }, items: [] },
      intents: [{ external_identifier: 'uuid', state: 'DRY_RUN_PASSED', broker_order_id: null }],
      reconciliations: []
    }),
    performance: () => of({ series: [], unavailableDates: [], modeledCostNote: 'Broker-net is primary.' }),
    audit: () => of({ events: [] }),
    scan: () => of({}),
    finalize: () => of({}),
    preflight: () => of({ confirmationChallenge: 'token', planHash: 'hash', dryRuns: [] }),
    confirm: () => of({}),
    execute: () => of({}),
    reconcile: () => of({}),
    finalSync: () => of({}),
    killSwitch: () => of({}),
    capitalReset: jasmine.createSpy('capitalReset').and.returnValue(of({})),
    resetLedger: () => of({})
  };
}

describe('Study4ManagerComponent', () => {
  let fixture: ComponentFixture<Study4ManagerComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Study4ManagerComponent],
      providers: [
        provideRouter([]),
        { provide: Study4ExecutionService, useValue: apiStub() }
      ]
    }).compileComponents();
    fixture = TestBed.createComponent(Study4ManagerComponent);
    fixture.detectChanges();
  });

  it('names the strategy and links to Study 3 and Study 4 without the exploratory banner', () => {
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('SANDBOX');
    expect(text).not.toContain('EXPLORATORY');
    expect(text).toContain('Pre-Earnings Momentum');
    expect(text).toContain('Study 3');
    expect(text).toContain('Study 4');
    expect(text).toContain('Kill switch stops new broker submissions immediately');
    const hrefs = [...fixture.nativeElement.querySelectorAll('a')].map((el: HTMLAnchorElement) => el.getAttribute('href') || '');
    expect(hrefs.some(href => href.includes('post-earnings-risk-on'))).toBeTrue();
    expect(hrefs.some(href => href.includes('post-earnings-weekly-extension'))).toBeTrue();
    expect(hrefs).toContain('/preEarningsExplainer');
  });

  it('lists tracking, plan, and execute views', () => {
    const buttons = [...fixture.nativeElement.querySelectorAll('.tab')].map((el: HTMLElement) => el.textContent?.trim());
    expect(buttons).toEqual(['overview', 'tracking', 'plan', 'execute', 'reconcile', 'performance', 'audit']);
  });

  it('initializes the capital version from the configured target, not the pilot cap', () => {
    const component = fixture.componentInstance;
    component.status = {
      ...configuredStatus,
      configuredTargetBucket: 50000,
      recommendedPilotCap: 5000,
      capital: null
    } as typeof component.status;
    const api = TestBed.inject(Study4ExecutionService) as unknown as { capitalReset: jasmine.Spy };

    component.initializeCapital();

    expect(api.capitalReset).toHaveBeenCalledWith(50000);
  });
});

describe('Study4ManagerComponent unconfigured', () => {
  it('lists the missing setup instead of Awaiting setup', async () => {
    await TestBed.configureTestingModule({
      imports: [Study4ManagerComponent],
      providers: [
        provideRouter([]),
        {
          provide: Study4ExecutionService,
          useValue: apiStub({
            ...configuredStatus,
            configured: false,
            environment: null,
            cycle: null,
            capital: null,
            configuredTargetBucket: null,
            nextAction: 'configure_execution_mode',
            submissionsAllowed: false,
            setupRequirements: [
              { id: 'execution_mode', label: 'Execution mode', ready: false, action: 'Set SFP_STUDY4_EXECUTION_MODE=sandbox in app.env' }
            ]
          })
        }
      ]
    }).compileComponents();
    const fixture = TestBed.createComponent(Study4ManagerComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Not configured');
    expect(text).not.toContain('Awaiting setup');
    expect(text).toContain('Setup needed');
    expect(text).toContain('Complete setup below');
    expect(text).not.toContain('configure_execution_mode');
    expect(text).toContain('SFP_STUDY4_EXECUTION_MODE');
    expect(text).not.toContain('NOT CONFIGURED');
    expect(text).not.toContain('EXPLORATORY');
  });
});
