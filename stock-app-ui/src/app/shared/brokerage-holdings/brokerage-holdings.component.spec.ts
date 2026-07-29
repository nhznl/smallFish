import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import { BrokerageId, HoldingItem, HoldingsResponse } from '../../model/brokerage';
import { BrokerageHoldingsComponent } from './brokerage-holdings.component';

const LONG_NOTE = 'A deliberately long test note that should remain available while being visually truncated in the holdings table';

/** Both brokerages run through the same fixtures: needing different data for
 *  one of them would be exactly the branch this migration removes. */
const BROKERAGES: { id: BrokerageId; label: string; institution: string; role: string }[] = [
  { id: 'tastytrade', label: 'Tastytrade', institution: 'TASTYTRADE', role: 'TRADING' },
  { id: 'fidelity', label: 'Fidelity', institution: 'FIDELITY', role: 'RETIREMENT' },
];

function holding(overrides: Partial<HoldingItem> = {}): HoldingItem {
  return {
    id: 'acct-1:EQUITY:DEMO',
    account_id: 'acct-1',
    account: 'Main',
    instrument: 'EQUITY',
    symbol: 'DEMO',
    side: 'LONG',
    option_type: null,
    state: 'OPEN',
    quantity: 10,
    strike: null,
    expiry: null,
    contract_key: null,
    cash_in: 0,
    cash_out: -1000,
    net_cash_flow: -1000,
    mark_per_unit: 90,
    mark_observed_at: '2026-07-28T16:00:00Z',
    open_market_value: 900,
    realized_pnl: null,
    total_pnl: -100,
    pnl_completeness: 'INDICATIVE',
    cash_flow_basis: 'POSITION_COST_BASIS',
    open_leg_count: 1,
    event_count: 1,
    provenance: {
      position_source: 'BROKER', activity_source: null, market_source: 'BROKER',
      position_retrieved_at: '2026-07-28T16:00:00Z', activity_retrieved_at: null,
      mark_observed_at: '2026-07-28T16:00:00Z', mark_retrieved_at: '2026-07-28T16:00:00Z',
    },
    missing: [],
    category: 'GROWTH',
    industry: 'SOFTWARE',
    note: LONG_NOTE,
    metadata_updated_at: '2026-07-28T00:00:00Z',
    cost_basis: 1000,
    cost_per_unit: 100,
    market_value: 900,
    unrealized_pnl: -100,
    unrealized_pnl_pct: -10,
    pct_of_total: 75,
    trend: {
      alert: true, peak_pct: 5, peak_at: '2026-07-20T16:00:00Z', drop_pct: 12,
      from_pct: 5, to_pct: -10, alert_at: '2026-07-28T16:00:00Z', direction: 'LOSS',
    },
    gain_loss_snapshots: { '2026-07-27': -8 },
    ...overrides,
  };
}

const SECOND = holding({
  id: 'acct-2:EQUITY:OTHER', account_id: 'acct-2', account: 'IRA', symbol: 'OTHER',
  category: 'DIV', industry: 'ENERGY', note: '', quantity: 6, cost_basis: 300,
  cost_per_unit: 50, mark_per_unit: 50, market_value: 300, unrealized_pnl: 0,
  unrealized_pnl_pct: 0, pct_of_total: 25, gain_loss_snapshots: {},
  trend: {
    alert: false, peak_pct: 0, peak_at: '2026-07-28T16:00:00Z', drop_pct: null,
    from_pct: null, to_pct: null, alert_at: null, direction: 'GAIN',
  },
});

function response(
  brokerage: (typeof BROKERAGES)[number],
  items: HoldingItem[] = [holding(), SECOND]
): HoldingsResponse {
  return {
    schema_name: 'smallfish.brokerage-holdings',
    schema_version: 1,
    brokerage: {
      id: brokerage.id, label: brokerage.label,
      institution: brokerage.institution, portfolio_role: brokerage.role,
    },
    availability: { status: 'AVAILABLE', reasons: [] },
    as_of: {
      positions: '2026-07-28T16:00:00Z', activity: null, market: '2026-07-28',
    },
    coverage: {
      status: 'INDICATIVE', history_start: null, equity_activity: 'UNAVAILABLE',
      option_activity: 'COMPLETE', reached_provider_boundary: null, reasons: [],
    },
    summary: {
      holding_count: items.length,
      account_count: new Set(items.map(item => item.account_id)).size,
      total_cost_basis: 1300,
      total_market_value: 1200,
      total_unrealized_pnl: -100,
      total_unrealized_pnl_pct: -7.69,
      gain_loss_snapshots: [{
        sync_date: '2026-07-27', retrieved_at: '2026-07-27T16:00:00Z',
        captured_at: '2026-07-27T17:00:00Z',
      }],
      pnl_completeness: 'INDICATIVE',
    },
    items,
    warnings: [],
  } as HoldingsResponse;
}

function stub(): jasmine.SpyObj<BrokerageService> {
  return jasmine.createSpyObj<BrokerageService>('BrokerageService', [
    'getHoldings', 'captureGainLossSnapshot', 'updateHoldingsMetadata',
  ]);
}

async function mount(api: jasmine.SpyObj<BrokerageService>, brokerageId: BrokerageId) {
  await TestBed.configureTestingModule({
    imports: [BrokerageHoldingsComponent],
    providers: [{ provide: BrokerageService, useValue: api }],
  }).compileComponents();
  const fixture = TestBed.createComponent(BrokerageHoldingsComponent);
  fixture.componentRef.setInput('brokerageId', brokerageId);
  fixture.detectChanges();
  return fixture;
}

describe('BrokerageHoldingsComponent', () => {
  afterEach(() => TestBed.resetTestingModule());

  for (const brokerage of BROKERAGES) {
    it(`renders filters, snapshots, declining state, and editable notes for ${brokerage.id}`,
      async () => {
        const api = stub();
        api.getHoldings.and.returnValue(of(response(brokerage)));
        const fixture = await mount(api, brokerage.id);

        expect(api.getHoldings).toHaveBeenCalledWith(brokerage.id);
        const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
        expect(text).toContain(`${brokerage.label} holdings`);
        expect(text).toContain('All categories');
        expect(text).toContain('All accounts');
        expect(text).toContain('Declining only (1)');
        expect(text).toContain('Snapshot G/L %');
        expect(text).toContain('Copy Symbols');
        expect(text).toContain('G/L % as of Jul 27, 2026');
        // The label comes from the response, never from the brokerage id.
        expect(text).not.toContain('SNAPTRADE');

        const noteButton = fixture.nativeElement.querySelector('.note-button') as HTMLButtonElement;
        const noteStyles = getComputedStyle(noteButton);
        expect(noteStyles.overflow).toBe('hidden');
        expect(noteStyles.textOverflow).toBe('ellipsis');
        expect(noteStyles.whiteSpace).toBe('nowrap');
        expect(noteButton.textContent?.trim()).toBe(LONG_NOTE);

        noteButton.click();
        fixture.detectChanges();
        await fixture.whenStable();
        fixture.detectChanges();
        expect((fixture.nativeElement as HTMLElement).textContent).toContain('Classify DEMO');
        expect((fixture.nativeElement.querySelector('.modal-note') as HTMLTextAreaElement).value)
          .toBe(LONG_NOTE);
      });
  }

  it('hides category and account filters when each has only one choice', async () => {
    const api = stub();
    api.getHoldings.and.returnValue(of(response(BROKERAGES[0], [holding()])));
    const fixture = await mount(api, 'tastytrade');

    const options = Array.from(
      fixture.nativeElement.querySelectorAll('.holdings-filters option') as NodeListOf<HTMLOptionElement>
    ).map(option => option.textContent?.trim());
    expect(options).not.toContain('All categories');
    expect(options).not.toContain('All accounts');
  });

  it('renders the portfolio share and return the API computed', async () => {
    const api = stub();
    api.getHoldings.and.returnValue(of(response(BROKERAGES[0])));
    const fixture = await mount(api, 'tastytrade');

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('−7.69%');    // Return card
    expect(text).toContain('75.00%');    // % Portfolio, first row
  });

  it('renders a missing value as an em dash rather than zero', async () => {
    const api = stub();
    api.getHoldings.and.returnValue(of(response(BROKERAGES[0], [holding({
      mark_per_unit: null, market_value: null, unrealized_pnl: null,
      unrealized_pnl_pct: null, pct_of_total: null,
    })])));
    const fixture = await mount(api, 'tastytrade');

    const cells = Array.from(
      fixture.nativeElement.querySelectorAll('tbody td.num') as NodeListOf<HTMLElement>
    ).map(cell => cell.textContent?.trim());
    expect(cells).toContain('—');
  });

  it('captures a snapshot through the common client and reloads', async () => {
    const api = stub();
    api.getHoldings.and.returnValue(of(response(BROKERAGES[1])));
    api.captureGainLossSnapshot.and.returnValue(of({
      schema_name: 'smallfish.brokerage-holdings-snapshot', schema_version: 1,
      brokerage_id: 'fidelity' as BrokerageId, sync_date: '2026-07-28',
      retrieved_at: '2026-07-28T16:00:00Z', captured_at: '2026-07-28T17:00:00Z',
      replaced: false, holding_count: 2, retained_dates: ['2026-07-28'],
    }));
    const fixture = await mount(api, 'fidelity');

    const button = Array.from(
      fixture.nativeElement.querySelectorAll('.toolbar-actions .btn') as NodeListOf<HTMLButtonElement>
    ).find(candidate => candidate.textContent?.includes('Snapshot'))!;
    button.click();
    fixture.detectChanges();

    expect(api.captureGainLossSnapshot).toHaveBeenCalledWith('fidelity');
    expect(api.getHoldings).toHaveBeenCalledTimes(2);
    expect((fixture.nativeElement as HTMLElement).textContent)
      .toContain('G/L snapshot for Jul 28, 2026 saved.');
  });

  it('saves a classification through the common metadata endpoint', async () => {
    const api = stub();
    api.getHoldings.and.returnValue(of(response(BROKERAGES[0])));
    api.updateHoldingsMetadata.and.returnValue(of({
      schema_name: 'smallfish.brokerage-holdings-metadata', schema_version: 1,
      brokerage_id: 'tastytrade' as BrokerageId,
      metadata: { symbol: 'DEMO', category: 'VALUE', industry: 'SOFTWARE', note: '' },
    }));
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.note-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    fixture.componentInstance.editing!.category = 'VALUE';
    fixture.componentInstance.saveEnrichment();

    expect(api.updateHoldingsMetadata).toHaveBeenCalledWith(
      'tastytrade', 'DEMO',
      { category: 'VALUE', industry: 'SOFTWARE', note: LONG_NOTE }
    );
  });

  it('says nothing is imported rather than showing an empty portfolio', async () => {
    const api = stub();
    const body = response(BROKERAGES[1], []);
    body.availability = {
      status: 'UNAVAILABLE', reasons: ['No brokerage positions have been imported.'],
    };
    api.getHoldings.and.returnValue(of(body));
    const fixture = await mount(api, 'fidelity');

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Nothing imported yet.');
    expect(text).toContain('No brokerage positions have been imported.');
  });
});
