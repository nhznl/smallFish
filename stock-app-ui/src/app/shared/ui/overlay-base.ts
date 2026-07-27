import { Directive, EventEmitter, HostListener, Input, OnDestroy, OnInit, Output } from '@angular/core';

/**
 * Shared behaviour for the drawer and modal overlays: Escape closes, the page
 * behind stops scrolling while open, and the caller is told to tear the
 * overlay down. Focus containment and focus restore come from the CDK's
 * `cdkTrapFocus` with auto-capture in each template.
 */
/**
 * Scroll lock is reference-counted: if two overlays are ever open at once,
 * the last one to close is the one that restores the page's own overflow.
 */
let openOverlays = 0;
let overflowBeforeLock = '';

@Directive()
export abstract class OverlayBase implements OnInit, OnDestroy {
  /** Accessible name for the dialog. */
  @Input() ariaLabel = 'Details';

  /** Emitted on Escape, backdrop click, or the close button. */
  @Output() readonly closed = new EventEmitter<void>();

  ngOnInit(): void {
    if (openOverlays === 0) {
      overflowBeforeLock = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    openOverlays++;
  }

  ngOnDestroy(): void {
    openOverlays = Math.max(0, openOverlays - 1);
    if (openOverlays === 0) {
      document.body.style.overflow = overflowBeforeLock;
    }
  }

  @HostListener('document:keydown.escape', ['$event'])
  onEscape(event: Event): void {
    event.stopPropagation();
    this.close();
  }

  close(): void {
    this.closed.emit();
  }
}
