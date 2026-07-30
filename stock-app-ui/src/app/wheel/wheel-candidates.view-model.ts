import { computed, signal } from '@angular/core';
import { Subscription } from 'rxjs';

import { StockService } from '../api/stock.service';
import { WheelCandidate } from '../model/wheel-candidate';
import { DataViewState } from '../shared/data-view-state';

/** Transport and list state for the Wheel candidates table (Phase 16). */
export class WheelCandidatesViewModel {
  private readonly _state = signal<DataViewState>('loading');
  private readonly _candidates = signal<WheelCandidate[]>([]);
  private readonly _runMode = signal('');

  readonly state = this._state.asReadonly();
  readonly candidates = this._candidates.asReadonly();
  readonly runMode = this._runMode.asReadonly();
  readonly loadingData = computed(() => this._state() === 'loading');
  readonly loadError = computed(() => this._state() === 'failed');

  applyCandidates(candidates: WheelCandidate[]): void {
    const rows = candidates ?? [];
    this._candidates.set(rows);
    this._runMode.set(rows[0]?.wheel?.runMode ?? '');
    this._state.set(rows.length ? 'ready' : 'empty');
  }

  markLoading(): void {
    this._state.set('loading');
  }

  markFailed(): void {
    this._candidates.set([]);
    this._runMode.set('');
    this._state.set('failed');
  }

  load(service: StockService, onSettled?: () => void): Subscription {
    this.markLoading();
    return service.getWheelCandidates().subscribe({
      next: (candidates) => {
        this.applyCandidates(candidates);
        onSettled?.();
      },
      error: () => {
        this.markFailed();
        onSettled?.();
      },
    });
  }
}
