import { Component, Input } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BrokerageId } from '../model/brokerage';
import { BrokerageLedgerPortfolioSlug } from '../model/brokerage-ledger';
import { OptionsComponent } from './options.component';

@Component({ selector: 'app-brokerage-ledger-page', standalone: true, template: '' })
class BrokerageLedgerPageStub {
  @Input() brokerageId!: BrokerageId;
  @Input() portfolio!: BrokerageLedgerPortfolioSlug;
  @Input() title = '';
}

describe('OptionsComponent', () => {
  it('is a thin Trading shell over the shared brokerage ledger page', async () => {
    await TestBed.configureTestingModule({ imports: [OptionsComponent] })
      .overrideComponent(OptionsComponent, { set: { imports: [BrokerageLedgerPageStub] } })
      .compileComponents();
    const fixture = TestBed.createComponent(OptionsComponent);
    fixture.detectChanges();

    const shell = fixture.nativeElement.querySelector('app-brokerage-ledger-page') as HTMLElement;
    expect(shell).toBeTruthy();
    const instance = fixture.debugElement.children[0].componentInstance as BrokerageLedgerPageStub;
    expect(instance.brokerageId).toBe('tastytrade');
    expect(instance.portfolio).toBe('trading');
    expect(instance.title).toBe('Trading Ledger');
  });
});
