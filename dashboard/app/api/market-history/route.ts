import { NextResponse } from "next/server";

// Server-side proxy for Yahoo Finance historical candles, replacing the
// separate FastAPI's /api/v1/market/history endpoint -- keeps the whole
// dashboard deployable as a single Vercel project with no always-on server
// to host. Same Yahoo chart endpoint the quotes route already uses.

export const dynamic = "force-dynamic";

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
};

// Theme/name -> Yahoo symbol, same mapping the Python check_outcomes.py /
// api/main.py used.
const TICKER_MAP: Record<string, string> = {
  "10y treasury": "^TNX", "10-year": "^TNX", "treasury yield": "^TNX",
  "brent": "BZ=F", "wti": "CL=F", "oil": "CL=F",
  "nvda": "NVDA", "nvidia": "NVDA",
  "msft": "MSFT", "microsoft": "MSFT",
  "googl": "GOOGL", "google": "GOOGL", "alphabet": "GOOGL",
  "gold": "GC=F", "dollar": "DX-Y.NYB", "usd": "DX-Y.NYB",
};

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ticker = searchParams.get("ticker");
  const days = parseInt(searchParams.get("days") || "30", 10);

  if (!ticker) {
    return NextResponse.json({ error: "ticker is required" }, { status: 400 });
  }

  const symbol = TICKER_MAP[ticker.toLowerCase()] ?? ticker.toUpperCase();

  try {
    const res = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=${days}d&interval=1d`,
      { headers: HEADERS, cache: "no-store" }
    );
    if (!res.ok) {
      return NextResponse.json({ error: `No data found for ${symbol}` }, { status: 404 });
    }
    const json = await res.json();
    const result = json?.chart?.result?.[0];
    const timestamps: number[] = result?.timestamp ?? [];
    const quote = result?.indicators?.quote?.[0];

    if (!timestamps.length || !quote) {
      return NextResponse.json({ error: `No data found for ${symbol}` }, { status: 404 });
    }

    const data = timestamps
      .map((time: number, i: number) => ({
        time,
        open: quote.open?.[i],
        high: quote.high?.[i],
        low: quote.low?.[i],
        close: quote.close?.[i],
      }))
      .filter((d: any) => d.open != null && d.high != null && d.low != null && d.close != null)
      .map((d: any) => ({
        time: d.time,
        open: Math.round(d.open * 100) / 100,
        high: Math.round(d.high * 100) / 100,
        low: Math.round(d.low * 100) / 100,
        close: Math.round(d.close * 100) / 100,
      }));

    return NextResponse.json({ symbol, data });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to fetch market history" }, { status: 500 });
  }
}
