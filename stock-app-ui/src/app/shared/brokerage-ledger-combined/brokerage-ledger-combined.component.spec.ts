import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import { AdjustedBasisItem, AdjustedBasisResponse, BrokerageComponent } from '../../model/brokerage';
import { BrokerageLedgerCombinedComponent } from './brokerage-ledger-combined.component';

const PROVENANCE = {
  position_source: 'BROKER', activity_source: 'BROKER_ACTIVITY', market_source: 'BROKER_MARK',
  position_retrieved_at: '2026-07-28T16:00:00Z', activity_retrieved_at: '2026-07-28T16:00:00Z',
  mark_observed_at: '2026-07-28T16:00:00Z', mark_retrieved_at: '2026-07-28T16:01:00Z',
};

function component(overrides: Partial<BrokerageComponent>): BrokerageComponent {
  return {
    id: 'demo-equity', account_id: 'retirement', account: 'Retirement', instrument: 'EQUITY',
    symbol: 'DEMO', side: 'LONG', option_type: null, state: 'OPEN', quantity: 100,
    strike: null, expiry: null, contract_key: null, cash_in: 0, cash_out: -11_100,
    net_cash_flow: -11_100, mark_per_unit: 120, mark_observed_at: '2026-07-28T16:00:00Z',
    open_market_value: 12_000, realized_pnl: null, total_pnl: 900,
    pnl_completeness: 'INDICATIVE', cash_flow_basis: 'POSITION_COST_BASIS',
    open_leg_count: 1, event_count: 0, provenance: PROVENANCE, missing: [],
    ...overrides,
  };
}

function item(symbol: string, overrides: Partial<AdjustedBasisItem> = {}): AdjustedBasisItem {
  return {
    symbol, accounts: ['Retirement'], share_quantity: 100, equity_cost: 11_100,
    equity_cost_per_share: 111, current_equity: 12_000, equity_pnl: 900,
    option_market_value: -75, option_pnl: 525, net_pnl: 1_425, pnl_completeness: 'INDICATIVE',
    adjusted_basis: {
      realized_per_share: null, marked_per_share: null, completeness: 'UNAVAILABLE',
      reason: 'Option P/L cannot be allocated safely across accounts.',
    },
    components: [
      component({ id: `${symbol}-equity`, symbol }),
      component({
        id: `${symbol}-put`, symbol, instrument: 'OPTION', side: 'SHORT', option_type: 'PUT',
        quantity: -1, strike: 100, expiry: '2026-08-21', contract_key: `${symbol}-put`, cash_in: 600,
        cash_out: 0, net_cash_flow: 600, mark_per_unit: 0.75, open_market_value: -75,
        realized_pnl: null, total_pnl: 525, cash_flow_basis: 'BROKER_ACTIVITY', open_leg_count: 1,
        event_count: 1, missing: ['OPTION_ACTIVITY_HISTORY'],
      }),
    ],
    ...overrides,
  };
}

function response(items: AdjustedBasisItem[]): AdjustedBasisResponse {
  return {
    schema_name: 'smallfish.brokerage-option-adjusted-basis', schema_version: 1,
    brokerage: { id: 'fidelity', label: 'Fidelity', institution: 'FIDELITY', portfolio_role: 'RETIREMENT' },
    availability: { status: 'AVAILABLE', reasons: [] },
    as_of: {
      positions: '2026-07-28T16:00:00Z', activity: '2026-07-28T16:00:00Z', market: '2026-07-28',
    },
    coverage: {
      status: 'INDICATIVE', history_start: '2026-01-01', equity_activity: 'INDICATIVE',
      option_activity: 'INDICATIVE', reached_provider_boundary: null, reasons: [],
    },
    summary: { symbol_count: items.length, incomplete_symbol_count: 0, net_pnl: null, pnl_completeness: 'INDICATIVE' },
    items, warnings: [],
  };
}

describe('BrokerageLedgerCombinedComponent', () => {
  it('uses the brokerage-neutral adjusted-basis response and counts only genuine unavailable rows', async () => {
    const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['getOptionAdjustedBasis']);
    api.getOptionAdjustedBasis.and.returnValue(of(response([
      item('UNAVAILABLE'),
      item('READY', {
        adjusted_basis: {
          realized_per_share: null, marked_per_share: 105, completeness: 'INDICATIVE', reason: null,
        },
      }),
      item('EQUITY_FLAT', {
        components: [
          component({ id: 'EQUITY_FLAT-equity', symbol: 'EQUITY_FLAT', state: 'FLAT', quantity: 0 }),
          component({
            id: 'EQUITY_FLAT-put', symbol: 'EQUITY_FLAT', instrument: 'OPTION', side: 'SHORT',
            option_type: 'PUT', quantity: -1, strike: 100, expiry: '2026-08-21', contract_key: 'flat-put',
          }),
        ],
      }),
    ])));
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerCombinedComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerCombinedComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(api.getOptionAdjustedBasis).toHaveBeenCalledWith('fidelity');
    expect(text).toContain('Fidelity option-adjusted basis');
    expect(text).toContain('UNAVAILABLE');
    expect(text).toContain('READY');
    expect(text).not.toContain('EQUITY_FLAT');
    expect(text).toContain('Basis unavailable');
    expect(text).toContain('Option P/L cannot be allocated safely across accounts.');
    const unavailableCard = Array.from(
      fixture.nativeElement.querySelectorAll('.combined-stats .stat-card') as NodeListOf<HTMLElement>
    ).find(card => card.querySelector('.stat-label')?.textContent === 'Basis unavailable');
    expect(unavailableCard?.querySelector('.stat-value')?.textContent?.trim()).toBe('1');
    const headers = Array.from(
      fixture.nativeElement.querySelectorAll('.combined-table th') as NodeListOf<HTMLElement>
    ).map(header => header.textContent?.replace(/\s+/g, ' ').trim());
    expect(headers).not.toContain('Equity P/L / Share');
    expect(headers).toContain('Adjusted Basis / Share (Cost Price − Option P/L) / Share Qty');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.component-detail-row > td')?.getAttribute('colspan'))
      .toBe('11');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('OPTION_ACTIVITY_HISTORY');
  });

  it('shows a retryable error state', async () => {
    const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['getOptionAdjustedBasis']);
    api.getOptionAdjustedBasis.and.returnValue(throwError(() => ({ error: { detail: 'Adjusted basis unavailable.' } })));
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerCombinedComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerCombinedComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.detectChanges();

    const error = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(error.textContent).toContain('Adjusted basis unavailable.');
    expect(error.querySelector('button')?.textContent).toContain('Try again');
  });
});
