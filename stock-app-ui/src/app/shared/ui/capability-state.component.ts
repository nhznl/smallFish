import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { NgIf } from '@angular/common';
import { Capability } from '../../api/capability.service';

/**
 * The shared "this feature needs something first" state.
 *
 * Replaces per-view blank tables and dead-end instructions. It renders the
 * four situations a data-backed view can be in, using the shared `.empty-state`
 * and `.badge` primitives from `styles.scss`:
 *
 * - **not configured** — name the provider, say what the feature adds, give the
 *   exact setup command, and offer to continue without it;
 * - **configured but empty** — offer the sync and say what to expect;
 * - **error / expired** — safe remediation, no credential detail;
 * - **core data missing** — point at `./commands.sh bootstrap-data`.
 *
 * Usage:
 * ```html
 * <app-capability-state
 *   *ngIf="(tastytrade$ | async) as capability"
 *   [capability]="capability"
 *   [emptyWhenAvailable]="!data.rows.length"
 *   availableEmptyText="No open broker option positions for this account.">
 * </app-capability-state>
 * ```
 */
@Component({
  selector: 'app-capability-state',
  standalone: true,
  imports: [NgIf],
  templateUrl: './capability-state.component.html',
  styleUrl: './capability-state.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class CapabilityStateComponent {
  /** The capability this view depends on. Null hides the component entirely. */
  @Input() capability: Capability | null = null;

  /** True when the provider is ready but has returned no rows. */
  @Input() emptyWhenAvailable = false;

  /** Shown when the provider is ready and `emptyWhenAvailable` is true. */
  @Input() availableEmptyText = 'No data yet.';

  /** Action label for the configured-but-empty state, if the view offers one. */
  @Input() syncLabel = '';

  /** Set false to hide the "continue without it" reassurance. */
  @Input() dismissible = true;

  get show(): boolean {
    if (!this.capability) {
      return false;
    }
    return !this.capability.available || this.emptyWhenAvailable;
  }

  get isError(): boolean {
    return this.capability?.state === 'ERROR';
  }

  get isPartial(): boolean {
    return this.capability?.state === 'INCOMPLETE'
      || this.capability?.state === 'NEEDS_REGISTRATION';
  }

  /**
   * Status word paired with the badge colour, so the state is never conveyed
   * by colour alone.
   */
  get badgeText(): string {
    if (!this.capability) {
      return '';
    }
    if (this.capability.available) {
      return 'No data yet';
    }
    switch (this.capability.state) {
      case 'ERROR': return 'Error';
      case 'INCOMPLETE': return 'Partly configured';
      case 'NEEDS_REGISTRATION': return 'Setup unfinished';
      default: return 'Optional — not set up';
    }
  }

  get badgeClass(): string {
    if (this.isError) {
      return 'badge badge-neg';
    }
    if (this.isPartial) {
      return 'badge badge-warn';
    }
    return 'badge badge-info';
  }

  get title(): string {
    if (!this.capability) {
      return '';
    }
    if (this.capability.available) {
      return this.availableEmptyText;
    }
    if (this.isError) {
      return `${this.capability.label} is not usable right now`;
    }
    return `${this.capability.label} is not set up`;
  }
}
