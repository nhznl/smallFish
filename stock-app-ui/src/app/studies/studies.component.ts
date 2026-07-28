import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { StudiesService } from '../api/studies.service';
import { StrategyStocksComponent } from '../strategy-stocks/strategy-stocks.component';
import {
  StudyCatalogItem, StudyDetail, StudyEvidenceLevel, StudyStatistic,
  StudyVariation, StudyVerdict
} from './study.models';

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

  catalog: StudyCatalogItem[] = [];
  study: StudyDetail | null = null;
  selectedVariation: StudyVariation | null = null;
  loading = true;
  error = '';
  scanRunning = false;
  scanMessage = '';
  scanCandidates: any[] = [];
  scanGeneratedAt = '';

  ngOnInit(): void {
    this.studiesService.getCatalog().subscribe({
      next: catalog => {
        this.catalog = catalog.studies;
        this.route.paramMap.subscribe(params => this.selectStudy(params.get('studyId')));
      },
      error: () => this.fail('Research Studies are unavailable. Build or repair the materialized study artifacts, then try again.')
    });
  }

  private selectStudy(studyId: string | null): void {
    const selected = studyId || this.catalog[0]?.id;
    if (!selected) {
      this.loading = false;
      return;
    }
    if (!studyId) {
      void this.router.navigate(['/studies', selected], { replaceUrl: true });
      return;
    }
    this.loading = true;
    this.error = '';
    this.studiesService.getStudy(selected).subscribe({
      next: study => {
        this.study = study;
        const requestedVariation = this.route.snapshot.queryParamMap.get('variation');
        this.selectedVariation = study.variations.find(item => item.id === requestedVariation)
          ?? study.variations.find(item => item.id === study.defaultVariationId)
          ?? study.variations[0]
          ?? null;
        this.loading = false;
        if (this.selectedVariation?.scan?.executionSupported) this.loadScan();
      },
      error: () => this.fail('This study record is missing or invalid. The app is not showing stale or partial evidence.')
    });
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
    this.scanRunning = true;
    this.scanMessage = 'Running scan…';
    this.studiesService.runScan(this.study.id).subscribe({
      next: result => {
        this.scanRunning = false;
        this.scanMessage = result.status === 'ok' ? 'Scan complete.' : `Scan failed: ${result.message || result.output || 'see server logs'}`;
        if (result.status === 'ok') this.loadScan();
      },
      error: () => { this.scanRunning = false; this.scanMessage = 'Scan failed. See the server response for details.'; }
    });
  }

  private loadScan(): void {
    if (!this.study) return;
    this.studiesService.getScan(this.study.id).subscribe({
      next: snapshot => { this.scanCandidates = snapshot.candidates; this.scanGeneratedAt = snapshot.generatedAt; },
      error: () => { this.scanCandidates = []; this.scanGeneratedAt = ''; }
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
