import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { BrokerageLedgerService } from '../../api/brokerage-ledger.service';
import { BrokerageLedgerSnapshot } from '../../model/brokerage-ledger';
import { BrokerageLedgerCombinedComponent } from './brokerage-ledger-combined.component';

const SNAPSHOT: BrokerageLedgerSnapshot = {
  schema_name: 'smallfish.brokerage-ledger',
  schema_version: 1,
  portfolio: { id: 'TRADING', label: 'Trading', brokerage: 'TASTYTRADE' },
  as_of: {
    positions: '2026-07-28T16:00:00Z', activity: '2026-07-28T16:00:00Z', market: '2026-07-28',
  },
  coverage: {
    open_equity: 'COMPLETE', closed_equity: 'UNAVAILABLE', options: 'INDICATIVE',
    history_start: '2026-01-01', reasons: ['Closed equity activity is not imported.'],
  },
  summary: {
    symbol_count: 1, incomplete_symbol_count: 1, equity_market_value: 12_000,
    option_market_value: -75, total_market_value: 11_925, total_pnl: null,
  },
  symbols: [{
    symbol: 'DEMO', exposure: 'EQUITY_AND_OPTIONS', state: 'OPEN', accounts: ['Trading'], shares: 100,
    current_price_per_share: 120, share_quantity: 100, equity_cost_per_share: 111,
    equity_cost: 11_100, current_equity: 12_000, equity_pnl: 900,
    equity_pnl_per_share: 9, net_credit: 600, net_debit: 0, option_pnl: null,
    net_pnl: null, option_adjusted_basis_per_share: null,
    cash_in: 600, cash_out: -11_100, net_cash_flow: -10_500, equity_market_value: 12_000,
    option_market_value: -75, open_market_value: 11_925, total_pnl: null,
    pnl_completeness: 'INDICATIVE',
    adjusted_basis: {
      realized_per_share: null, marked_per_share: null, history_start: '2026-01-01',
      completeness: 'UNAVAILABLE', reason: 'Option history is incomplete.',
    },
    annotations: [{
      scope: 'SYMBOL', kind: 'NOTE', text: 'Review after earnings', source: 'USER', updated_at: null,
    }],
    components: [{
      id: 'demo-call', account_id: 'trading', account: 'Trading', instrument: 'OPTION', side: 'SHORT',
      option_type: 'CALL', state: 'OPEN', quantity: -1, strike: 125, expiry: '2026-08-21',
      cash_in: 600, cash_out: 0, net_cash_flow: 600, mark_per_unit: 0.75,
      mark_observed_at: '2026-07-28T16:00:00Z', open_market_value: -75, realized_pnl: null,
      total_pnl: 525, pnl_completeness: 'INDICATIVE', cash_flow_basis: 'BROKER_ACTIVITY',
      open_leg_count: 1, event_count: 1, annotations: [], missing: ['closing activity'],
      provenance: {
        position_source: 'TASTYTRADE', activity_source: 'TASTYTRADE_ACTIVITY', market_source: 'TASTYTRADE_MARK',
        position_retrieved_at: '2026-07-28T16:00:00Z', activity_retrieved_at: '2026-07-28T16:00:00Z',
        mark_observed_at: '2026-07-28T16:00:00Z', mark_retrieved_at: '2026-07-28T16:01:00Z',
      },
    }],
  }],
  warnings: [{
    code: 'INCOMPLETE_HISTORY', scope: 'SYMBOL', symbol: 'DEMO', component_id: null,
    message: 'Option history is incomplete.',
  }],
};

describe('BrokerageLedgerCombinedComponent', () => {
  it('renders normalized summary fields and expands component provenance without calculating partial totals', async () => {
    const api = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', ['getCombined']);
    api.getCombined.and.returnValue(of({
      ...SNAPSHOT,
      symbols: [
        ...SNAPSHOT.symbols,
        { ...SNAPSHOT.symbols[0], symbol: 'EQUITY_ONLY', exposure: 'EQUITY' },
      ],
    }));
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerCombinedComponent],
      providers: [{ provide: BrokerageLedgerService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerCombinedComponent);
    fixture.componentRef.setInput('portfolio', 'trading');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('Trading option-adjusted basis');
    expect(text).toContain('DEMO');
    expect(text).not.toContain('EQUITY_ONLY');
    expect(text).toContain('Indicative');
    expect(text).toContain('—');
    const headers = Array.from(
      fixture.nativeElement.querySelectorAll('.combined-table th') as NodeListOf<HTMLElement>
    ).map(header => header.textContent?.replace(/\s+/g, ' ').trim());
    expect(headers).not.toContain('Exposure');
    expect(headers).not.toContain('Equity P/L / Share');
    expect(headers).toContain('Adjusted Basis / Share (Cost Price − Option P/L) / Share Qty');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    const detail = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(fixture.nativeElement.querySelector('.component-detail-row > td')?.getAttribute('colspan'))
      .toBe('12');
    expect(detail).toContain('Review after earnings');
    expect(detail).toContain('Short call');
    expect(detail).toContain('TASTYTRADE · TASTYTRADE_ACTIVITY · TASTYTRADE_MARK');
    expect(detail).toContain('Missing: closing activity');
  });

  it('shows a retryable error state', async () => {
    const api = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', ['getCombined']);
    api.getCombined.and.returnValue(throwError(() => ({ error: { detail: 'Combined artifact unavailable.' } })));
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerCombinedComponent],
      providers: [{ provide: BrokerageLedgerService, useValue: api }],
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerCombinedComponent);
    fixture.componentRef.setInput('portfolio', 'retirement');
    fixture.detectChanges();

    const error = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(error.textContent).toContain('Combined artifact unavailable.');
    expect(error.querySelector('button')?.textContent).toContain('Try again');
  });
});
