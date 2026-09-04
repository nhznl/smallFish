import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { PreEarningsExplainerComponent } from './pre-earnings-explainer.component';

describe('PreEarningsExplainerComponent', () => {
  let fixture: ComponentFixture<PreEarningsExplainerComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PreEarningsExplainerComponent],
      providers: [provideRouter([])]
    }).compileComponents();
    fixture = TestBed.createComponent(PreEarningsExplainerComponent);
    fixture.detectChanges();
  });

  it('links to Study 3 and Study 4 research tabs', () => {
    const hrefs = [...fixture.nativeElement.querySelectorAll('a')].map((el: HTMLAnchorElement) => el.getAttribute('href'));
    expect(hrefs.some(href => href?.includes('post-earnings-risk-on'))).toBeTrue();
    expect(hrefs.some(href => href?.includes('post-earnings-weekly-extension'))).toBeTrue();
    expect(fixture.nativeElement.textContent).toContain('Study 3');
    expect(fixture.nativeElement.textContent).toContain('Study 4');
    expect(fixture.nativeElement.textContent).toContain('Kill switch');
  });
});
