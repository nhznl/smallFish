import { Component, Input } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BrokerageId } from '../model/brokerage';
import { RetirementPortfolioComponent } from './retirement-portfolio.component';

@Component({ selector: 'app-brokerage-ledger-page', standalone: true, template: '' })
class BrokerageLedgerPageStub {
  @Input() brokerageId!: BrokerageId;
  @Input() title = '';
}

describe('RetirementPortfolioComponent', () => {
  it('is a thin Retirement shell over the shared brokerage ledger page', async () => {
    await TestBed.configureTestingModule({ imports: [RetirementPortfolioComponent] })
      .overrideComponent(RetirementPortfolioComponent, { set: { imports: [BrokerageLedgerPageStub] } })
      .compileComponents();
    const fixture = TestBed.createComponent(RetirementPortfolioComponent);
    fixture.detectChanges();

    const shell = fixture.nativeElement.querySelector('app-brokerage-ledger-page') as HTMLElement;
    expect(shell).toBeTruthy();
    const instance = fixture.debugElement.children[0].componentInstance as BrokerageLedgerPageStub;
    expect(instance.brokerageId).toBe('fidelity');
    expect(instance.title).toBe('Retirement Ledger');
  });
});
