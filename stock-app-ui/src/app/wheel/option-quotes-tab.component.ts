import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, OnInit, SimpleChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MatTooltipModule } from '@angular/material/tooltip';
import { StockService } from '../api/stock.service';
import { OptionQuoteRow, OptionQuoteSnapshot } from '../model/option-quotes';

@Component({
  selector: 'app-option-quotes-tab',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, MatTooltipModule],
  templateUrl: './option-quotes-tab.component.html',
  styleUrls: ['./option-quotes-tab.component.css']
})
export class OptionQuotesTabComponent implements OnInit, OnChanges {
  @Input() refreshToken = 0;

  private readonly stockService = inject(StockService);
  snapshot: OptionQuoteSnapshot | null = null;
  loading = false;
  error = '';
  symbolFilter = '';
  viewFilter = 'ALL';
  qualityFilter = 'ALL';

  ngOnInit(): void {
    this.load();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['refreshToken'] && !changes['refreshToken'].firstChange) this.load();
  }

  load(): void {
    this.loading = true;
    this.error = '';
    this.stockService.getOptionQuotes().subscribe({
      next: snapshot => {
        this.snapshot = snapshot ?? null;
        this.loading = false;
        if (!snapshot) this.error = 'Could not load the latest quote archive.';
      },
      error: err => {
        this.snapshot = null;
        this.loading = false;
        this.error = err?.error?.detail ?? 'Could not load the latest quote archive.';
      }
    });
  }

  rows(): OptionQuoteRow[] {
    const query = this.symbolFilter.trim().toUpperCase();
    return (this.snapshot?.rows ?? []).filter(row =>
      (!query || (row.symbol ?? '').toUpperCase().includes(query)) &&
      (this.viewFilter === 'ALL' || row.analysisView === this.viewFilter) &&
      (this.qualityFilter === 'ALL' || row.quoteQuality === this.qualityFilter)
    );
  }

  qualityLabels(): string[] {
    return Object.keys(this.snapshot?.summary?.quoteQualityCounts ?? {}).sort();
  }

  providerValue(name: string): string | number | null {
    const value = this.snapshot?.quoteProvider?.[name];
    return typeof value === 'string' || typeof value === 'number' ? value : null;
  }

  /** Plain-language rendering of the manifest's recorded collection scope. */
  scopeDescription(): string {
    const scope = this.snapshot?.collectionScope;
    if (!scope?.scoped) {
      return '';
    }
    const parts: string[] = [];
    if (scope.requestedDtes?.length) {
      parts.push(`${scope.requestedDtes.join(', ')} DTE only` +
        (scope.configuredDtes?.length
          ? ` (configured: ${scope.configuredDtes.join(', ')})`
          : ''));
    }
    if (scope.symbolCount != null) {
      parts.push(`${scope.symbolCount} requested symbol${scope.symbolCount === 1 ? '' : 's'}`);
    }
    if (scope.minOtmPct) {
      parts.push(`entry strikes at least ${(scope.minOtmPct * 100).toFixed(1).replace(/\.0$/, '')}% OTM` +
        ' (roll/exit strikes unaffected)');
    }
    if (scope.limit != null) {
      parts.push(`limit ${scope.limit}`);
    }
    return parts.join('; ') + '.';
  }

  qualityBadgeClass(quality: string | null): string {
    if (!quality || quality === 'UNKNOWN') return 'badge-neutral';
    if (quality === 'OK' || quality === 'GOOD') return 'badge-pos';
    if (quality === 'STALE' || quality === 'WIDE') return 'badge-warn';
    return 'badge-warn';
  }
}
