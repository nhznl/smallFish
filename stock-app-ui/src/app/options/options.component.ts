import { ChangeDetectionStrategy, Component } from '@angular/core';

import { BrokerageLedgerPageComponent } from '../shared/brokerage-ledger-page/brokerage-ledger-page.component';

/** Trading is a route shell; all brokerage behavior lives in shared code. */
@Component({
  selector: 'app-options',
  standalone: true,
  imports: [BrokerageLedgerPageComponent],
  templateUrl: './options.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
})
export class OptionsComponent {}
