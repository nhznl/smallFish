import { TestBed } from '@angular/core/testing';

import {
  BrokerRiskMetric,
  BrokerRiskPositionsComponent,
  BrokerRiskRow,
} from './broker-risk-positions.component';

const ROWS: BrokerRiskRow[] = [{
  id: 'stock-row', account: 'DEMO', wheel_id: '', symbol: 'DEMO', trade_type: 'STOCK',
  qty: 500, strike: null, expiry: '', mark_price: 42, mark_retrieved_at: null,
  status: 'OPEN', non_standard: false,
}, {
  id: 'option-row', account: 'DEMO', wheel_id: 'DEMO 2026', symbol: 'DEMO',
  trade_type: 'SHORT_PUT', qty: 1, strike: 40, expiry: '2026-08-21',
  mark_price: 1.25, mark_retrieved_at: '2026-07-28T16:00:00Z', status: 'OPEN',
  non_standard: false, dte_remaining: 24, current_underlying_price: 42,
  percent_to_strike: 5,
}];

const METRICS: BrokerRiskMetric[] = [{
  row_id: 'option-row', vol_annual: 0.32, vol_as_of: '2026-07-28T16:00:00Z',
  delta_source: 'TASTYTRADE_IV', delta_shares: -28,
}];

describe('BrokerRiskPositionsComponent', () => {
  it('renders the normalized options-only columns and excludes stock rows', async () => {
    await TestBed.configureTestingModule({ imports: [BrokerRiskPositionsComponent] })
      .compileComponents();
    const fixture = TestBed.createComponent(BrokerRiskPositionsComponent);
    fixture.componentRef.setInput('rows', ROWS);
    fixture.componentRef.setInput('metrics', METRICS);
    fixture.detectChanges();

    const headers = Array.from(
      fixture.nativeElement.querySelectorAll('thead th') as NodeListOf<HTMLTableCellElement>
    ).map(header => header.textContent?.replace(/\s+/g, ' ').trim());
    expect(headers).toEqual([
      'Account / group', 'Symbol', 'Leg', 'Qty', 'Strike', 'Expiry / DTE',
      'Current option price', 'Spot / to strike ▲', 'IV / source', 'Delta shares',
    ]);

    const bodyRows = fixture.nativeElement.querySelectorAll('tbody tr') as NodeListOf<HTMLTableRowElement>;
    expect(bodyRows.length).toBe(1);
    expect(bodyRows[0].textContent).toContain('SHORT_PUT');
    expect(bodyRows[0].textContent).not.toContain('STOCK');
    expect(headers).not.toContain('Beta');
    expect(headers).not.toContain('Tasty Beta');
    expect(headers).not.toContain('Beta-delta $');
    expect(headers).not.toContain('Risk status');
  });
});
