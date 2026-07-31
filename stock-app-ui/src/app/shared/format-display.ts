/**
 * Narrow display helpers for Brokerage Holdings and Combined Ledger.
 *
 * Contracts match the previous component-local implementations exactly
 * (en-US USD currency, Unicode minus, optional leading '+', em-dash for null).
 * Do not reuse these for Symbol Ledger or other surfaces without checking
 * locale and sign conventions — those differ on purpose today.
 */

/** USD money with en-US currency formatting; null → em dash. */
export function formatUsdMoney(
  value: number | null | undefined,
  signed = false,
): string {
  if (value == null) return '—';
  const formatted = Math.abs(value).toLocaleString('en-US', {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  if (value < 0) return `−${formatted}`;
  return signed && value > 0 ? `+${formatted}` : formatted;
}

/** Fixed two-decimal percent string; null → em dash. */
export function formatFixedPercent(
  value: number | null | undefined,
  signed = false,
): string {
  if (value == null) return '—';
  const formatted = `${Math.abs(value).toFixed(2)}%`;
  if (value < 0) return `−${formatted}`;
  return signed && value > 0 ? `+${formatted}` : formatted;
}

/** Whole numbers stay unpadded; fractions use two decimals. */
export function formatQuantity(value: number | null | undefined): string {
  if (value == null) return '—';
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** ISO / parseable timestamps via the browser locale; invalid → raw string. */
export function formatIsoTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

/** CSS tone class for P&L cells; zero and null stay unmarked. */
export function pnlToneClass(value: number | null | undefined): string {
  if (value == null || value === 0) return '';
  return value > 0 ? 'positive' : 'negative';
}
