import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import {
  ArchiveCreatedResponse,
  BrokerageId,
  LedgerEventsResponse,
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
      symbol_count: items.length, active_count: 1, closed_count: 0,
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

function eventsResponse(
  brokerage = BROKERAGES[0],
  overrides: Partial<LedgerEventsResponse> = {}
): LedgerEventsResponse {
  return {
    schema_name: 'smallfish.symbol-ledger-events', schema_version: 1,
    brokerage: {
      id: brokerage.id, label: brokerage.label,
      institution: brokerage.institution, portfolio_role: brokerage.role,
    },
    symbol: 'DEMO', period: 'current', total_event_count: 1,
    items: [{
      provider_event_id: 'event-1', account_id: 'main', account: 'Main', symbol: 'DEMO',
      instrument: 'OPTION', contract_key: 'DEMO 260821P00050000', option_type: 'PUT',
      strike: 50, expiry: '2026-08-21', action: 'SELL_TO_OPEN', quantity_delta: -1,
      net_cash_flow: 600, fees: -1, executed_at: '2026-07-28T16:00:00Z',
      imported_at: '2026-07-28T16:01:00Z', source: brokerage.institution,
      is_manual_reconciliation: false, missing: [],
    }],
    next_cursor: null, has_more: false,
    ...overrides,
  };
}

function archiveCreatedResponse(
  brokerage = BROKERAGES[0]
): ArchiveCreatedResponse {
  const detail = detailResponse(brokerage).symbol;
  return {
    schema_name: 'smallfish.symbol-ledger-archive-created', schema_version: 1,
    archive: {
      archive_id: 'archive-1', symbol: 'DEMO', period_started_at: '2026-01-15T15:30:00Z',
      period_ended_at: '2026-07-28T16:00:00Z', event_count: 6, realized_pnl: 1425,
      pnl_completeness: 'COMPLETE', verification_status: 'VERIFIED',
      created_at: '2026-07-28T16:01:00Z', note: '', warnings: [],
    },
    symbol: {
      ...detail, state: 'CLOSED', reset_eligible: false,
      reset_blockers: ['PERIOD_EMPTY'], archived_period_count: 1,
      archives: [{
        archive_id: 'archive-1', symbol: 'DEMO', period_started_at: '2026-01-15T15:30:00Z',
        period_ended_at: '2026-07-28T16:00:00Z', event_count: 6, realized_pnl: 1425,
        pnl_completeness: 'COMPLETE', verification_status: 'VERIFIED',
        created_at: '2026-07-28T16:01:00Z', note: '', warnings: [],
      }],
    },
  };
}

function spyApi() {
  const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', [
    'listSymbols', 'getSymbol', 'updateSymbolNotes', 'getSymbolEvents', 'createArchive',
  ]);
  api.getSymbolEvents.and.returnValue(of(eventsResponse()));
  return api;
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

        expect(api.listSymbols).toHaveBeenCalledWith(
          brokerage.id, { state: 'active', exposure: 'options' }
        );
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

      it('shows option symbols and option symbols with equity, but not equity-only holdings', async () => {
        const api = spyApi();
        api.listSymbols.and.returnValue(of(listResponse(brokerage, [
          summary({ symbol: 'OPTION_ONLY', exposure: 'OPTIONS' }),
          summary({ symbol: 'OPTION_WITH_EQUITY', exposure: 'EQUITY_AND_OPTIONS' }),
          summary({ symbol: 'EQUITY_ONLY', exposure: 'EQUITY' }),
        ])));
        const fixture = await mount(api, brokerage.id);

        expect(fixture.componentInstance.rows().map(row => row.symbol)).toEqual([
          'OPTION_ONLY', 'OPTION_WITH_EQUITY',
        ]);
        const body = text(fixture);
        expect(body).toContain('OPTION_ONLY');
        expect(body).toContain('OPTION_WITH_EQUITY');
        expect(body).not.toContain('EQUITY_ONLY');
        expect(fixture.nativeElement.querySelector('.result-count')?.textContent)
          .toContain('2 of 2');
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

      it('hides archive controls when the current period has no events', async () => {
        const api = spyApi();
        const emptyPeriod = {
          ...summary().current_period,
          started_at: null,
          event_count: 0,
          first_event_at: null,
          last_event_at: null,
          net_cash_flow: 0,
          realized_pnl: 0,
        };
        api.listSymbols.and.returnValue(of(listResponse(brokerage, [summary({ current_period: emptyPeriod })])));
        api.getSymbol.and.returnValue(of(detailResponse(brokerage, { current_period: emptyPeriod })));
        const fixture = await mount(api, brokerage.id);

        (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
        fixture.detectChanges();

        expect(fixture.nativeElement.querySelector('.archive-control')).toBeNull();
        expect(text(fixture)).not.toContain('Not ready to archive');
      });

      it('shows archive blockers when the current period has activity', async () => {
        const api = spyApi();
        api.listSymbols.and.returnValue(of(listResponse(brokerage)));
        api.getSymbol.and.returnValue(of(detailResponse(brokerage)));
        const fixture = await mount(api, brokerage.id);

        (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
        fixture.detectChanges();

        expect(fixture.nativeElement.querySelector('.archive-control')).toBeTruthy();
        expect(text(fixture)).toContain('Not ready to archive');
        expect(text(fixture)).toContain('Open exposure remains, so this symbol is still active.');
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

    const closed = Array.from(
      fixture.nativeElement.querySelectorAll('.tab-bar .tab') as NodeListOf<HTMLButtonElement>
    ).find(tab => tab.textContent?.includes('Closed'))!;
    closed.click();
    fixture.detectChanges();

    expect(api.listSymbols).toHaveBeenCalledWith(
      'tastytrade', { state: 'closed', exposure: 'options' }
    );
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

  it('loads more immutable history with the API cursor', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detailResponse(BROKERAGES[0])));
    api.getSymbolEvents.and.returnValues(
      of(eventsResponse(BROKERAGES[0], { next_cursor: 'page-2', has_more: true })),
      of(eventsResponse(BROKERAGES[0], {
        items: [{ ...eventsResponse().items[0], provider_event_id: 'event-2' }],
        next_cursor: null, has_more: false,
      })),
    );
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.getSymbolEvents).toHaveBeenCalledWith(
      'tastytrade', 'DEMO', { period: 'current', cursor: undefined, limit: 25 }
    );

    (fixture.nativeElement.querySelector('.load-more') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.getSymbolEvents).toHaveBeenCalledWith(
      'tastytrade', 'DEMO', { period: 'current', cursor: 'page-2', limit: 25 }
    );
    expect(fixture.componentInstance.eventHistory?.items.length).toBe(2);
  });

  it('does not render an empty-state card for an empty current period', async () => {
    const api = spyApi();
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detailResponse(BROKERAGES[0])));
    api.getSymbolEvents.and.returnValue(of(eventsResponse(BROKERAGES[0], {
      total_event_count: 0, items: [],
    })));
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.history-empty-compact')).toBeNull();
    expect(fixture.nativeElement.querySelector('.history-empty')).toBeNull();
    expect(fixture.nativeElement.querySelector('.history-tabs')).toBeNull();
    expect(api.getSymbolEvents).toHaveBeenCalledTimes(1);
  });

  it('shows changed archive warnings and loads their history on demand', async () => {
    const api = spyApi();
    const detail = detailResponse(BROKERAGES[0]);
    detail.symbol.archives = [{
      archive_id: 'archive-changed', symbol: 'DEMO', period_started_at: '2026-01-15T15:30:00Z',
      period_ended_at: '2026-07-28T16:00:00Z', event_count: 6, realized_pnl: 1425,
      pnl_completeness: 'COMPLETE', verification_status: 'CHANGED',
      created_at: '2026-07-28T16:01:00Z', note: '',
      warnings: ['Broker activity in this archived period has changed since it was created.'],
    }];
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detail));
    api.getSymbolEvents.and.returnValues(
      of(eventsResponse()),
      of(eventsResponse(BROKERAGES[0], { period: 'archive-changed' })),
    );
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('Changed');
    expect(text(fixture)).toContain('Broker activity in this archived period has changed');

    (fixture.nativeElement.querySelector('.archive-summary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.getSymbolEvents).toHaveBeenCalledWith(
      'tastytrade', 'DEMO', { period: 'archive-changed', cursor: undefined, limit: 25 }
    );
    expect(fixture.nativeElement.querySelector('.archive-events')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('.archive-summary')?.getAttribute('aria-expanded')).toBe('true');
  });

  it('keeps current events visible while one archived period expands at a time', async () => {
    const api = spyApi();
    const detail = detailResponse(BROKERAGES[0]);
    detail.symbol.archives = [
      {
        archive_id: 'archive-one', symbol: 'DEMO', period_started_at: '2026-01-15T15:30:00Z',
        period_ended_at: '2026-06-01T16:00:00Z', event_count: 2, realized_pnl: 100,
        pnl_completeness: 'COMPLETE', verification_status: 'VERIFIED',
        created_at: '2026-06-01T16:01:00Z', note: '', warnings: [],
      },
      {
        archive_id: 'archive-two', symbol: 'DEMO', period_started_at: '2026-06-02T15:30:00Z',
        period_ended_at: '2026-07-01T16:00:00Z', event_count: 3, realized_pnl: 200,
        pnl_completeness: 'COMPLETE', verification_status: 'VERIFIED',
        created_at: '2026-07-01T16:01:00Z', note: '', warnings: [],
      },
    ];
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detail));
    api.getSymbolEvents.and.returnValues(
      of(eventsResponse()),
      of(eventsResponse(BROKERAGES[0], { period: 'archive-one' })),
      of(eventsResponse(BROKERAGES[0], { period: 'archive-two' })),
    );
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('Current period');
    expect(fixture.nativeElement.querySelectorAll('.event-table').length).toBe(1);

    const archiveButtons = fixture.nativeElement.querySelectorAll('.archive-summary') as NodeListOf<HTMLButtonElement>;
    archiveButtons[0].click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('.archive-events').length).toBe(1);
    expect(fixture.nativeElement.querySelectorAll('.event-table').length).toBe(2);
    expect(archiveButtons[0].getAttribute('aria-expanded')).toBe('true');

    archiveButtons[1].click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('.archive-events').length).toBe(1);
    expect(archiveButtons[0].getAttribute('aria-expanded')).toBe('false');
    expect(archiveButtons[1].getAttribute('aria-expanded')).toBe('true');
    expect(fixture.nativeElement.querySelectorAll('.event-table').length).toBe(2);
  });

  it('confirms and archives only a reset-eligible completed period', async () => {
    const api = spyApi();
    const detail = detailResponse(BROKERAGES[0]);
    detail.symbol.reset_eligible = true;
    detail.symbol.reset_blockers = [];
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detail));
    api.createArchive.and.returnValue(of(archiveCreatedResponse()));
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    const archive = fixture.nativeElement.querySelector('.archive-control .btn-primary') as HTMLButtonElement;
    expect(archive.textContent).toContain('Archive completed history');
    archive.click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('Events');
    expect(text(fixture)).toContain('Realized P/L');

    (fixture.nativeElement.querySelector('app-modal .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    const body = api.createArchive.calls.mostRecent().args[2];
    expect(api.createArchive).toHaveBeenCalledWith('tastytrade', 'DEMO', body);
    expect(body.expected_period_version).toBe('v1:abc');
    expect(body.request_id).toBeTruthy();
    expect(text(fixture)).toContain('DEMO completed history archived.');
  });

  it('offers a refresh when the archive period changed', async () => {
    const api = spyApi();
    const detail = detailResponse(BROKERAGES[0]);
    detail.symbol.reset_eligible = true;
    detail.symbol.reset_blockers = [];
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detail));
    api.createArchive.and.returnValue(throwError(() => ({
      error: { detail: { code: 'PERIOD_CHANGED', message: 'This period changed since you loaded it. Refresh and try again.' } },
    })));
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('.archive-control .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('app-modal .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('Refresh ledger');

    (fixture.nativeElement.querySelector('app-modal .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.getSymbol.calls.count()).toBe(2);
  });

  it('retries an uncertain archive request with the original request identity', async () => {
    const api = spyApi();
    const detail = detailResponse(BROKERAGES[0]);
    detail.symbol.reset_eligible = true;
    detail.symbol.reset_blockers = [];
    api.listSymbols.and.returnValue(of(listResponse(BROKERAGES[0])));
    api.getSymbol.and.returnValue(of(detail));
    api.createArchive.and.returnValues(
      throwError(() => ({ error: { detail: { code: 'NETWORK', message: 'Connection interrupted.' } } })),
      of(archiveCreatedResponse()),
    );
    const fixture = await mount(api, 'tastytrade');

    (fixture.nativeElement.querySelector('.expand-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('.archive-control .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    (fixture.nativeElement.querySelector('app-modal .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('Retry archive');
    const firstBody = api.createArchive.calls.mostRecent().args[2];

    (fixture.nativeElement.querySelector('app-modal .btn-primary') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(api.createArchive.calls.mostRecent().args[2]).toEqual(firstBody);
    expect(text(fixture)).toContain('DEMO completed history archived.');
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
