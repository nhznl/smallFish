import { WheelCandidatesViewModel } from './wheel-candidates.view-model';
import { WheelCandidate } from '../model/wheel-candidate';

function candidate(symbol: string): WheelCandidate {
  return {
    type: 'STOCK',
    trendAvailable: true,
    trendDirection: 'BULLISH',
    wheel: {
      schemaVersion: 1,
      runMode: 'full',
      symbol,
      asOf: '2026-07-28',
      horizonDte: 37,
      horizonSessions: 26,
      priceAsOf: '2026-07-28',
      lastClose: 50,
      rvPercentile252: 80,
      sampleCount: 20,
      dataQuality: 'OK',
      rvWindowSessions: 21,
    },
  };
}

describe('WheelCandidatesViewModel', () => {
  it('marks empty when the service returns no rows', () => {
    const vm = new WheelCandidatesViewModel();
    vm.applyCandidates([]);
    expect(vm.state()).toBe('empty');
    expect(vm.loadError()).toBeFalse();
    expect(vm.candidates()).toEqual([]);
  });

  it('marks ready with run mode from the first row', () => {
    const vm = new WheelCandidatesViewModel();
    vm.applyCandidates([candidate('DEMO')]);
    expect(vm.state()).toBe('ready');
    expect(vm.runMode()).toBe('full');
    expect(vm.candidates().length).toBe(1);
  });

  it('marks failed and clears rows on transport error', () => {
    const vm = new WheelCandidatesViewModel();
    vm.applyCandidates([candidate('DEMO')]);
    vm.markFailed();
    expect(vm.state()).toBe('failed');
    expect(vm.loadError()).toBeTrue();
    expect(vm.candidates()).toEqual([]);
    expect(vm.runMode()).toBe('');
  });
});
