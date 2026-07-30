import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';

import { StudiesService } from '../api/studies.service';
import { StudiesComponent } from './studies.component';
import {
  StudyCatalog,
  StudyCatalogItem,
  StudyDetail,
  StudyVariation,
} from './study.models';

function catalogItem(id: string, overrides: Partial<StudyCatalogItem> = {}): StudyCatalogItem {
  return {
    id,
    name: `${id} name`,
    summary: `${id} summary`,
    defaultVariationId: `${id}-v1`,
    variationCount: 1,
    verdict: 'PASSED',
    evidenceLevel: 'EXPLORATORY',
    scanAvailable: false,
    updatedAt: '2026-07-28T00:00:00Z',
    ...overrides,
  };
}

function variation(id: string, overrides: Partial<StudyVariation> = {}): StudyVariation {
  return {
    id,
    name: `${id} variation`,
    thesis: `${id} thesis`,
    methodology: {
      summary: 'Method summary',
      population: 'Demo population',
      inclusionCriteria: ['Included'],
      exclusionCriteria: ['Excluded'],
      features: ['Feature'],
      endpoint: 'Endpoint',
      controls: ['Control'],
      period: '2020–2025',
      inference: 'Inference',
      limitations: ['Limitation'],
    },
    outcome: {
      verdict: 'PASSED',
      evidenceLevel: 'EXPLORATORY',
      summary: 'Outcome summary',
      whatWorked: ['Worked'],
      whatDidNotWork: ['Did not'],
      nextSteps: ['Next'],
      moreData: { assessment: false, rationale: 'Enough data.' },
    },
    stats: [{
      id: 'hit-rate',
      label: 'Hit rate',
      value: 0.5,
      format: 'PERCENT',
      precision: 1,
      scope: 'Holdout',
      interpretation: 'Neutral',
      priority: 'PRIMARY',
    }],
    scan: null,
    provenance: {
      specificationPath: 'studies/demo/spec.md',
      artifactPath: 'data/studies/demo.json',
      runId: 'demo-run',
      sourceCommit: null,
      generatedAt: '2026-07-28T00:00:00Z',
      dataCutoff: null,
      verificationState: 'verified',
    },
    caveats: ['Demo caveat'],
    ...overrides,
  };
}

function study(id: string, overrides: Partial<StudyDetail> = {}): StudyDetail {
  const defaultVariationId = `${id}-v1`;
  return {
    schemaName: 'smallfish.research-study',
    schemaVersion: 1,
    id,
    name: `${id} name`,
    summary: `${id} summary`,
    updatedAt: '2026-07-28T00:00:00Z',
    defaultVariationId,
    variations: [variation(defaultVariationId)],
    ...overrides,
  };
}

function catalog(...ids: string[]): StudyCatalog {
  return {
    schemaName: 'smallfish.research-study',
    schemaVersion: 1,
    studies: ids.map(id => catalogItem(id)),
  };
}

describe('StudiesComponent', () => {
  let fixture: ComponentFixture<StudiesComponent>;
  let paramMap$: BehaviorSubject<ParamMap>;
  let studiesService: jasmine.SpyObj<StudiesService>;

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function mount(): void {
    fixture = TestBed.createComponent(StudiesComponent);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    paramMap$ = new BehaviorSubject<ParamMap>(convertToParamMap({ studyId: 'demo-study' }));
    studiesService = jasmine.createSpyObj<StudiesService>('StudiesService', [
      'getCatalog',
      'getStudy',
      'runScan',
      'getScan',
    ]);
    studiesService.getCatalog.and.returnValue(of(catalog('demo-study', 'other-study')));
    studiesService.getStudy.and.callFake((id: string) => of(study(id)));
    studiesService.getScan.and.returnValue(of({
      schemaName: 'pre-earnings-candidates-v1',
      generatedAt: '',
      candidates: [],
    }));
    studiesService.runScan.and.returnValue(of({ status: 'ok' }));

    await TestBed.configureTestingModule({
      imports: [StudiesComponent],
      providers: [
        provideRouter([]),
        { provide: StudiesService, useValue: studiesService },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: paramMap$.asObservable(),
            snapshot: {
              queryParamMap: convertToParamMap({}),
            },
          },
        },
      ],
    }).compileComponents();
  });

  it('loads the catalog and selected study', () => {
    mount();
    expect(text()).toContain('demo-study name');
    expect(text()).toContain('demo-study summary');
    expect(text()).toContain('demo-study-v1 thesis');
    expect(studiesService.getCatalog).toHaveBeenCalled();
    expect(studiesService.getStudy).toHaveBeenCalledWith('demo-study');
  });

  it('surfaces catalog failure without pretending studies are empty', () => {
    studiesService.getCatalog.and.returnValue(
      throwError(() => ({ message: 'catalog missing' }))
    );
    mount();
    expect(text()).toContain('Research Studies are unavailable');
    expect(text()).not.toContain('No research studies are published');
    expect(studiesService.getStudy).not.toHaveBeenCalled();
  });

  it('surfaces a missing study record without keeping prior evidence', () => {
    studiesService.getStudy.and.returnValue(
      throwError(() => ({ status: 404, message: 'Not Found' }))
    );
    mount();
    expect(text()).toContain('This study record is missing or invalid');
    expect(text()).not.toContain('demo-study-v1 thesis');
  });

  it('ignores a slower study response from a previous route param', () => {
    const demoStudy$ = new Subject<StudyDetail>();
    const otherStudy$ = new Subject<StudyDetail>();
    studiesService.getStudy.and.callFake((id: string) => {
      if (id === 'demo-study') {
        return demoStudy$.asObservable();
      }
      return otherStudy$.asObservable();
    });

    mount();
    expect(studiesService.getStudy).toHaveBeenCalledWith('demo-study');

    paramMap$.next(convertToParamMap({ studyId: 'other-study' }));
    fixture.detectChanges();
    expect(studiesService.getStudy).toHaveBeenCalledWith('other-study');

    otherStudy$.next(study('other-study', {
      name: 'other-study name',
      summary: 'other-study summary',
      variations: [variation('other-study-v1', {
        thesis: 'other thesis',
        stats: [{
          id: 'hit-rate',
          label: 'Hit rate',
          value: 0.55,
          format: 'PERCENT',
          precision: 1,
          scope: 'Holdout',
          interpretation: 'Neutral',
          priority: 'PRIMARY',
        }],
      })],
    }));
    otherStudy$.complete();
    fixture.detectChanges();
    expect(text()).toContain('other-study name');
    expect(text()).toContain('other thesis');

    demoStudy$.next(study('demo-study', {
      name: 'STALE demo name',
      variations: [variation('demo-study-v1', {
        thesis: 'STALE thesis',
        stats: [{
          id: 'hit-rate',
          label: 'Hit rate',
          value: 0.99,
          format: 'PERCENT',
          precision: 1,
          scope: 'Holdout',
          interpretation: 'Stale',
          priority: 'PRIMARY',
        }],
      })],
    }));
    demoStudy$.complete();
    fixture.detectChanges();

    expect(text()).toContain('other-study name');
    expect(text()).toContain('other thesis');
    expect(text()).not.toContain('STALE');
    expect(text()).not.toContain('+99.0%');
    expect(fixture.componentInstance.study?.id).toBe('other-study');
  });
});
