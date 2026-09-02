import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { of } from 'rxjs';

import { BrokerageService } from '../../api/brokerage.service';
import { BrokerageId } from '../../model/brokerage';
import { BrokerageLedgerPageComponent } from './brokerage-ledger-page.component';

@Component({ selector: 'app-brokerage-holdings', standalone: true, template: '' })
class HoldingsStub {
  @Input() brokerageId!: BrokerageId;
  @Input() refreshToken = 0;
  @Output() countChange = new EventEmitter<number>();
}

@Component({ selector: 'app-symbol-ledger', standalone: true, template: '' })
class SymbolLedgerStub {
  @Input() brokerageId!: BrokerageId;
  @Input() refreshToken = 0;
}

@Component({ selector: 'app-brokerage-ledger-combined', standalone: true, template: '' })
class BasisStub {
  @Input() brokerageId!: BrokerageId;
  @Input() refreshToken = 0;
}

@Component({ selector: 'app-portfolio-analysis', standalone: true, template: '' })
class PortfolioAnalysisStub {
  @Input() brokerageId!: BrokerageId;
  @Input() refreshToken = 0;
}

describe('BrokerageLedgerPageComponent', () => {
  it('adds Portfolio Analysis as the fourth shared tab without changing the first three', async () => {
    const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['runSync']);
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerPageComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).overrideComponent(BrokerageLedgerPageComponent, {
      set: { imports: [CommonModule, HoldingsStub, SymbolLedgerStub, BasisStub, PortfolioAnalysisStub] },
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerPageComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.componentRef.setInput('title', 'Retirement Ledger');
    fixture.detectChanges();

    const tabs = Array.from(
      fixture.nativeElement.querySelectorAll('[role="tab"]') as NodeListOf<HTMLButtonElement>
    );
    expect(tabs.map(tab => tab.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
      'Holdings0', 'Options', 'Combined Adjusted Basis', 'Portfolio Analysis',
    ]);
    expect(fixture.nativeElement.querySelector('app-brokerage-holdings')).toBeTruthy();

    tabs[3].click();
    fixture.detectChanges();

    expect(tabs[3].getAttribute('aria-selected')).toBe('true');
    const analysis = fixture.debugElement.query(By.directive(PortfolioAnalysisStub))
      ?.componentInstance as PortfolioAnalysisStub;
    expect(analysis).toBeTruthy();
    expect(analysis.brokerageId).toBe('fidelity');
    expect(fixture.nativeElement.querySelector('app-brokerage-holdings')).toBeFalsy();
  });

  it('mentions Sold Stock names after a holdings sync closes them', async () => {
    const api = jasmine.createSpyObj<BrokerageService>('BrokerageService', ['runSync']);
    api.runSync.and.returnValue(of({
      schema_name: 'smallfish.brokerage-sync-report',
      schema_version: 1,
      brokerage_id: 'fidelity',
      results: [
        {
          resource: 'HOLDINGS',
          status: 'OK',
          detail: { sold_symbols: ['AAA', 'BBB'], sold_tracked: 1, sold_updated: 1 },
          warnings: [],
        },
        {
          resource: 'ACCOUNT_CAPITAL',
          status: 'OK',
          detail: { sold_symbols: ['AAA', 'BBB'], sold_tracked: 1, sold_updated: 1 },
          warnings: [],
        },
      ],
    }));
    await TestBed.configureTestingModule({
      imports: [BrokerageLedgerPageComponent],
      providers: [{ provide: BrokerageService, useValue: api }],
    }).overrideComponent(BrokerageLedgerPageComponent, {
      set: { imports: [CommonModule, HoldingsStub, SymbolLedgerStub, BasisStub, PortfolioAnalysisStub] },
    }).compileComponents();

    const fixture = TestBed.createComponent(BrokerageLedgerPageComponent);
    fixture.componentRef.setInput('brokerageId', 'fidelity');
    fixture.componentRef.setInput('title', 'Retirement Ledger');
    fixture.detectChanges();

    fixture.nativeElement.querySelector('.btn-primary').click();
    fixture.detectChanges();

    const banner = fixture.nativeElement.querySelector('.banner-pos') as HTMLElement;
    expect(banner.textContent).toContain('AAA, BBB moved to Tracking as Sold Stock.');
    expect(api.runSync).toHaveBeenCalledWith('fidelity');
  });
});
