"use client";

import { useMemo } from "react";
import type { Article } from "@/lib/supabase";

export function SectorHeatmap({ articles }: { articles: Article[] }) {
  const heatmapData = useMemo(() => {
    const data: Record<string, { bullish: number; bearish: number; total: number }> = {};

    articles.forEach((a) => {
      if (!a.tickers_raw) return;
      a.tickers_raw.forEach((ticker: string) => {
        if (!data[ticker]) data[ticker] = { bullish: 0, bearish: 0, total: 0 };
        data[ticker].total++;
        if (a.ai_sentiment === "bullish") data[ticker].bullish++;
        if (a.ai_sentiment === "bearish") data[ticker].bearish++;
      });
    });

    const items = Object.entries(data)
      .map(([ticker, counts]) => {
        const net = counts.bullish - counts.bearish;
        let color = "#333";
        let shadow = "none";
        
        if (net > 0) {
          color = `rgba(74, 222, 128, ${Math.min(1, 0.4 + net * 0.15)})`;
          shadow = `0 0 ${10 + net * 5}px rgba(74, 222, 128, 0.4)`;
        } else if (net < 0) {
          color = `rgba(248, 113, 113, ${Math.min(1, 0.4 + Math.abs(net) * 0.15)})`;
          shadow = `0 0 ${10 + Math.abs(net) * 5}px rgba(248, 113, 113, 0.4)`;
        }

        return {
          ticker,
          total: counts.total,
          color,
          shadow,
        };
      })
      .sort((a, b) => b.total - a.total)
      .slice(0, 16); // Top 16 sectors

    return items;
  }, [articles]);

  if (heatmapData.length === 0) return <div className="dim">Gathering sector data...</div>;

  return (
    <div className="heatmap-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(80px, 1fr))", gap: "8px", marginTop: "16px" }}>
      {heatmapData.map((d) => (
        <div
          key={d.ticker}
          style={{
            background: d.color,
            boxShadow: d.shadow,
            borderRadius: "6px",
            padding: "16px 8px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontWeight: "bold",
            fontSize: "12px",
            minHeight: "80px",
            transition: "all 0.3s ease",
            cursor: "crosshair",
            border: "1px solid rgba(255,255,255,0.1)"
          }}
          title={`${d.ticker}: ${d.total} signals`}
        >
          <span>{d.ticker}</span>
          <span style={{ fontSize: "10px", opacity: 0.8, fontWeight: "normal", marginTop: "4px" }}>
            {d.total} sig
          </span>
        </div>
      ))}
    </div>
  );
}
