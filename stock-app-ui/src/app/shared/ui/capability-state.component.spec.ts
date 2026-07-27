import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CapabilityStateComponent } from './capability-state.component';
import { Capability } from '../../api/capability.service';

function capability(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'tastytrade',
    label: 'Tastytrade',
    provides: 'the options ledger',
    state: 'NOT_CONFIGURED',
    available: false,
    reason: 'Tastytrade is not connected.',
    action: './setup-brokerages.sh setup tastytrade',
    provider: 'Tastytrade',
    docs: 'docs/BROKERAGES.md',
    requires: { TT_CLIENT_SECRET: false, TT_REFRESH_TOKEN: false },
    ...overrides
  };
}

describe('CapabilityStateComponent', () => {
  let fixture: ComponentFixture<CapabilityStateComponent>;
  let component: CapabilityStateComponent;

  const text = () => fixture.nativeElement.textContent as string;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [CapabilityStateComponent] })
      .compileComponents();
    fixture = TestBed.createComponent(CapabilityStateComponent);
    component = fixture.componentInstance;
  });

  it('renders nothing when the capability is unknown', () => {
    component.capability = null;
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.capability-state')).toBeNull();
  });

  it('renders nothing when the capability is available and has data', () => {
    component.capability = capability({ available: true, state: 'CONFIGURED' });
    component.emptyWhenAvailable = false;
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.capability-state')).toBeNull();
  });

  describe('not configured', () => {
    beforeEach(() => {
      component.capability = capability();
      fixture.detectChanges();
    });

    it('names the provider, the benefit, and the exact setup command', () => {
      expect(text()).toContain('Tastytrade is not set up');
      expect(text()).toContain('the options ledger');
      expect(text()).toContain('Tastytrade is not connected.');
      expect(text()).toContain('./setup-brokerages.sh setup tastytrade');
    });

    it('reassures the user that the feature is optional', () => {
      expect(text()).toContain('Every other part of smallFish works without it');
    });

    it('links the setup documentation', () => {
      expect(text()).toContain('docs/BROKERAGES.md');
    });

    it('pairs the badge colour with a text label, never colour alone', () => {
      const badge = fixture.nativeElement.querySelector('.badge');
      expect(badge.textContent.trim()).toBe('Optional — not set up');
      expect(badge.classList).toContain('badge-info');
    });

    it('exposes the state to assistive technology', () => {
      expect(fixture.nativeElement.querySelector('[role="status"]')).not.toBeNull();
    });
  });

  describe('configured but empty', () => {
    beforeEach(() => {
      component.capability = capability({ available: true, state: 'CONFIGURED' });
      component.emptyWhenAvailable = true;
      component.availableEmptyText = 'No open broker option positions.';
      component.syncLabel = 'Sync Tastytrade';
      fixture.detectChanges();
    });

    it('shows the view-specific empty message, not the setup instruction', () => {
      expect(text()).toContain('No open broker option positions.');
      expect(text()).not.toContain('./setup-brokerages.sh');
    });

    it('offers the sync and labels the state', () => {
      expect(text()).toContain('Sync Tastytrade');
      expect(fixture.nativeElement.querySelector('.badge').textContent.trim())
        .toBe('No data yet');
    });

    it('does not claim the feature is optional once it is configured', () => {
      expect(text()).not.toContain('Every other part of smallFish');
    });
  });

  describe('partially configured', () => {
    it('warns and names the missing setting', () => {
      component.capability = capability({
        state: 'INCOMPLETE',
        reason: 'Tastytrade is partially configured: TT_REFRESH_TOKEN is missing.'
      });
      fixture.detectChanges();

      expect(text()).toContain('TT_REFRESH_TOKEN is missing');
      const badge = fixture.nativeElement.querySelector('.badge');
      expect(badge.textContent.trim()).toBe('Partly configured');
      expect(badge.classList).toContain('badge-warn');
    });

    it('treats an unfinished registration as a warning too', () => {
      component.capability = capability({ id: 'snaptrade', state: 'NEEDS_REGISTRATION' });
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.badge').textContent.trim())
        .toBe('Setup unfinished');
    });
  });

  describe('error', () => {
    beforeEach(() => {
      component.capability = capability({
        state: 'ERROR',
        reason: "TT_ENV must be 'sandbox' or 'live', found 'production'.",
        action: 'Correct TT_ENV in app.env'
      });
      fixture.detectChanges();
    });

    it('shows the safe remediation and a negative badge', () => {
      expect(text()).toContain('Tastytrade is not usable right now');
      expect(text()).toContain('Correct TT_ENV in app.env');
      const badge = fixture.nativeElement.querySelector('.badge');
      expect(badge.textContent.trim()).toBe('Error');
      expect(badge.classList).toContain('badge-neg');
    });
  });

  describe('core data not bootstrapped', () => {
    it('points at the bootstrap command rather than a blank table', () => {
      component.capability = capability({
        id: 'core-data', label: 'Market data', provider: 'Yahoo Finance',
        provides: 'stock and ETF price history',
        reason: 'No price data has been downloaded yet.',
        action: './commands.sh bootstrap-data', docs: 'docs/DATA.md'
      });
      fixture.detectChanges();

      expect(text()).toContain('Market data is not set up');
      expect(text()).toContain('./commands.sh bootstrap-data');
      expect(text()).toContain('docs/DATA.md');
    });
  });

  it('never renders a value from the requires map', () => {
    component.capability = capability({
      requires: { TT_CLIENT_SECRET: true, TT_REFRESH_TOKEN: false }
    });
    fixture.detectChanges();
    expect(text()).not.toContain('TT_CLIENT_SECRET');
  });
});
