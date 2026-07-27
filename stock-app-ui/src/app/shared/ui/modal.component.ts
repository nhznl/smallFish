import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { A11yModule } from '@angular/cdk/a11y';
import { OverlayBase } from './overlay-base';

/**
 * Centred modal dialog.
 *
 * Usage:
 * ```html
 * <app-modal *ngIf="editing" title="Edit group" (closed)="editing = null">
 *   …body…
 *   <ng-container modalActions>
 *     <button class="btn" (click)="editing = null">Cancel</button>
 *     <button class="btn btn-primary" (click)="save()">Save</button>
 *   </ng-container>
 * </app-modal>
 * ```
 */
@Component({
  selector: 'app-modal',
  standalone: true,
  imports: [A11yModule],
  templateUrl: './modal.component.html',
  styleUrl: './modal.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ModalComponent extends OverlayBase {
  /** Heading text. Also names the dialog unless `ariaLabel` is set. */
  @Input() title = '';

  /** Sub-heading under the title. */
  @Input() subtitle = '';

  /** Panel width; any CSS length. */
  @Input() width = 'min(680px, 94vw)';

  get dialogLabel(): string {
    return this.ariaLabel !== 'Details' ? this.ariaLabel : this.title || 'Dialog';
  }
}
