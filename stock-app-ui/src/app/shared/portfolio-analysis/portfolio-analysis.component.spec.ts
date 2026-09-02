import { TestBed } from '@angular/core/testing';
import { MatTooltip } from '@angular/material/tooltip';
import { By } from '@angular/platform-browser';
import { of, throwError } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import {
  PortfolioAnalysisProfile,
  PortfolioAnalysisResponse,
  PortfolioPreviewResponse,
} from '../../model/brokerage';
import { PortfolioAnalysisComponent } from './portfolio-analysis.component';

function profile(): PortfolioAnalysisProfile {
  return {
    objective: 'LONG_TERM_AGGRESSIVE_GROWTH', status: 'COMPLETE', max_single_issuer_pct: 12,
    max_speculative_pct: 20, max_put_assignment_commitment_pct: 20,
    max_stress_loss_pct: 35, minimum_liquid_pct: 5, notes: 'Synthetic limits',
    reviewed_at: '2026-08-29T20:00:00Z', max_gross_exposure_pct: null,
    deployment_min_pct: null, deployment_max_pct: null, max_sector_pct: 30,
    growth_min_pct: 85, growth_max_pct: 100, cash_min_pct: 0, cash_max_pct: 15,
    max_top_five_pct: 65, first_expected_withdrawal_date: '2045-01-01',
  };
}

function response(
  role = 'RETIREMENT',
  status: 'UNCONFIGURED' | 'PARTIAL' | 'COMPLETE' = 'COMPLETE',
): PortfolioAnalysisResponse {
  return {
    schema_name: 'smallfish.portfolio-analysis', schema_version: 1,
    brokerage: { id: 'tastytrade', label: 'Synthetic Broker', institution: 'SYNTHETIC', portfolio_role: role },
    availability: { status: 'AVAILABLE', reasons: [] },
    as_of: {
      positions: '2026-08-29T20:00:00Z', activity: '2026-08-29T20:00:00Z',
      market: '2026-08-29T19:59:00Z',
      capital: '2026-08-29T20:00:00Z', cached_prices: '2026-08-28',
    },
    coverage: {
      status: 'COMPLETE', history_start: '2025-08-28', equity_activity: 'COMPLETE',
      option_activity: 'COMPLETE', reached_provider_boundary: true, reasons: [],
    },
    summary: {
      profile: status === 'UNCONFIGURED'
        ? { objective: role === 'TRADING' ? 'SPECULATIVE_TRADING' : 'LONG_TERM_AGGRESSIVE_GROWTH', status }
        : { ...profile(), status, ...(status === 'PARTIAL' ? { max_top_five_pct: null } : {}) },
      verdicts: {
        profile_fit: 'ABOVE_PROFILE', construction: 'CONCENTRATED',
        capital_deployment: 'IN_RANGE', data_confidence: 'COMPLETE',
      },
      capital: {
        analyzed_capital: 100_000, liquid_value: 10_000, reconciliation_gap: 0,
        accounts: [{
          account_id: 'synthetic-account', account: 'Synthetic Retirement', currency: 'USD',
          net_liquidating_value: 100_000, cash_balance: 10_000, buying_power: 25_000,
          maintenance_requirement: null, source: 'SYNTHETIC',
          retrieved_at: '2026-08-29T20:00:00Z', missing: [],
        }],
      },
      allocation: {
        buckets: {
          GROWTH: { market_value: 75_000, pct_of_capital: 75 },
          SPECULATIVE: { market_value: 15_000, pct_of_capital: 15 },
          DEFENSIVE: { market_value: 0, pct_of_capital: 0 },
          CASH: { market_value: 10_000, pct_of_capital: 10 },
          UNKNOWN: { market_value: 0, pct_of_capital: 0 },
        },
        growth_pct: 90, liquid_pct: 10, deployment_pct: 90,
        gross_marked_exposure_pct: 90,
      },
      concentration: {
        largest_issuer_pct: 18.4, top_five_pct: 60, effective_position_count: 7.4,
        issuers: [{ symbol: 'SYNTH', market_value: 18_400, pct_of_capital: 18.4 }],
        sectors: [{ sector: 'Technology', market_value: 35_000, pct_of_capital: 35 }],
        sector_classified_pct: 90,
      },
      historical_risk: {
        status: 'COMPLETE', label: 'Current-holdings replay', date_start: '2025-08-28',
        date_end: '2026-08-28', aligned_sessions: 252, analyzed_market_value: 90_000,
        excluded_symbols: [], excluded_pct: 0, annualized_volatility_pct: 28.4,
        beta_vs_spy: null, correlation_vs_spy: 0.82, maximum_drawdown_pct: -22.3,
      },
      stress: {
        status: 'COMPLETE', classification: 'HYPOTHETICAL', severe_loss_pct: 31.5,
        excluded_value: 10_000,
        scenarios: [{ shock_pct: -35, estimated_loss: -31_500, estimated_loss_pct: 31.5 }],
      },
      option_commitments: {
        open_contract_count: 1,
        put_assignment_commitment: 12_000, put_assignment_commitment_pct: 12,
        long_option_premium_at_risk: 800, long_option_premium_at_risk_pct: 0.8,
        by_underlying: [{ symbol: 'SYNTH', amount: 12_000, pct_of_capital: 12 }],
        uncovered_short_calls: [], risk_completeness: 'INDICATIVE',
        missing: ['OPTION_ACTIVITY_HISTORY'],
        note: 'Put spreads contribute zero to cash-secured-put commitment, not zero risk.',
      },
      findings: [{
        code: 'SINGLE_ISSUER_LIMIT', severity: 'HIGH', direction: 'OVER', scope: 'ISSUER',
        symbol: 'SYNTH', title: 'SYNTH exceeds the selected issuer limit', actual: 18.4,
        limit: 12, unit: 'PERCENT_OF_CAPITAL', excess_amount: 6_400,
        explanation: 'Current long value is above the profile limit.',
        remediation: {
          immediate_trim_amount: 6_400, approximate_units: 51,
          new_outside_capital_to_dilute: 53_333, price: 125.5,
          price_source: 'CACHED_CLOSE', price_as_of: '2026-08-28',
        },
      }],
    },
    items: [{
      account_id: 'synthetic-account', account: 'Synthetic Retirement', symbol: 'SYNTH',
      display_name: '',
      instrument: 'EQUITY', quantity: 146.61, market_value: 18_400, weight_pct: 18.4,
      allocation_bucket: 'GROWTH', classification_source: 'PROVIDER_INSTRUMENT',
      sector: 'Technology', security_type: 'STOCK', mark_per_unit: 125.5,
      price_source: 'CACHED_CLOSE', price_as_of: '2026-08-28',
    }],
    warnings: [],
  };
}

function previewResult(base: PortfolioAnalysisResponse): PortfolioPreviewResponse {
  return {
    schema_name: 'smallfish.portfolio-analysis-preview', schema_version: 1,
    brokerage: base.brokerage, persisted: false,
    proposal: {
      account_id: 'synthetic-account', side: 'SELL', symbol: 'SYNTH', quantity: 51,
      notional: 6_400, assumed_price: 125.5, price_source: 'USER_ASSUMPTION',
      price_as_of: null, funding_source: 'SALE_PROCEEDS_TO_CASH',
      fees_taxes_slippage_included: false,
    },
    before: base.summary,
    after: {
      ...base.summary,
      verdicts: { ...base.summary.verdicts, profile_fit: 'ALIGNED', construction: 'WELL_CONSTRUCTED' },
    },
    metric_deltas: [{
      metric: 'deployment_pct', before: 90, after: 83.6, change: -6.4,
    }],
    new_findings: [], worsened_findings: [], improved_findings: [],
    resolved_findings: base.summary.findings,
  };
}

function apiStub(analysis: PortfolioAnalysisResponse): jasmine.SpyObj<BrokerageService> {
  const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', [
    'getPortfolioAnalysis', 'getPortfolioAnalysisProfile', 'updatePortfolioAnalysisProfile',
    'updatePortfolioClassification', 'previewPortfolioChange',
  ]);
  api.getPortfolioAnalysis.and.returnValue(of(analysis));
  api.getPortfolioAnalysisProfile.and.returnValue(of({
    schema_name: 'smallfish.portfolio-analysis-profile', schema_version: 1,
    brokerage: analysis.brokerage, profile: analysis.summary.profile,
  }));
  return api;
}

describe('PortfolioAnalysisComponent', () => {
  it('uses portfolio_role wording and renders traceable findings and unavailable values', async () => {
    const analysis = response('RETIREMENT');
    const api = apiStub(analysis);
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'tastytrade');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(api.getPortfolioAnalysis).toHaveBeenCalledWith('tastytrade');
    expect(text).toContain('Long-term aggressive growth');
    expect(text).not.toContain('Speculative trading risk budget');
    expect(text).not.toContain('Data confidence');
    expect(text).toContain('SYNTH exceeds the selected issuer limit');
    expect(text).toContain('18.40%');
    expect(text).toContain('12.00%');
    expect(text).toContain('$6,400.00');
    expect(text).toContain('Cash balance');
    expect(text).toContain('Buying power');
    expect(text).toContain('$10,000.00');
    const betaMetric = Array.from(
      fixture.nativeElement.querySelectorAll('.risk-layout .compact-metrics > div') as NodeListOf<HTMLElement>
    ).find(metric => metric.querySelector('dt')?.textContent?.includes('Beta vs SPY'));
    expect(betaMetric?.querySelector('dd')?.textContent?.trim()).toBe('—');
    expect(text).toContain('Current-holdings replay');
    expect(text).toContain('HYPOTHETICAL');
    expect(fixture.nativeElement.querySelector('.analyzed-table .col-sticky')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('.analyzed-table th button')).toBeTruthy();
  });

  it('shows a holdings display name instead of the broker symbol', async () => {
    const analysis = response('RETIREMENT');
    analysis.summary.findings[0].title = 'Example Target Date Fund exceeds the selected issuer limit';
    analysis.items[0].display_name = 'Example Target Date Fund';
    const api = apiStub(analysis);
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('Example Target Date Fund exceeds the selected issuer limit');
    expect(text).not.toContain('SYNTH exceeds the selected issuer limit');
    expect(
      (fixture.nativeElement.querySelector('.analyzed-table .symbol-cell strong') as HTMLElement)
        .textContent?.trim()
    ).toBe('Example Target Date Fund');
  });

  it('hides non-actionable cash-capital availability notices', async () => {
    const analysis = response();
    analysis.warnings = [
      { code: 'CASH_BALANCE_UNAVAILABLE', scope: 'CAPITAL', symbol: null, component_id: null,
        message: 'Cash balance unavailable.' },
      { code: 'BUYING_POWER_UNAVAILABLE', scope: 'CAPITAL', symbol: null, component_id: null,
        message: 'Buying power unavailable.' },
      { code: 'MAINTENANCE_REQUIREMENT_UNAVAILABLE', scope: 'CAPITAL', symbol: null, component_id: null,
        message: 'Maintenance requirement unavailable.' },
    ];
    const api = apiStub(analysis);
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'tastytrade');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).not.toContain('Cash balance unavailable');
    expect(text).not.toContain('Buying power unavailable');
    expect(text).not.toContain('Maintenance requirement unavailable');
  });

  it('renders an unconfigured profile as a valid not-assessed state with an action', async () => {
    const analysis = response('TRADING', 'UNCONFIGURED');
    analysis.summary.verdicts.profile_fit = 'NOT_ASSESSED';
    const api = apiStub(analysis);
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'tastytrade');
    fixture.detectChanges();

    fixture.componentInstance.openProfileEditor();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('Speculative trading risk budget');
    expect(text).toContain('No risk limits have been saved');
    expect(text).toContain('Not assessed');
    expect(text).toContain('Trading is never called underinvested');
  });

  it('explains every visible profile field with a keyboard-accessible info bubble', async () => {
    const analysis = response('RETIREMENT');
    const api = apiStub(analysis);
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.detectChanges();

    fixture.componentInstance.openProfileEditor();
    fixture.detectChanges();

    const bubbles = fixture.debugElement.queryAll(By.directive(MatTooltip));
    expect(bubbles.length).toBe(12);
    const liquid = bubbles.find(row =>
      row.nativeElement.getAttribute('aria-label') === 'Explain minimum liquid percentage'
    );
    const stress = bubbles.find(row =>
      row.nativeElement.getAttribute('aria-label') === 'Explain maximum stress loss percentage'
    );
    expect(liquid?.injector.get(MatTooltip).message).toContain('excludes buying power');
    expect(stress?.injector.get(MatTooltip).message).toContain('not a forecast');
    expect(liquid?.nativeElement.tagName).toBe('BUTTON');
  });

  it('announces a before/after preview as non-persistent', async () => {
    const analysis = response();
    const api = apiStub(analysis);
    api.previewPortfolioChange.and.returnValue(of(previewResult(analysis)));
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'tastytrade');
    fixture.detectChanges();

    fixture.componentInstance.previewForm.symbol = 'SYNTH';
    fixture.componentInstance.previewForm.amountMode = 'NOTIONAL';
    fixture.componentInstance.previewForm.amount = 6_400;
    fixture.componentInstance.submitPreview();
    fixture.detectChanges();

    expect(api.previewPortfolioChange).toHaveBeenCalledWith('tastytrade', jasmine.objectContaining({
      account_id: 'synthetic-account', symbol: 'SYNTH', quantity: null,
      notional: 6_400, funding_source: 'ACCOUNT_CASH',
    }));
    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('Saved holdings and brokerage data were not changed');
    expect(text).toContain('Before');
    expect(text).toContain('After');
    expect(text).toContain('Not persisted');
    expect(text).toContain('Deployment pct');
  });

  it('shows a retryable unavailable state', async () => {
    const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['getPortfolioAnalysis']);
    api.getPortfolioAnalysis.and.returnValue(throwError(() => ({
      error: { detail: { code: 'PORTFOLIO_ANALYSIS_UNAVAILABLE', message: 'Capital facts unavailable.' } },
    })));
    await TestBed.configureTestingModule({
      imports: [PortfolioAnalysisComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(PortfolioAnalysisComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.detectChanges();

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(alert.textContent).toContain('Capital facts unavailable.');
    expect(alert.querySelector('button')?.textContent).toContain('Try again');
  });
});
