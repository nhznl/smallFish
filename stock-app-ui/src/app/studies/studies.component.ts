import { CommonModule } from '@angular/common';
import { Component, DestroyRef, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Observable } from 'rxjs';
import { catchError, distinctUntilChanged, map, switchMap, tap } from 'rxjs/operators';
import { StudiesService } from '../api/studies.service';
import { StrategyStocksComponent } from '../strategy-stocks/strategy-stocks.component';
import {
  StudyCatalogItem, StudyDetail, StudyEvidenceLevel, StudyStatistic,
  StudyVariation, StudyVerdict
} from './study.models';
import { StrategyStock } from '../model/stock';

@Component({
  selector: 'app-studies',
  standalone: true,
  imports: [CommonModule, RouterLink, StrategyStocksComponent],
  templateUrl: './studies.component.html',
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./studies.component.css']
})
export class StudiesComponent implements OnInit {
  private readonly studiesService = inject(StudiesService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  catalog: StudyCatalogItem[] = [];
  study: StudyDetail | null = null;
  selectedVariation: StudyVariation | null = null;
  loading = true;
  error = '';
  scanRunning = false;
  scanStatus: 'idle' | 'ok' | 'error' = 'idle';
  scanMessage = '';
  scanCandidates: StrategyStock[] = [];
  scanGeneratedAt = '';
  scanEventWindow: { start: string; end: string; eventCount: number } | null = null;

  ngOnInit(): void {
    // Catalog once, then switchMap on studyId so a slower getStudy/getScan for A
    // cannot overwrite a newer load for B when the same component stays mounted.
    this.studiesService.getCatalog().pipe(
      switchMap(catalog => {
        this.catalog = catalog.studies;
        if (!this.catalog.length) {
          this.loading = false;
          this.study = null;
          return EMPTY;
        }
        return this.route.paramMap.pipe(
          map(params => params.get('studyId')),
          distinctUntilChanged(),
          switchMap(studyId => this.loadStudy(studyId)),
        );
      }),
      catchError(() => {
        this.fail('Research Studies are unavailable. Build or repair the materialized study artifacts, then try again.');
        return EMPTY;
      }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe();
  }

  private loadStudy(studyId: string | null): Observable<unknown> {
    const selected = studyId || this.catalog[0]?.id;
    if (!selected) {
      this.loading = false;
      return EMPTY;
    }
    if (!studyId) {
      void this.router.navigate(['/studies', selected], { replaceUrl: true });
      return EMPTY;
    }

    this.loading = true;
    this.error = '';
    this.scanCandidates = [];
    this.scanGeneratedAt = '';
    this.scanEventWindow = null;
    this.scanStatus = 'idle';
    this.scanMessage = '';

    return this.studiesService.getStudy(selected).pipe(
      tap(study => this.applyStudy(study)),
      switchMap(study => this.loadScanIfSupported(study)),
      catchError(() => {
        this.fail('This study record is missing or invalid. The app is not showing stale or partial evidence.');
        return EMPTY;
      }),
    );
  }

  private applyStudy(study: StudyDetail): void {
    this.study = study;
    const requestedVariation = this.route.snapshot.queryParamMap.get('variation');
    this.selectedVariation = study.variations.find(item => item.id === requestedVariation)
      ?? study.variations.find(item => item.id === study.defaultVariationId)
      ?? study.variations[0]
      ?? null;
    this.loading = false;
  }

  private loadScanIfSupported(study: StudyDetail): Observable<unknown> {
    if (!this.selectedVariation?.scan?.executionSupported) {
      return EMPTY;
    }
    return this.studiesService.getScan(study.id).pipe(
      tap(snapshot => {
        this.scanCandidates = snapshot.candidates;
        this.scanGeneratedAt = snapshot.generatedAt;
        this.scanEventWindow = snapshot.eventWindow ?? null;
      }),
      catchError(() => {
        this.scanCandidates = [];
        this.scanGeneratedAt = '';
        this.scanEventWindow = null;
        return EMPTY;
      }),
    );
  }

  selectVariation(variation: StudyVariation): void {
    if (!this.study || variation.id === this.selectedVariation?.id) return;
    this.selectedVariation = variation;
    void this.router.navigate(['/studies', this.study.id], {
      queryParams: { variation: variation.id },
      queryParamsHandling: 'merge'
    });
  }

  runScan(): void {
    if (!this.study || this.scanRunning) return;
    const studyId = this.study.id;
    this.scanRunning = true;
    this.scanStatus = 'idle';
    this.scanMessage = 'Running scan…';
    this.studiesService.runScan(studyId).subscribe({
      next: result => {
        if (this.study?.id !== studyId) return;
        this.scanStatus = result.status === 'ok' ? 'ok' : 'error';
        this.scanMessage = result.status === 'ok'
          ? 'Loading current candidate results…'
          : `Scan not run: ${result.message || result.output || 'see server logs'}`;
        if (result.status === 'ok') {
          this.studiesService.getScan(studyId).subscribe({
            next: snapshot => {
              if (this.study?.id !== studyId) return;
              this.scanCandidates = snapshot.candidates;
              this.scanGeneratedAt = snapshot.generatedAt;
              this.scanEventWindow = snapshot.eventWindow ?? null;
              this.scanRunning = false;
              this.scanMessage = 'Scan complete with a current upcoming-earnings calendar.';
            },
            error: () => {
              if (this.study?.id !== studyId) return;
              this.scanCandidates = [];
              this.scanGeneratedAt = '';
              this.scanEventWindow = null;
              this.scanRunning = false;
            },
          });
        } else {
          this.scanRunning = false;
        }
      },
      error: () => {
        if (this.study?.id !== studyId) return;
        this.scanRunning = false;
        this.scanStatus = 'error';
        this.scanMessage = 'Scan not run. Fresh upcoming earnings data could not be verified.';
      }
    });
  }

  verdictClass(verdict: StudyVerdict): string {
    return verdict === 'PASSED' ? 'badge-pos' : verdict === 'FAILED' ? 'badge-neg' : 'badge-warn';
  }

  evidenceClass(level: StudyEvidenceLevel): string {
    return level === 'CONFIRMATORY' ? 'badge-info' : level === 'DESCRIPTIVE' ? 'badge-neutral' : 'badge-warn';
  }

  formatStatistic(stat: StudyStatistic): string {
    if (stat.value == null) return '—';
    if (typeof stat.value === 'string') return stat.value;
    if (stat.format === 'PERCENT') return `${stat.value >= 0 ? '+' : ''}${(stat.value * 100).toFixed(stat.precision)}%`;
    if (stat.format === 'CURRENCY') return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: stat.precision }).format(stat.value);
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: stat.precision }).format(stat.value);
  }

  formatIntervalValue(stat: StudyStatistic, value: number | null): string {
    return this.formatStatistic({ ...stat, value });
  }

  statisticClass(stat: StudyStatistic): string {
    return typeof stat.value === 'number' && stat.value < 0 ? 'neg-value' : typeof stat.value === 'number' && stat.value > 0 ? 'pos-value' : '';
  }

  private fail(message: string): void {
    this.loading = false;
    this.error = message;
  }
}
