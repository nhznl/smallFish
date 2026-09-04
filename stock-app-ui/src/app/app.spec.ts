import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { App } from './app';
import { routes } from './app.routes';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the nav', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect([...compiled.querySelectorAll('nav a')].map(link => link.textContent?.trim())).toEqual([
      'Momentum',
      'Wheel',
      'Sectors',
      'Studies',
      'Pre-Earnings Momentum',
      'Trading',
      'Retirement',
      'Portfolios',
      'Tracking'
    ]);
    expect([...compiled.querySelectorAll('.nav-group-label')].map(el => el.textContent?.trim())).toEqual([
      'Research',
      'Strategies',
      'Ledgers',
      'Tracking'
    ]);
  });

  it('should expose only the Studies routes after legacy removal', () => {
    const paths = routes.map(route => route.path);
    expect(paths).toContain('studies');
    expect(paths).toContain('studies/:studyId');
    expect(paths).toContain('study4');
    expect(paths).toContain('preEarningsExplainer');
    expect(paths).not.toContain('strategy');
    expect(paths).not.toContain('strategyExplainer');
  });
});
