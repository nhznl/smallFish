import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { BrokerageLedgerService } from '../api/brokerage-ledger.service';
import { CapabilityService } from '../api/capability.service';
import { StockService } from '../api/stock.service';
import { BrokerageHoldingsSnapshot } from '../model/brokerage-holdings';
import {
  OptionsActivityEvent, OptionsActivitySnapshot, OptionsTradeGroup,
} from '../model/options-ledger';
import { OptionsComponent } from './options.component';

const HOLDINGS: BrokerageHoldingsSnapshot = {
  holdings: [], totalInitial: 0, totalCurrent: 0, totalGainLoss: 0, totalGainLossPct: 0,
  byCategory: {}, byIndustry: {}, byAccountType: {}, topPositions: [],
  gainLossSnapshots: [], retrievedAt: '2026-07-28T16:30:00Z', source: 'TASTYTRADE',
};

const HOLDINGS_WITH_EQUITY: BrokerageHoldingsSnapshot = {
  ...HOLDINGS,
  holdings: [{
    enrichmentSymbol: 'ABC', category: 'UNCLASSIFIED', accountType: 'TRADING',
    industry: 'UNCLASSIFIED', symbol: 'ABC', costPrice: 45, qty: 100,
    initialInvestment: 4500, marketPrice: 52, currentValue: 5200,
    pctOfTotal: 100, gainLossPct: 15.56, gainLoss: 700,
    gainLossSnapshots: {}, note: '', trend: {
      alert: false, peakPct: null, peakAt: '', dropPct: null, fromPct: null,
      toPct: null, alertAt: null, direction: 'GAIN',
    },
  }],
  totalInitial: 4500, totalCurrent: 5200, totalGainLoss: 700,
  totalGainLossPct: 15.56,
};

const ACTIVITY: OptionsActivitySnapshot = {
  schema_name: 'smallfish.options_activity', schema_version: 1, account_filter: 'TRADING',
  events: [], groups: [], ungrouped_event_count: 0, reconciliation_issues: [], manual_events: [],
  last_sync_at: '2026-07-28T16:30:00Z', pnl_definition: 'Test definition',
};

function event(id: string, contractKey: string, groupId: string): OptionsActivityEvent {
  return {
    id, source: 'TASTYTRADE', source_transaction_id: id, account: 'TRADING',
    executed_at: '2026-07-28T16:30:00Z', transaction_date: '2026-07-28',
    transaction_type: 'Trade', transaction_sub_type: 'Sell to Open',
    instrument_type: 'Equity Option', contract_symbol: contractKey,
    contract_key: contractKey, underlying_symbol: 'ABC', action: 'SELL_TO_OPEN',
    quantity: 1, position_delta: -1, price: 1, value: 100, net_value: 99,
    fee_effect: -1, option_type: 'PUT', expiry: '2026-08-21', strike: 50,
    description: '', group_id: groupId, group_name: 'ABC 2026',
  };
}

describe('OptionsComponent', () => {
  it('shows the Tastytrade data timestamp before the sync action', async () => {
    const stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getOptions', 'getOptionsActivity',
    ]);
    stockService.getOptions.and.returnValue(of({
      totals: { combined: { open_broker_positions: 3 } },
    } as any));
    stockService.getOptionsActivity.and.returnValue(of(ACTIVITY));

    const capabilityService = jasmine.createSpyObj<CapabilityService>('CapabilityService', ['get']);
    capabilityService.get.and.returnValue(of(null));

    const brokerageLedger = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', [
      'getHoldings',
    ]);
    brokerageLedger.getHoldings.and.returnValue(of(HOLDINGS));

    await TestBed.configureTestingModule({
      imports: [OptionsComponent],
      providers: [
        { provide: StockService, useValue: stockService },
        { provide: CapabilityService, useValue: capabilityService },
        { provide: BrokerageLedgerService, useValue: brokerageLedger },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(OptionsComponent);
    fixture.detectChanges();

    const actions = fixture.nativeElement.querySelector('.page-actions') as HTMLElement;
    const timestamp = actions.querySelector('.snapshot-chip') as HTMLElement;
    const syncButton = actions.querySelector('.btn-primary') as HTMLButtonElement;
    expect(timestamp.textContent).toContain('Data as of');
    expect(timestamp.textContent).toContain('Jul 28, 9:30 AM');
    expect(timestamp.compareDocumentPosition(syncButton) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(Array.from(
      fixture.nativeElement.querySelectorAll('.tab-count') as NodeListOf<HTMLElement>
    ).map(count => count.textContent?.trim())).toEqual(['0', '3']);
  });

  it('shows current marks only for open contracts and no event edit column', async () => {
    const stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getOptions', 'getOptionsActivity',
    ]);
    stockService.getOptions.and.returnValue(of({
      totals: { combined: { open_broker_positions: 1 } },
    } as any));
    stockService.getOptionsActivity.and.returnValue(of(ACTIVITY));

    const capabilityService = jasmine.createSpyObj<CapabilityService>('CapabilityService', ['get']);
    capabilityService.get.and.returnValue(of(null));

    const brokerageLedger = jasmine.createSpyObj<BrokerageLedgerService>('BrokerageLedgerService', [
      'getHoldings',
    ]);
    brokerageLedger.getHoldings.and.returnValue(of(HOLDINGS_WITH_EQUITY));

    await TestBed.configureTestingModule({
      imports: [OptionsComponent],
      providers: [
        { provide: StockService, useValue: stockService },
        { provide: CapabilityService, useValue: capabilityService },
        { provide: BrokerageLedgerService, useValue: brokerageLedger },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(OptionsComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const openContract = 'ABC 260821P00050000';
    const closedContract = 'ABC 260717P00045000';
    const group: OptionsTradeGroup = {
      group_id: 'group-1', account: 'TRADING', symbol: 'ABC', name: 'ABC 2026',
      status: 'ACTIVE', notes: '', auto_created: 'true', event_count: 2,
      first_execution: '2026-07-01T16:30:00Z', last_execution: '2026-07-28T16:30:00Z',
      net_cash_flow: 99, fee_effect: -1, open_market_value: -40, total_pnl: 59,
      realized_pnl: null, position_status: 'OPEN', pnl_completeness: 'INDICATIVE',
      missing_marks: [], mark_retrieved_at: '2026-07-28T16:30:00Z',
      open_positions: [{
        contract_key: openContract, quantity: -1, option_type: 'PUT',
        expiry: '2026-08-21', strike: 50, mark_price: .4, market_value: -40,
      }],
    };
    component.data = null;
    component.activity = {
      ...ACTIVITY,
      groups: [group],
      events: [
        event('open', openContract, group.group_id),
        event('closed', closedContract, group.group_id),
        event('different-group', openContract, 'group-2'),
        {
          ...event('equity', 'ABC', group.group_id),
          instrument_type: 'Equity', contract_symbol: 'ABC', contract_key: 'ABC',
          option_type: '', expiry: '', strike: null, action: 'BUY_TO_OPEN',
        },
      ],
    };
    component.openGroupDetails(group);
    fixture.detectChanges();

    const dialog = fixture.nativeElement.querySelector('[role="dialog"]') as HTMLElement;
    const tables = dialog.querySelectorAll('table');
    const optionHeaders = Array.from(tables[0].querySelectorAll('th')).map(
      header => header.textContent?.trim()
    );
    const shareHeaders = Array.from(tables[1].querySelectorAll('th')).map(
      header => header.textContent?.trim()
    );
    const rows = tables[0].querySelectorAll('tbody tr');
    const markedValues = Array.from(rows).map(row => row.lastElementChild?.textContent?.trim());
    expect(optionHeaders).toEqual([
      'Date', 'Contract', 'Action', 'Qty', 'Price', 'Net cash', 'Fees', 'Open marked value',
    ]);
    expect(shareHeaders).toEqual([
      'Account', 'Shares', 'Avg cost', 'Last price', 'Cost basis', 'Market value', 'Open P/L',
    ]);
    expect(rows.length).toBe(2);
    expect(markedValues).toEqual(['-$40.00', '—']);
    expect(tables[1].textContent).toContain('100');
    expect(tables[1].textContent).toContain('$5,200.00');
  });
});
