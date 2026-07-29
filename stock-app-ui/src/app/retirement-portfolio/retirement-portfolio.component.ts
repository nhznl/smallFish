import { ChangeDetectionStrategy, Component } from '@angular/core';

import { BrokerageLedgerPageComponent } from '../shared/brokerage-ledger-page/brokerage-ledger-page.component';

/** Retirement is a route shell; all brokerage behavior lives in shared code. */
@Component({
  selector: 'app-retirement-portfolio',
  standalone: true,
  imports: [BrokerageLedgerPageComponent],
  templateUrl: './retirement-portfolio.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class RetirementPortfolioComponent {}
