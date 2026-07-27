export interface StockInfo {
  ticker: string;
  period: string;
  retrievedAt: string;
  company: StockCompanyInfo;
  price: StockPriceInfo;
  valuation: StockValuationInfo;
  news: StockNewsItem[];
}

export interface StockCompanyInfo {
  longName?: string | null;
  shortName?: string | null;
  sector?: string | null;
  industry?: string | null;
  website?: string | null;
  summary?: string | null;
  country?: string | null;
  city?: string | null;
  state?: string | null;
  fullTimeEmployees?: number | null;
}

export interface StockPriceInfo {
  regularMarketPrice?: number | null;
  regularMarketPreviousClose?: number | null;
  currency?: string | null;
  marketCap?: number | null;
  fiftyTwoWeekHigh?: number | null;
  fiftyTwoWeekLow?: number | null;
}

export interface StockValuationInfo {
  trailingPe?: number | null;
  forwardPe?: number | null;
  pegRatio?: number | null;
  priceToBook?: number | null;
  dividendYield?: number | null;
  dividendRate?: number | null;
  payoutRatio?: number | null;
  exDividendDate?: string | null;
}

export interface StockNewsItem {
  title?: string | null;
  link?: string | null;
  publisher?: string | null;
  type?: string | null;
  summary?: string | null;
  publishedAt?: string | null;
  thumbnail?: string | null;
  relatedTickers?: string[] | null;
}
