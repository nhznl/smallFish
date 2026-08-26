import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ChangeDetectorRef } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { MatTooltip } from '@angular/material/tooltip';
import { of } from 'rxjs';
import { TrackingSoldStocksComponent } from './tracking-sold-stocks.component';
import { TrackedStockService } from '../api/tracked-stock.service';

describe('TrackingSoldStocksComponent', () => {
  let fixture: ComponentFixture<TrackingSoldStocksComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TrackingSoldStocksComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: TrackedStockService,
          useValue: {
            list: () => of({
              as_of: '2026-07-23',
              last_expected_session: '2026-07-24',
              prices_stale: false,
              spy_ytd_return: 5,
              spy_week_return: 1,
              spy_price: 150,
              stocks: [{
                symbol: 'AAA',
                name: 'AAA Inc.',
                category: 'Sold Stock',
                notes: 'Sold after a sharp run; watching for a better re-entry level',
                target_date: null,
                target_amount: null,
                coverage_initiation_date: '2026-01-02',
                created_at: '2026-07-23T00:00:00+00:00',
                setup: 'BULLISH_CONTINUATION',
                setup_score: 72,
                fifty_two_week_low: 10,
                fifty_two_week_high: 20,
                range_position: 50,
                has_data: true,
                partial_history: false,
                price: 15,
                price_date: '2026-07-23',
                coverage_return: 8,
                spy_coverage_return: 5,
                coverage_vs_spy: 3,
                coverage_vs_spy_snapshots: { '2026-07-22': 2.5 },
                ytd_return: 10,
                ytd_vs_spy: 5
              }],
              coverage_vs_spy_snapshots: [{
                snapshot_date: '2026-07-22', captured_at: '2026-07-22T16:00:00+00:00'
              }]
            }),
            lookupSymbols: () => of({ as_of: '2026-07-23', known: [], unknown: [] }),
            add: () => of({ stocks: [] }),
            update: () => of({ stocks: [] }),
            remove: () => of({ stocks: [] }),
            captureCoverageVsSpySnapshot: () => of({ stocks: [] })
          }
        }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(TrackingSoldStocksComponent);
    fixture.detectChanges();
  });

  it('renders tracked stock rows', () => {
    const text = fixture.nativeElement.textContent ?? '';
    expect(text).toContain('AAA');
    expect(text).toContain('Copy symbols');
    expect(text).toContain('Coverage vs SPY');
    expect(text).toContain('Snapshot Coverage vs SPY');
  });

  function setNotes(notes: string): void {
    fixture.componentInstance.rows[0].notes = notes;
    fixture.debugElement.injector.get(ChangeDetectorRef).markForCheck();
    fixture.detectChanges();
  }

  it('ellipsizes long and multiline notes without widening the table or growing the row', () => {
    setNotes('Short note');
    const element = fixture.nativeElement as HTMLElement;
    const table = element.querySelector('table')!;
    const row = element.querySelector('tbody tr')!;
    const preview = element.querySelector<HTMLElement>('.row-meta')!;
    const tableWidth = table.getBoundingClientRect().width;
    const rowHeight = row.getBoundingClientRect().height;
    for (const notes of ['Long watch-list context. '.repeat(80), 'X'.repeat(2_000), 'First line\nSecond line\n'.repeat(50)]) {
      setNotes(notes);
      const styles = getComputedStyle(preview);
      expect(styles.whiteSpace).toBe('nowrap');
      expect(styles.overflow).toBe('hidden');
      expect(styles.textOverflow).toBe('ellipsis');
      expect(preview.getBoundingClientRect().width).toBe(160);
      expect(preview.scrollWidth).toBeGreaterThan(preview.clientWidth);
      expect(table.getBoundingClientRect().width).toBeCloseTo(tableWidth, 1);
      expect(row.getBoundingClientRect().height).toBeCloseTo(rowHeight, 1);
      expect(preview.textContent).toContain(notes);
    }
  });

  it('keeps the full note available to the tooltip and edit form', async () => {
    const notes = 'Full synthetic note with a long explanation. '.repeat(20).trim();
    setNotes(notes);
    const preview = fixture.debugElement.query(By.css('.row-meta'));
    expect(preview.injector.get(MatTooltip).message).toBe(notes);
    expect(preview.nativeElement.getAttribute('tabindex')).toBe('0');
    fixture.componentInstance.openEdit(fixture.componentInstance.rows[0]);
    fixture.detectChanges();
    await fixture.whenStable();
    const textarea = (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLTextAreaElement>('textarea[name="editNotes"]')!;
    expect(textarea.value).toBe(notes);
    expect(fixture.componentInstance.rows[0].notes).toBe(notes);
    fixture.componentInstance.closeEdit();
    fixture.detectChanges();
  });

  it('does not render an empty notes preview', () => {
    setNotes('');
    expect((fixture.nativeElement as HTMLElement).querySelector('.row-meta')).toBeNull();
  });
});
