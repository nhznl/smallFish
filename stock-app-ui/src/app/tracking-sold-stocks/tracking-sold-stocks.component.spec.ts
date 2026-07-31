import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
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
                ytd_return: 10,
                ytd_vs_spy: 5
              }]
            }),
            lookupSymbols: () => of({ as_of: '2026-07-23', known: [], unknown: [] }),
            add: () => of({ stocks: [] }),
            update: () => of({ stocks: [] }),
            remove: () => of({ stocks: [] })
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
  });
});
