import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { StockService } from '../api/stock.service';
import { SectorRotationSnapshot } from '../model/sector-rotation';
import { SectorRotationComponent } from './sector-rotation.component';

function snapshot(overrides: Partial<SectorRotationSnapshot> = {}): SectorRotationSnapshot {
  return {
    available: true,
    asOf: '2026-07-28',
    windows: [5, 20, 60],
    sectors: [{
      schemaVersion: 1,
      asOf: '2026-07-28',
      symbol: 'XLK',
      sector: 'Technology',
      windowSessions: 20,
      windowStart: '2026-06-30',
      windowEnd: '2026-07-28',
      totalReturn: 0.05,
      benchmarkReturn: 0.02,
      excessReturn: 0.03,
      rank: 1,
      rankOf: 11,
      percentile: 1,
      priorExcessReturn: 0.01,
      priorRank: 2,
      rankChange: 1,
      rsChange: 0.02,
      leadershipState: 'LEADING',
      rsTrend: 'IMPROVING',
      volumeWindowAvg: 1,
      volumeBaselineAvg: 1,
      volumeRatio: 1.1,
      volumeConfirms: true,
    }],
    pairs: [],
    rotationCandidates: [],
    ...overrides,
  };
}

describe('SectorRotationComponent', () => {
  let fixture: ComponentFixture<SectorRotationComponent>;
  let stockService: jasmine.SpyObj<StockService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(SectorRotationComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    stockService = jasmine.createSpyObj<StockService>('StockService', [
      'getSectorRotation',
      'runSectorRotation',
    ]);
    stockService.getSectorRotation.and.returnValue(of(snapshot()));
    stockService.runSectorRotation.and.returnValue(of({ status: 'ok', durationMs: 500 }));

    await TestBed.configureTestingModule({
      imports: [SectorRotationComponent],
      providers: [
        provideRouter([]),
        { provide: StockService, useValue: stockService },
      ],
    }).compileComponents();
  });

  it('shows a load error when the snapshot request fails', () => {
    stockService.getSectorRotation.and.returnValue(
      throwError(() => ({ error: { detail: 'Snapshot missing' } }))
    );
    mount();
    expect(text()).toContain('Snapshot missing');
    expect(fixture.componentInstance.snapshot).toBeNull();
  });

  it('treats a null snapshot payload as a load error', () => {
    stockService.getSectorRotation.and.returnValue(of(null as unknown as SectorRotationSnapshot));
    mount();
    expect(text()).toContain('Could not load the sector-rotation snapshot.');
  });

  it('keeps the prior snapshot and shows job-error when recompute fails', () => {
    mount();
    expect(text()).toContain('Technology');

    stockService.runSectorRotation.and.returnValue(of({
      status: 'error',
      message: 'Cache incomplete',
    }));
    const beforeLoads = stockService.getSectorRotation.calls.count();
    fixture.componentInstance.run();
    fixture.detectChanges();

    expect(fixture.componentInstance.runStatus).toBe('error');
    expect(fixture.componentInstance.runStatusClass()).toBe('job-error');
    expect(text()).toContain('Recompute failed');
    expect(text()).toContain('Technology');
    expect(stockService.getSectorRotation.calls.count()).toBe(beforeLoads);
  });

  it('reloads the snapshot after a successful recompute', () => {
    mount();
    const beforeLoads = stockService.getSectorRotation.calls.count();
    fixture.componentInstance.run();
    fixture.detectChanges();
    expect(fixture.componentInstance.runStatus).toBe('ok');
    expect(stockService.getSectorRotation.calls.count()).toBeGreaterThan(beforeLoads);
  });
});
