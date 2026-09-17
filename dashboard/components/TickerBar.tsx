"use client";

import { useQuotes } from "@/lib/useQuotes";

const LABELS: Record<string, string> = {
  "NVDA": "NVDA", "MSFT": "MSFT", "GOOGL": "GOOGL", "ASML.AS": "ASML",
  "CL=F": "WTI", "BZ=F": "BRENT", "^TNX": "10Y", "GC=F": "GOLD", "VWCE.DE": "VWCE",
};

export function Sparkline({ points, up, width = 100, height = 24 }: { points: number[]; up: boolean; width?: number; height?: number }) {
  if (points.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - ((p - min) / range) * height).toFixed(1)}`)
    .join(" ");
  const color = up ? "var(--bull)" : "var(--bear)";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export default function TickerBar() {
  const { quotes, error, loaded } = useQuotes();

  if (error || !loaded) {
    return (
      <div className="index-strip">
        <div className="index-strip-inner">
          <span className="index-strip-loading">{error ? "Live quotes unavailable" : "Loading live quotes…"}</span>
        </div>
      </div>
    );
  }

  const renderItems = () => (
    <>
      {quotes.filter((q) => q.symbol !== "EURUSD=X").map((q) => {
        const up = (q.changePercent ?? 0) >= 0;
        const isYield = q.symbol === "^TNX";
        return (
          <div className="index-item" key={q.symbol}>
            <span className="index-label">{LABELS[q.symbol] ?? q.symbol}</span>
            <span className="index-price">
              {q.price != null ? q.price.toFixed(isYield ? 3 : 2) : "--"}{isYield ? "%" : ""}
            </span>
            <span className={`index-change ${up ? "up" : "down"}`}>
              {q.changePercent != null ? `${up ? "▲" : "▼"} ${Math.abs(q.changePercent).toFixed(2)}%` : ""}
            </span>
          </div>
        );
      })}
    </>
  );

  return (
    <div className="index-strip">
      <div className="index-strip-inner">
        {renderItems()}
        {renderItems()}
      </div>
    </div>
  );
}
