import {
  formatFixedPercent,
  formatIsoTimestamp,
  formatQuantity,
  formatUsdMoney,
  pnlToneClass,
} from './format-display';

describe('format-display helpers', () => {
  describe('formatUsdMoney', () => {
    it('renders null as an em dash', () => {
      expect(formatUsdMoney(null)).toBe('—');
      expect(formatUsdMoney(undefined)).toBe('—');
    });

    it('uses en-US currency formatting with Unicode minus and optional plus', () => {
      expect(formatUsdMoney(1234.5)).toBe('$1,234.50');
      expect(formatUsdMoney(-12.3)).toBe('−$12.30');
      expect(formatUsdMoney(12.3, true)).toBe('+$12.30');
      expect(formatUsdMoney(0, true)).toBe('$0.00');
    });
  });

  describe('formatFixedPercent', () => {
    it('matches Holdings percent sign rules', () => {
      expect(formatFixedPercent(null)).toBe('—');
      expect(formatFixedPercent(10.5)).toBe('10.50%');
      expect(formatFixedPercent(-3.2)).toBe('−3.20%');
      expect(formatFixedPercent(3.2, true)).toBe('+3.20%');
    });
  });

  describe('formatQuantity', () => {
    it('keeps integers plain and pads fractions to three decimals', () => {
      expect(formatQuantity(null)).toBe('—');
      expect(formatQuantity(10)).toBe('10');
      expect(formatQuantity(1.5)).toBe('1.500');
    });
  });

  describe('formatIsoTimestamp', () => {
    it('passes through empty and invalid values', () => {
      expect(formatIsoTimestamp(null)).toBe('—');
      expect(formatIsoTimestamp('')).toBe('—');
      expect(formatIsoTimestamp('not-a-date')).toBe('not-a-date');
    });

    it('formats a valid ISO timestamp with the browser locale', () => {
      const iso = '2026-07-28T16:00:00Z';
      expect(formatIsoTimestamp(iso)).toBe(new Date(iso).toLocaleString());
    });
  });

  describe('pnlToneClass', () => {
    it('marks only non-zero signed values', () => {
      expect(pnlToneClass(null)).toBe('');
      expect(pnlToneClass(0)).toBe('');
      expect(pnlToneClass(1)).toBe('positive');
      expect(pnlToneClass(-1)).toBe('negative');
    });
  });
});
