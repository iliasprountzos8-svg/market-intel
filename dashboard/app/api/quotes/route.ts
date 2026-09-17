import { NextResponse } from "next/server";

// Server-side proxy for Yahoo Finance quotes. Runs on the server (Vercel
// serverless function), not the browser, so there's no CORS issue.
//
// Uses the /v8/finance/chart/{symbol} endpoint (unauthenticated, no crumb
// needed -- same one the yfinance Python library itself calls under the
// hood). Yahoo's older /v7/finance/quote endpoint now requires an auth
// cookie+crumb and returns 401 without it, so this route fetches each
// symbol's chart individually instead of one batched /v7 call.

export const dynamic = "force-dynamic"; // never cache -- always fetch fresh quotes

import config from "../../../config.json";

const SYMBOLS = config.quotes_symbols;

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
};

export type Quote = {
  symbol: string;
  price: number | null;
  change: number | null;
  changePercent: number | null;
  currency: string | null;
  marketState: string | null;
  spark: number[]; // recent closes, oldest -> newest, for a sparkline
};

async function fetchQuote(symbol: string): Promise<Quote | null> {
  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1h`,
      { headers: HEADERS, cache: "no-store" }
    );
    if (!res.ok) return null;
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    const meta = result?.meta;
    if (!meta) return null;

    const price = meta.regularMarketPrice ?? null;
    const prevClose = meta.chartPreviousClose ?? meta.previousClose ?? null;
    const change = price != null && prevClose != null ? price - prevClose : null;
    const changePercent = price != null && prevClose ? (change! / prevClose) * 100 : null;

    const closes: (number | null)[] = result?.indicators?.quote?.[0]?.close ?? [];
    const spark = closes.filter((c): c is number => c != null);

    return {
      symbol,
      price,
      change,
      changePercent,
      currency: meta.currency ?? null,
      marketState: meta.marketState ?? null,
      spark,
    };
  } catch {
    return null;
  }
}

export async function GET() {
  const results = await Promise.all(SYMBOLS.map(fetchQuote));
  const quotes = results.filter((q): q is Quote => q !== null);

  if (quotes.length === 0) {
    return NextResponse.json({ error: "No quotes could be fetched from Yahoo Finance" }, { status: 502 });
  }

  return NextResponse.json({ quotes, fetchedAt: new Date().toISOString() });
}
