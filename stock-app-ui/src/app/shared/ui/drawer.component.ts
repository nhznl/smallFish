import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { A11yModule } from '@angular/cdk/a11y';
import { OverlayBase } from './overlay-base';

/**
 * Right-hand details drawer.
 *
 * Usage:
 * ```html
 * <app-drawer *ngIf="row as r" [ariaLabel]="r.code + ' details'" (closed)="clear()">
 *   <div drawerHeader>…</div>
 *   …body…
 *   <div drawerFooter>…</div>
 * </app-drawer>
 * ```
 */
@Component({
  selector: 'app-drawer',
  standalone: true,
  imports: [A11yModule],
  templateUrl: './drawer.component.html',
  styleUrl: './drawer.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class DrawerComponent extends OverlayBase {
  /** Panel width; any CSS length. */
  @Input() width = 'min(760px, 94vw)';
}
