import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError, map, shareReplay } from 'rxjs/operators';
import { API_BASE_URL } from './api-base';

/** One optional feature's availability. Mirrors stock-app/app/capabilities.py. */
export interface Capability {
  id: string;
  label: string;
  /** What the user loses while this is unavailable. */
  provides: string;
  state: 'NOT_CONFIGURED' | 'INCOMPLETE' | 'CONFIGURED' | 'NEEDS_REGISTRATION' | 'READY' | 'ERROR';
  available: boolean;
  /** Safe to display verbatim; never contains a secret. */
  reason: string;
  /** The exact command or action that advances the state. */
  action: string;
  provider: string;
  docs: string;
  requires: Record<string, boolean>;
}

export interface CapabilitySnapshot {
  schemaName: string;
  schemaVersion: number;
  capabilities: Capability[];
  unavailable: string[];
}

/**
 * Reads `GET /capabilities` so views can tell "not configured" apart from
 * "configured but empty" and from "error" — three situations that otherwise
 * render as the same blank table.
 *
 * The snapshot is fetched once and shared: it reflects app.env, which cannot
 * change while the server is running.
 */
@Injectable({ providedIn: 'root' })
export class CapabilityService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = inject(API_BASE_URL);

  private readonly snapshot$: Observable<CapabilitySnapshot> = this.http
    .get<CapabilitySnapshot>(`${this.apiBaseUrl}/capabilities`)
    .pipe(
      // A capability lookup must never break the page it is decorating. On
      // failure every feature is reported unknown, and views fall back to
      // their ordinary empty state.
      catchError(() => of({
        schemaName: 'smallfish.capabilities', schemaVersion: 1,
        capabilities: [], unavailable: []
      } as CapabilitySnapshot)),
      shareReplay({ bufferSize: 1, refCount: false })
    );

  all(): Observable<CapabilitySnapshot> {
    return this.snapshot$;
  }

  /** One capability by id, or null when unknown (including a failed lookup). */
  get(id: string): Observable<Capability | null> {
    return this.snapshot$.pipe(
      map(snapshot => snapshot.capabilities.find(item => item.id === id) ?? null)
    );
  }
}
