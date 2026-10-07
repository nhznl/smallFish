import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Subject, throwError } from 'rxjs';
import { MarketCalendarService } from '../api/market-calendar.service';
import { MarketCalendarJobResult } from '../model/job-results';
import { MarketEvent, MarketEventCollection } from '../model/market-event';
import { MarketCalendarComponent } from './market-calendar.component';

function event(overrides: Partial<MarketEvent> = {}): MarketEvent {
  return {
    id: 'US:BLS:CPI:2026-09',
    canonicalKey: 'US:BLS:CPI:2026-09',
    title: 'Consumer Price Index',
    eventType: 'CPI',
    category: 'inflation',
    referencePeriod: '2026-09',
    referencePeriodLabel: 'September 2026',
    scheduledAtUtc: '2026-10-14T12:30:00Z',
    easternTime: '8:30 AM ET',
    pacificTime: '5:30 AM PT',
    easternDate: '2026-10-14',
    pacificDate: '2026-10-14',
    civilDate: '2026-10-14',
    timePrecision: 'exact',
    scheduleStatus: 'confirmed',
    lifecycleStatus: 'scheduled',
    reviewRequired: false,
    reviewReason: null,
    portfolioRelevance: 'DIRECT_BROAD_INDEX',
    priority: 'primary',
    importance: { score: 5, label: 'Extreme', ruleIds: ['importance-cpi-extreme'], policyVersion: '2026-10-04.1' },
    affectedAssetClasses: ['equities', 'rates'],
    affectedInstruments: ['SPY', 'QQQ'],
    etfExposures: [
      { symbol: 'SPY', relationship: 'direct_underlying', relevance: 'primary', impactChannel: 'Direction is unknown.', priceDataState: 'current', latestPriceSession: '2026-10-02' },
      { symbol: 'TIP', relationship: 'rate_sensitive', relevance: 'secondary', impactChannel: 'Rates channel.', priceDataState: 'missing', latestPriceSession: null }
    ],
    measurements: [
      { metric: 'CPI_U_ALL_ITEMS_MOM', label: 'CPI-U all items', value: '4.44', numericValue: '4.44', unit: 'percent', scale: 'month_over_month', seasonalAdjustment: 'seasonally_adjusted', valueKind: 'actual', provider: 'synthetic', observedAtUtc: null }
    ],
    strategyAssessments: [
      { strategyId: 'short-premium-20d-0dte', assessment: 'high_risk', timingRelationship: 'before_entry', potentialBenefits: ['Option premium may be elevated. Elevated premium is not an edge.'], potentialRisks: ['A large surprise can continue moving after the open.'], ruleIds: ['s1-broad-high-while-exposed'], policyVersion: '2026-10-04.1' },
      { strategyId: 'long-premium-20d-0-or-1dte', assessment: 'mixed_opportunity', timingRelationship: 'before_entry', potentialBenefits: ['Price can keep moving after the open.'], potentialRisks: ['Movement is not profit.'], ruleIds: ['s2-broad-before-entry'], policyVersion: '2026-10-04.1' },
      { strategyId: 'long-wings-repeated-0dte-premium', assessment: 'high_risk', timingRelationship: 'before_entry', potentialBenefits: ['The longer-dated wings can help in a tail move.'], potentialRisks: ['Different expirations mean maximum loss is not simply strike width minus credit.'], ruleIds: ['s3-broad-while-exposed'], policyVersion: '2026-10-04.1' }
    ],
    sources: [{ provider: 'bls', sourceRecordId: 'synthetic', sourceUrl: 'https://www.bls.gov/schedule/news_release/bls.ics', fetchedAtUtc: '2026-10-04T15:00:00Z', parserVersion: 'bls-ics-1' }],
    relatedEventIds: [],
    ...overrides
  };
}

function collection(overrides: Partial<MarketEventCollection> = {}): MarketEventCollection {
  const cpi = event();
  return {
    available: true,
    schemaVersion: 1,
    generatedAtUtc: '2026-10-04T15:00:00Z',
    asOfDate: '2026-10-14',
    horizon: { from: '2026-10-14', to: '2026-11-14' },
    timezone: 'America/New_York',
    coverageStatus: 'fresh',
    caveat: 'An empty day is not an all-clear. Nothing here predicts whether prices will rise or fall.',
    sources: [{
      provider: 'bls', status: 'fresh', required: true, configured: true, scope: 'cpi_schedule',
      detail: 'CPI only.', coverageStart: '2026-10-14', coverageEnd: '2026-11-14',
      lastAttemptUtc: '2026-10-04T15:00:00Z', lastSuccessUtc: '2026-10-04T15:00:00Z',
      resultCount: 1, errorCategory: null, parserVersion: 'bls-ics-1'
    }],
    summary: { horizonDays: 32, primaryRiskDays: 1, highOrExtremeEvents: 1, secondaryEvents: 0 },
    days: [{
      date: '2026-10-14', sessionState: 'regular', riskLabel: 'EXTREME', eventCount: 1,
      clusterWarning: null, sessionAssessment: '0-DTE short premium is high risk.',
      coverage: 'covered', primaryEvents: [cpi], secondaryEvents: []
    }, {
      date: '2026-10-15', sessionState: 'regular', riskLabel: 'ROUTINE', eventCount: 0,
      clusterWarning: null, sessionAssessment: 'No direct SPY/QQQ release is recorded.',
      coverage: 'covered_empty', primaryEvents: [], secondaryEvents: []
    }],
    scannedAtUtc: '2026-10-04T15:00:00Z',
    ...overrides
  };
}

describe('MarketCalendarComponent', () => {
  let fixture: ComponentFixture<MarketCalendarComponent>;
  let requests: Subject<MarketEventCollection>;
  let runRequests: Subject<MarketCalendarJobResult>;

  beforeEach(async () => {
    requests = new Subject<MarketEventCollection>();
    runRequests = new Subject<MarketCalendarJobResult>();
    await TestBed.configureTestingModule({
      imports: [MarketCalendarComponent],
      providers: [{
        provide: MarketCalendarService,
        useValue: {
          getCollection: () => requests.asObservable(),
          runMarketCalendar: () => runRequests.asObservable()
        }
      }]
    }).compileComponents();
    fixture = TestBed.createComponent(MarketCalendarComponent);
    fixture.detectChanges();
  });

  function text(): string {
    return fixture.nativeElement.textContent;
  }

  it('shows the CPI risk day, an em dash for missing consensus, and no direction call', () => {
    requests.next(collection());
    fixture.detectChanges();
    expect(text()).toContain('Wednesday, October 14');
    expect(text()).toContain('EXTREME RISK');
    expect(text()).toContain('5:30 AM PT');
    expect(text()).toContain('8:30 AM ET');
    expect(text()).toContain('— / 4.44');
    expect(text()).not.toContain('bullish');
    expect(text()).not.toContain('bearish');
    expect(text()).toContain('Primary risk days');
    expect(text()).toContain('equities, rates');
    expect(text()).toContain('1');
  });

  it('renders an unavailable scan as em dashes rather than zero risk days', () => {
    requests.next(collection({
      available: false,
      coverageStatus: 'unavailable',
      generatedAtUtc: null,
      horizon: null,
      summary: { horizonDays: null, primaryRiskDays: null, highOrExtremeEvents: null, secondaryEvents: null },
      days: []
    }));
    fixture.detectChanges();
    expect(text()).toContain('The primary event-risk scan is unavailable');
    const stats = [...fixture.nativeElement.querySelectorAll('.stat-value')].map(node => node.textContent?.trim());
    expect(stats).toContain('—');
    expect(stats).not.toContain('0');
  });

  it('keeps a stale required source next to the result', () => {
    const stale = collection();
    stale.sources[0].status = 'stale';
    stale.coverageStatus = 'stale';
    requests.next(stale);
    fixture.detectChanges();
    expect(text()).toContain('Primary-calendar coverage is incomplete');
    expect(text()).toContain('stale');
  });

  it('filters to today and opens the shared drawer with all three strategies', () => {
    requests.next(collection());
    fixture.detectChanges();
    const tomorrow = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Tomorrow'
    ) as HTMLButtonElement;
    tomorrow.click();
    fixture.detectChanges();
    expect(text()).toContain('No covered primary risk days in this range');

    const today = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Today'
    ) as HTMLButtonElement;
    today.click();
    fixture.detectChanges();
    const row = fixture.nativeElement.querySelector('.row-button') as HTMLButtonElement;
    row.click();
    fixture.detectChanges();
    const drawer = text();
    expect(drawer).toContain('Short premium, 20 delta, 0 DTE');
    expect(drawer).toContain('Long premium, 20 delta, 0 or 1 DTE');
    expect(drawer).toContain('Long wings with repeated 0 DTE premium');
    expect(drawer).toContain('Different expirations mean maximum loss is not simply strike width minus credit.');
    expect(drawer).toContain('Movement is not profit.');
    expect(drawer).toContain('missing');
    expect(drawer).toContain('Freshness: fresh');
    expect(drawer).toContain('Coverage 2026-10-14 to 2026-11-14');
    expect(drawer).toContain('CPI only.');
    expect(drawer).toContain('Last successful refresh: 2026-10-04T15:00:00Z');
    expect(drawer).not.toContain('bullish');
  });

  it('uses the Market Calendar action and keeps the last snapshot visible while it runs', () => {
    requests.next(collection());
    fixture.detectChanges();
    const action = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Market Calendar'
    ) as HTMLButtonElement;
    expect(text()).not.toContain('Reload scan');
    action.click();
    fixture.detectChanges();
    expect(text()).toContain('last successful snapshot');
    expect(text()).toContain('Consumer Price Index');
    expect(action.disabled).toBeTrue();
  });

  it('reloads the published data after the daily calendar is reused', () => {
    requests.next(collection());
    fixture.detectChanges();
    const action = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Market Calendar'
    ) as HTMLButtonElement;

    action.click();
    runRequests.next({
      status: 'ok', reused: true,
      message: 'Today’s market calendar was already generated; the published data was reused.'
    });
    requests.next(collection({ generatedAtUtc: '2026-10-05T15:00:00Z' }));
    fixture.detectChanges();

    expect(text()).toContain('already generated');
    expect(fixture.componentInstance.running).toBeFalse();
    expect(fixture.componentInstance.reloading).toBeFalse();
    expect(action.disabled).toBeFalse();
  });

  it('keeps the last snapshot and shows a job failure', () => {
    requests.next(collection());
    fixture.detectChanges();
    const action = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Market Calendar'
    ) as HTMLButtonElement;

    action.click();
    runRequests.next({ status: 'error', message: 'Provider refresh failed.' });
    fixture.detectChanges();

    expect(text()).toContain('Provider refresh failed.');
    expect(text()).toContain('Consumer Price Index');
    expect(fixture.componentInstance.runStatus).toBe('error');
  });

  it('filters secondary events and keeps exposure channels and price states visible', () => {
    const petroleum = event({
      id: 'US:EIA:EIA_PETROLEUM_STATUS:2026-10-09',
      canonicalKey: 'US:EIA:EIA_PETROLEUM_STATUS:2026-10-09',
      title: 'Weekly Petroleum Status Report',
      eventType: 'EIA_PETROLEUM_STATUS',
      category: 'energy',
      portfolioRelevance: 'SECTOR_OR_COMMODITY',
      priority: 'secondary',
      importance: { score: 3, label: 'Moderate', ruleIds: ['importance-eia-petroleum-moderate'], policyVersion: '2026-10-04.1' },
      etfExposures: [
        { symbol: 'USO', relationship: 'direct_underlying', relevance: 'primary', impactChannel: 'Oil channel.', priceDataState: 'current', latestPriceSession: '2026-10-13' },
        { symbol: 'XLE', relationship: 'sector_equities', relevance: 'secondary', impactChannel: 'Sector channel.', priceDataState: 'stale', latestPriceSession: '2026-10-01' },
        { symbol: 'XOP', relationship: 'industry_equities', relevance: 'indirect', impactChannel: 'Industry channel.', priceDataState: 'missing', latestPriceSession: null }
      ]
    });
    const scan = collection();
    scan.days[0].secondaryEvents = [petroleum];
    scan.days[0].eventCount = 2;
    scan.summary.secondaryEvents = 1;
    requests.next(scan);
    fixture.detectChanges();

    expect(text()).toContain('Secondary events');
    expect(text()).toContain('Weekly Petroleum Status Report');
    expect(text()).toContain('Direct:');
    expect(text()).toContain('Sector:');
    expect(text()).toContain('Industry:');
    expect(text()).toContain('Direct');
    expect(text()).toContain('Sector');
    expect(text()).toContain('Indirect');
    expect(text()).toContain('current');
    expect(text()).toContain('stale');
    expect(text()).toContain('missing');

    const secondary = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Secondary'
    ) as HTMLButtonElement;
    secondary.click();
    fixture.detectChanges();
    expect(text()).not.toContain('Consumer Price Index');
    expect(text()).toContain('Weekly Petroleum Status Report');
  });

  it('keeps secondary empty results distinct from primary coverage and optional gaps', () => {
    const scan = collection();
    scan.sources.push({
      provider: 'eia_natural_gas', status: 'unknown', required: false, configured: true,
      scope: 'weekly_natural_gas_storage_schedule', detail: 'Optional fixture omitted.',
      coverageStart: null, coverageEnd: null, lastAttemptUtc: '2026-10-04T15:00:00Z',
      lastSuccessUtc: null, resultCount: 0, errorCategory: null,
      parserVersion: 'eia-natural-gas-schedule-html-1'
    });
    requests.next(scan);
    fixture.detectChanges();

    const secondary = [...fixture.nativeElement.querySelectorAll('button')].find(
      (button: HTMLButtonElement) => button.textContent?.trim() === 'Secondary'
    ) as HTMLButtonElement;
    secondary.click();
    fixture.detectChanges();

    expect(text()).toContain('No secondary events in this range');
    expect(text()).toContain('Primary-calendar day coverage is reported separately');
    expect(text()).toContain('1 optional secondary source has a coverage gap');
    expect(text()).not.toContain('No covered primary risk days in this range');
    expect(text()).not.toContain('complete configured-source coverage');
  });

  it('shows a load failure without inventing an empty calendar', () => {
    const service = TestBed.inject(MarketCalendarService);
    (service as unknown as { getCollection: () => unknown }).getCollection = () => throwError(() => ({ error: { detail: 'unreadable' } }));
    fixture.componentInstance.load(true);
    fixture.detectChanges();
    expect(text()).toContain('unreadable');
    expect(text()).not.toContain('EXTREME');
  });
});
