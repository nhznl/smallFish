import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { PortfolioService } from '../api/portfolio.service';
import {
  PortfolioDetailResponse,
  PortfolioListResponse,
  PortfolioSummary,
} from '../model/portfolio';
import { PortfoliosComponent } from './portfolios.component';

function summary(overrides: Partial<PortfolioSummary> = {}): PortfolioSummary {
  return {
    id: 'p1',
    name: 'Demo Book',
    description: '',
    sector: 'Technology',
    industry: '',
    created_date: '2026-01-15',
    created_at: '2026-01-15T15:00:00Z',
    symbol_count: 1,
    symbols: ['DEMO'],
    avg_price: 50,
    avg_price_prior_week: 49,
    week_return: 0.02,
    inception_return: 0.1,
    spy_inception_return: 0.05,
    inception_vs_spy: 0.05,
    ytd_return: 0.08,
    spy_ytd_return: 0.04,
    ytd_vs_spy: 0.04,
    missing_data_symbols: [],
    partial_history_symbols: [],
    ...overrides,
  };
}

function listResponse(portfolios: PortfolioSummary[] = [summary()]): PortfolioListResponse {
  return {
    as_of: '2026-07-28',
    last_expected_session: '2026-07-28',
    prices_stale: false,
    spy_ytd_return: 0.04,
    spy_week_return: 0.01,
    spy_price: 500,
    portfolios,
  };
}

function detailResponse(
  portfolio: PortfolioSummary = summary()
): PortfolioDetailResponse {
  return {
    ...listResponse([portfolio]),
    portfolio,
    members: [{
      symbol: 'DEMO',
      has_data: true,
      price: 50,
      price_date: '2026-07-28',
      week_return: 0.02,
      fifty_two_week_low: 40,
      fifty_two_week_high: 60,
      range_position: 50,
      ytd_return: 0.08,
      inception_return: 0.1,
      inception_baseline_date: '2026-01-15',
      inception_baseline_close: 45,
      partial_history: false,
      added_date: '2026-01-15',
      price_at_add: 45,
    }],
  };
}

describe('PortfoliosComponent', () => {
  let fixture: ComponentFixture<PortfoliosComponent>;
  let api: jasmine.SpyObj<PortfolioService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(PortfoliosComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    api = jasmine.createSpyObj<PortfolioService>('PortfolioService', [
      'list',
      'detail',
      'sectors',
      'lookupSymbols',
      'create',
      'update',
      'remove',
      'addSymbols',
      'removeSymbol',
    ]);
    api.list.and.returnValue(of(listResponse()));
    api.sectors.and.returnValue(of({ sectors: ['Technology'] }));
    api.detail.and.returnValue(of(detailResponse()));
    api.create.and.returnValue(of(detailResponse(summary({ id: 'p2', name: 'New Book' }))));
    api.remove.and.returnValue(of({ deleted: 'p1', name: 'Demo Book' }));
    api.addSymbols.and.callFake((_id: string, symbols: string[]) =>
      of(detailResponse(summary({
        symbols: ['DEMO', ...symbols],
        symbol_count: 1 + symbols.length,
      })))
    );
    api.removeSymbol.and.returnValue(of(detailResponse(summary({
      symbols: [],
      symbol_count: 0,
    }))));

    await TestBed.configureTestingModule({
      imports: [PortfoliosComponent],
      providers: [
        provideRouter([]),
        { provide: PortfolioService, useValue: api },
      ],
    }).compileComponents();
  });

  it('loads the portfolio list', () => {
    mount();
    expect(text()).toContain('Demo Book');
    expect(api.list).toHaveBeenCalled();
  });

  it('shows a list load error instead of an empty book shelf', () => {
    api.list.and.returnValue(throwError(() => ({ message: 'Unable to load portfolios.' })));
    mount();
    expect(text()).toContain('Unable to load portfolios.');
  });

  it('posts create payloads and refreshes the list on success', () => {
    mount();
    fixture.componentInstance.openCreate();
    fixture.componentInstance.createName = 'New Book';
    fixture.componentInstance.createEntry.chips = [
      { symbol: 'DEMO', name: 'Demo', known: true, price: 50 },
    ];
    fixture.detectChanges();

    fixture.componentInstance.submitCreate();
    fixture.detectChanges();

    expect(api.create).toHaveBeenCalledWith(jasmine.objectContaining({
      name: 'New Book',
      symbols: ['DEMO'],
    }));
    expect(api.list.calls.count()).toBeGreaterThan(1);
  });

  it('surfaces createError when create fails', () => {
    mount();
    fixture.componentInstance.openCreate();
    fixture.componentInstance.createName = 'New Book';
    fixture.componentInstance.createEntry.chips = [
      { symbol: 'DEMO', name: 'Demo', known: true, price: 50 },
    ];
    api.create.and.returnValue(throwError(() => ({ message: 'Name already used' })));
    fixture.componentInstance.submitCreate();
    fixture.detectChanges();

    expect(fixture.componentInstance.createError).toContain('Name already used');
    expect(text()).toContain('Name already used');
  });

  it('deletes from the confirm modal and refreshes the list once', () => {
    mount();
    fixture.componentInstance.detail = detailResponse();
    fixture.componentInstance.deleteOpen = true;
    fixture.detectChanges();

    const listCallsBefore = api.list.calls.count();
    fixture.componentInstance.deletePortfolio();
    fixture.detectChanges();

    expect(api.remove).toHaveBeenCalledOnceWith('p1');
    expect(api.list.calls.count()).toBe(listCallsBefore + 1);
    expect(fixture.componentInstance.detail).toBeNull();
    expect(fixture.componentInstance.deleteOpen).toBeFalse();
  });

  it('keeps addError distinct when adding a symbol fails', () => {
    mount();
    fixture.componentInstance.detail = detailResponse();
    fixture.componentInstance.addEntry.raw = 'ZZZZ';
    fixture.componentInstance.addEntry.chips = [
      { symbol: 'ZZZZ', name: '', known: true, price: 1 },
    ];
    api.addSymbols.and.returnValue(throwError(() => ({ message: 'Symbol rejected' })));
    fixture.componentInstance.addMembers();
    fixture.detectChanges();
    expect(fixture.componentInstance.addError).toContain('Symbol rejected');
    expect(fixture.componentInstance.memberError).toBeNull();
  });
});
