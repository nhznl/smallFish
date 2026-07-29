import { TestBed } from '@angular/core/testing';

import { BrokerageOptionGroup } from '../../model/brokerage-option-groups';
import { BrokerageOptionGroupsComponent } from './brokerage-option-groups.component';

const GROUPS: BrokerageOptionGroup[] = [{
  symbol: 'DEMO', account: 'TRADING', name: 'DEMO 2026', status: 'ACTIVE',
  net_cash_flow: 200, open_market_value: -80, total_pnl: 120,
  position_status: 'OPEN', pnl_completeness: 'INDICATIVE', event_count: 2, notes: 'Core',
}, {
  symbol: 'OLD', account: 'RETIREMENT', name: 'OLD 2025', status: 'ARCHIVED',
  net_cash_flow: 50, open_market_value: 0, total_pnl: 50,
  position_status: 'FLAT', pnl_completeness: 'COMPLETE', event_count: 2, notes: '',
}];

describe('BrokerageOptionGroupsComponent', () => {
  it('renders the normalized summary, filters, totals and provider event label', async () => {
    await TestBed.configureTestingModule({ imports: [BrokerageOptionGroupsComponent] })
      .compileComponents();
    const fixture = TestBed.createComponent(BrokerageOptionGroupsComponent);
    fixture.componentRef.setInput('groups', GROUPS);
    fixture.componentRef.setInput('eventCount', 4);
    fixture.componentRef.setInput('ungroupedCount', 1);
    fixture.componentRef.setInput('eventColumnLabel', 'Legs');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(text).toContain('4 imported events');
    expect(text).toContain('2 groups');
    expect(text).toContain('1 ungrouped');
    expect(text).toContain('Active');
    expect(text).toContain('Archived');
    expect(text).toContain('Legs');
    expect(text).toContain('$250.00');
    expect(text).toContain('$170.00');

    fixture.componentInstance.showArchived = false;
    fixture.detectChanges();
    const filtered = (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
    expect(filtered).toContain('DEMO 2026');
    expect(filtered).not.toContain('OLD 2025');
  });

  it('emits the edit action', async () => {
    await TestBed.configureTestingModule({ imports: [BrokerageOptionGroupsComponent] })
      .compileComponents();
    const fixture = TestBed.createComponent(BrokerageOptionGroupsComponent);
    fixture.componentRef.setInput('groups', [GROUPS[0]]);
    const edited = jasmine.createSpy('edited');
    fixture.componentInstance.editGroup.subscribe(edited);
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.icon-btn') as HTMLButtonElement).click();
    expect(edited).toHaveBeenCalledOnceWith(GROUPS[0]);
  });
});
