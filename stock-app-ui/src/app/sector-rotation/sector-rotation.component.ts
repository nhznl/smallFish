import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { StockService } from '../api/stock.service';
import {
  RotationCandidate,
  SectorLeadershipRow,
  SectorPairRow,
  SectorRotationSnapshot
} from '../model/sector-rotation';

/**
 * Sector price-leadership and rotation view.
 *
 * Reads only the archived snapshot written by `./commands.sh sector-rotation`;
 * opening or filtering this page never fetches prices. Every label says
 * rotation / leadership / relative strength — this is a price-and-volume proxy,
 * not a measured fund flow, and no forward endpoint has been validated.
 */
@Component({
  selector: 'app-sector-rotation',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, MatTooltipModule],
  templateUrl: './sector-rotation.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./sector-rotation.component.css']
})
export class SectorRotationComponent implements OnInit {
  private readonly stockService = inject(StockService);

  snapshot: SectorRotationSnapshot | null = null;
  loading = false;
  error = '';
  /** Selected leadership window; defaults to the medium-term 20-session view. */
  window = 20;
  expandedCandidate: string | null = null;

  // Recompute state. The job reads the local price cache only — it never
  // contacts a market-data provider, so it is safe to re-run at will.
  running = false;
  runStatus: 'idle' | 'ok' | 'error' = 'idle';
  runMessage = '';
  runMessageAt: Date | null = null;

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.error = '';
    this.stockService.getSectorRotation().subscribe({
      next: snapshot => {
        this.snapshot = snapshot ?? null;
        this.loading = false;
        if (!snapshot) {
          this.error = 'Could not load the sector-rotation snapshot.';
          return;
        }
        const windows = snapshot.windows ?? [];
        if (windows.length && !windows.includes(this.window)) {
          this.window = windows[Math.min(1, windows.length - 1)];
        }
      },
      error: err => {
        this.snapshot = null;
        this.loading = false;
        this.error = err?.error?.detail ?? 'Could not load the sector-rotation snapshot.';
      }
    });
  }

  /** Recompute the snapshot from the cache, then reload what was written. */
  run(): void {
    this.running = true;
    this.runStatus = 'idle';
    this.runMessage = 'Recomputing sector leadership from the local price cache…';
    this.runMessageAt = new Date();
    this.stockService.runSectorRotation().subscribe(res => {
      this.running = false;
      this.runMessageAt = new Date();
      if (res && res.status === 'ok') {
        this.runStatus = 'ok';
        const secs = res.durationMs != null ? Math.round(res.durationMs / 1000) : null;
        this.runMessage = `✓ Snapshot recomputed${secs != null ? ' in ' + secs + 's' : ''}.`;
        this.load();
      } else {
        this.runStatus = 'error';
        this.runMessage = '✗ Recompute failed: ' +
          (res?.message || res?.output || 'see server logs') + '.';
      }
    });
  }

  runStatusClass(): string {
    if (this.runStatus === 'ok') return 'job-ok';
    if (this.runStatus === 'error') return 'job-error';
    return 'job-running';
  }

  windows(): number[] {
    return this.snapshot?.windows ?? [];
  }

  /** Leadership rows for the selected window, strongest excess return first. */
  rows(): SectorLeadershipRow[] {
    return (this.snapshot?.sectors ?? [])
      .filter(row => row.windowSessions === this.window)
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  }

  benchmarkReturn(): number | null {
    return this.rows()[0]?.benchmarkReturn ?? null;
  }

  candidates(): RotationCandidate[] {
    return this.snapshot?.rotationCandidates ?? [];
  }

  /** Pairwise ratios for the selected window, largest absolute move first. */
  pairs(): SectorPairRow[] {
    return (this.snapshot?.pairs ?? [])
      .filter(row => row.windowSessions === this.window)
      .sort((a, b) => Math.abs(b.ratioChangePct ?? 0) - Math.abs(a.ratioChangePct ?? 0))
      .slice(0, 12);
  }

  candidateKey(candidate: RotationCandidate): string {
    return `${candidate.source}->${candidate.target}`;
  }

  toggleCandidate(candidate: RotationCandidate): void {
    const key = this.candidateKey(candidate);
    this.expandedCandidate = this.expandedCandidate === key ? null : key;
  }

  isExpanded(candidate: RotationCandidate): boolean {
    return this.expandedCandidate === this.candidateKey(candidate);
  }

  /** Format a fraction as a signed percentage; null renders as an em dash. */
  pct(value: number | null | undefined, digits = 2): string {
    return value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`;
  }

  ratio(value: number | null | undefined): string {
    return value == null ? '—' : `${value.toFixed(2)}×`;
  }

  /** Rank movement toward rank 1 reads as a positive, upward change. */
  rankChangeLabel(value: number | null | undefined): string {
    if (value == null) return '—';
    if (value === 0) return '0';
    return value > 0 ? `▲ ${value}` : `▼ ${Math.abs(value)}`;
  }

  signClass(value: number | null | undefined): string {
    if (value == null || value === 0) return '';
    return value > 0 ? 'val-pos' : 'val-neg';
  }

  stateClass(state: string | null | undefined): string {
    if (state === 'LEADING') return 'badge-pos';
    if (state === 'LAGGING') return 'badge-neg';
    return 'badge-neutral';
  }

  trendClass(trend: string | null | undefined): string {
    if (trend === 'STRENGTHENING') return 'val-pos';
    if (trend === 'WEAKENING') return 'val-neg';
    return '';
  }
}
