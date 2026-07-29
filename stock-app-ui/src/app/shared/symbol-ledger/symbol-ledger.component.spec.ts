import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import {
  BrokerageId,
  SymbolLedgerDetailResponse,
  SymbolLedgerListResponse,
  SymbolLedgerSummary,
} from '../../model/brokerage';
import { SymbolLedgerComponent } from './symbol-ledger.component';

/** Both brokerages are driven through the same fixtures on purpose: if the
 *  component ever needed different data for one of them, that would be the
 *  branch this whole migration exists to remove. */
const BROKERAGES: { id: BrokerageId; label: string; institution: string; role: string }[] = [
  { id: 'tastytrade', label: 'Tastytrade', institution: 'TASTYTRADE', role: 'TRADING' },
  { id: 'fidelity', label: 'Fidelity', institution: 'FIDELITY', role: 'RETIREMENT' },
];

function summary(overrides: Partial<SymbolLedgerSummary> = {}): SymbolLedgerSummary {
  return {
    symbol: 'DEMO',
    state: 'ACTIVE',
    reconciliation_status: 'RECONCILED',
    pnl_completeness: 'INDICATIVE',
    accounts: ['Main'],
    exposure: 'EQUITY_AND_OPTIONS',
    current_period: {
      period_version: 'v1:abc', started_at: '2026-01-15T15:30:00Z', event_count: 6,
      first_event_at: '2026-01-15T15:30:00Z', last_event_at: '2026-07-28T15:30:00Z',
      net_cash_flow: -10_500, open_market_value: 11_925, total_pnl: 1425, realized_pnl: null,
    },
    archived_period_count: 0,
    archived_pnl: 0,
    lifetime_pnl: 1425,
    notes: '',
    warnings: [],
    ...overrides,
  };
}

function listResponse(
  brokerage: (typeof BROKERAGES)[number],
  items: SymbolLedgerSummary[] = [summary()]
): SymbolLedgerListResponse {
  return {
    schema_name: 'smallfish.symbol-ledger-list',
    schema_version: 1,
    brokerage: {
      id: brokerage.id, label: brokerage.label,
      institution: brokerage.institution, portfolio_role: brokerage.role,
    },
    availability: { status: 'AVAILABLE', reasons: [] },
    as_of: { positions: '2026-07-28T16:00:00Z', activity: '2026-07-28T16:00:00Z', market: '2026-07-28' },
    coverage: {
      status: 'INDICATIVE', history_start: '2026-01-01', equity_activity: 'UNAVAILABLE',
      option_activity: 'COMPLETE', reached_provider_boundary: null,
      reasons: ['Closed equity activity is not imported for this brokerage.'],
    },
    summary: {
      symbol_count: items.length, active_count: 1, archived_count: 0,
      needs_review_count: items.filter(row => row.warnings.length).length, lifetime_pnl: 1425,
    },
    items,
    warnings: [],
  };
}

function detailResponse(
  brokerage: (typeof BROKERAGES)[number],
  overrides: Partial<SymbolLedgerSummary> = {}
): SymbolLedgerDetailResponse {
  const base = summary(overrides);
  const list = listResponse(brokerage);
  return {
    schema_name: 'smallfish.symbol-ledger',
    schema_version: 1,
    brokerage: list.brokerage,
    availability: list.availability,
    as_of: list.as_of,
    coverage: list.coverage,
    symbol: {
      ...base,
      reset_eligible: false,
      reset_blockers: ['SYMBOL_NOT_FLAT'],
      event_count_total: 6,
      archives: [],
      components: [{
        id: `${brokerage.id}:main:OPTION:DEMO 260821P00050000`,
        account_id: 'main', account: 'Main', instrument: 'OPTION', symbol: 'DEMO',
        side: 'SHORT', option_type: 'PUT', state: 'OPEN', quantity: -1, strike: 50,
        expiry: '2026-08-21', contract_key: 'DEMO 260821P00050000',
        cash_in: 600, cash_out: 0, net_cash_flow: 600, mark_per_unit: 0.75,
        mark_observed_at: '2026-07-28T16:00:00Z', open_market_value: -75,
        realized_pnl: null, total_pnl: 525, pnl_completeness: 'INDICATIVE',
        cash_flow_basis: 'BROKER_ACTIVITY', open_leg_count: 1, event_count: 1,
        missing: [],
        provenance: {
          position_source: brokerage.institution, activity_source: brokerage.institution,
          market_source: brokerage.institution,
          position_retrieved_at: '2026-07-28T16:00:00Z',
          activity_retrieved_at: '2026-07-28T16:00:00Z',
          mark_observed_at: '2026-07-28T16:00:00Z', mark_retrieved_at: '2026-07-28T16:01:00Z',
        },
      }],
    },
    warnings: [],
  };
}

function spyApi() {
  return jasmine.createSpyObj<BrokerageService>('BrokerageService', [
    'listSymbols', 'getSymbol', 'updateSymbolNotes',
  ]);
}

async function mount(api: jasmine.SpyObj<BrokerageService>, brokerageId: BrokerageId) {
  await TestBed.configureTestingModule({
    imports: [SymbolLedgerComponent],
    providers: [{ provide: BrokerageService, useValue: api }],
  }).compileComponents();
  const fixture = TestBed.createComponent(SymbolLedgerComponent);
  fixture.componentRef.setInput('brokerageId', brokerageId);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: { nativeElement: HTMLElement }): string {
  return fixture.nativeElement.textContent?.replace(/\s+/g, ' ') ?? '';
}

describe('SymbolLedgerComponent', () => {
  beforeEach(() => TestBed.resetTestingModule());

  for (const brokerage of BROKERAGES) {
    describe(brokerage.label, () => {
      it('renders one row per symbol with derived lifecycle and no group controls', async () => {
        const api = spyApi();
        api.listSymbols.and.returnValue(of(listResponse(brokerage)));
        const fixture = await mount(api, brokerage.id);

        expect(api.listSymbols).toHaveBeenCalledWith(brokerage.id, { state: 'active' });
        const body = text(fixture);
        expect(body).toContain('Symbol Ledger');
        expect(body).toContain('DEMO');
        expect(body).toContain('Active');
        expect(body).toContain('Equity + options');
        // The concepts the migration removes must not reappear in the UI.
        expect(body).not.toContain('Trade Groups');
        expect(body).not.toContain('Group Name');
        expect(body).not.toContain('Ungrouped');
        // Nor may a component name the provider behind the brokerage.
        expect(body.toLowerCase()).not.toContain('snaptrade');
      });

      it('shows retained-history coverage next to lifetime P/L', async () => {
        const api = spyApi();
        api.listSymbols.and.returnValue(of(listResponse(brokerage)));
        const fixture = await mount(api, brokerage.id);

        const body = text(fixture);
        expect(body).toContain('Lifetime P/L');
        expect(body).toContain('Begins 2026-01-01');
        expect(body).toContain('Lifetime totals cover this retained history only.');
      });

      it('opens a symbol detail with account-aware components and provenance', async () => {
        const api = spyApi();
        api.listSymbols.and.returnValue(of(listResponse(brokerage)));
        api.getSymbol.and.returnValue(of(detailResponse(brokerage)));
        const fixture = await mount(api, brokerage.id);

        (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
        fixture.detectChanges();

        const body = text(fixture);
        expect(api.getSymbol).toHaveBeenCalledWith(brokerage.id, 'DEMO');
        expect(body).toContain('Positions and contracts');
        expect(body).toContain('Short put');
        expect(body).toContain('Main');
        expect(body).toContain(brokerage.institution);
        expect(body).toContain('not brokerage tax-lot or taxable realized P/L');
      });
    });
  }

  it('renders an unavailable value as an em dash rather than zero', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0], [summary({
      pnl_completeness: 'UNAVAILABLE',
      reconciliation_status: 'UNRECONCILED',
      current_period: {
        ...summary().current_period,
        net_cash_flow: 600, open_market_value: null, total_pnl: null, realized_pnl: null,
      },
      lifetime_pnl: null,
      warnings: ['Imported activity does not reconcile with the broker position.'],
    })])));
    const fixture = await mount(api, 'tastytrade');

    const body = text(fixture);
    expect(body).toContain('Unreconciled');
    expect(body).toContain('Unavailable');
    expect(body).toContain('—');
    expect(body).toContain('does not reconcile');
    // A row whose flatness is unproven is flagged structurally, not by colour.
    expect(fixture.nativeElement.querySelector('.needs-review')).toBeTruthy();
  });

  it('refetches when the lifecycle filter changes', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0], [])));
    const fixture = await mount(api, 'tastytrade');

    const archived = Array.from(
      fixture.nativeElement.querySelectorAll('.tab-bar .tab') as NodeListOf<HTMLButtonElement>
    ).find(tab => tab.textContent?.includes('Archived'))!;
    archived.click();
    fixture.detectChanges();

    expect(api.listSymbols).toHaveBeenCalledWith('tastytrade', { state: 'archived' });
    expect(text(fixture)).toContain('No symbol is confidently flat yet.');
  });

  it('filters client-side by symbol, account, or note', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0], [
      summary(), summary({ symbol: 'OTHER', notes: 'watch assignment' }),
    ])));
    const fixture = await mount(api, 'tastytrade');
    const component = fixture.componentInstance;

    component.search = 'assignment';
    fixture.detectChanges();
    expect(component.rows().map(row => row.symbol)).toEqual(['OTHER']);

    component.search = 'nothing-matches';
    fixture.detectChanges();
    expect(text(fixture)).toContain('No symbol matches your search');
  });

  it('saves a note and leaves every other field derived', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detailResponse(BROKERAGES[0])));
    api.updateSymbolNotes.and.returnValue(of(
      detailResponse(BROKERAGES[0], { notes: 'watch assignment history' })
    ));
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.noteDirty).toBeFalse();
    component.noteDraft = 'watch assignment history';
    expect(component.noteDirty).toBeTrue();

    component.saveNote();
    fixture.detectChanges();

    expect(api.updateSymbolNotes).toHaveBeenCalledWith(
      'tastytrade', 'DEMO', 'watch assignment history'
    );
    expect(text(fixture)).toContain('Note saved.');
    // The list row picks up the saved note without a second round trip.
    expect(component.data!.items[0].notes).toBe('watch assignment history');
  });

  it('shows a skeleton while loading', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(new Promise(() => {}) as never);
    await TestBed.configureTestingModule({
      imports: [SymbolLedgerComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(SymbolLedgerComponent);
    fixture.componentInstance.loading = true;
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[aria-busy="true"]')).toBeTruthy();
  });

  it('surfaces a safe API error code with a retry', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(throwError(() => ({
      error: { detail: { code: 'UNKNOWN_BROKERAGE', message: 'That brokerage is not configured.' } },
    })));
    const fixture = await mount(api, 'fidelity');

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(alert.textContent).toContain('That brokerage is not configured.');
    expect(alert.querySelector('button')?.textContent).toContain('Try again');
  });

  it('explains an unsynced brokerage instead of showing an empty table', async () => {
    const api = spyApi();
    const body = listResponse(BROKERAGES[1], []);
    body.availability = {
      status: 'UNAVAILABLE', reasons: ['No holdings snapshot has been synced yet.'],
    };
    api.listSymbols.and.returnValue(of(body));
    const fixture = await mount(api, 'fidelity');

    expect(text(fixture)).toContain('Nothing imported yet.');
    expect(text(fixture)).toContain('No holdings snapshot has been synced yet.');
  });

  it('keeps the table horizontally scrollable rather than compressing it', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    const fixture = await mount(api, 'tastytrade');

    expect(fixture.nativeElement.querySelector('.table-shell .ledger-table')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('.sticky-symbol')).toBeTruthy();
  });
});
