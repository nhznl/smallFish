export interface Daily {
  /** ISO calendar date from the API (`YYYY-MM-DD`). */
  tradeDate: string;
  open: number;
  low: number;
  high: number;
  close: number;
  volume: number;
}
