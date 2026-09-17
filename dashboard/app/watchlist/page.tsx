"use client";

import { useMemo, useState } from "react";
import { useMarketData } from "@/lib/useMarketData";
import { useQuotes } from "@/lib/useQuotes";
import { Sparkline } from "@/components/TickerBar";
import { ArticleRow } from "@/components/ArticleRow";
import { Article } from "@/lib/supabase";

const WATCHLIST = [
  { symbol: "NVDA", name: "NVIDIA Corp", keywords: ["nvda", "nvidia"] },
  { symbol: "MSFT", name: "Microsoft Corp", keywords: ["msft", "microsoft"] },
  { symbol: "GOOGL", name: "Alphabet Inc", keywords: ["googl", "alphabet", "google"] },
  { symbol: "ASML.AS", name: "ASML Holding", keywords: ["asml"] },
];

function matchesTicker(article: Article, keywords: string[]): boolean {
  const tags = [...(article.ai_affected_tickers ?? []), ...(article.tickers_raw ?? [])].map((t) => t.toLowerCase());
  const text = `${article.title} ${article.ai_summary ?? ""}`.toLowerCase();
  return keywords.some((k) => tags.includes(k) || text.includes(k));
}

export default function WatchlistPage() {
  const { articles, loading } = useMarketData();
  const { quotes } = useQuotes();
  const [open, setOpen] = useState<string | null>("NVDA");

  const grouped = useMemo(() => {
    return WATCHLIST.map((w) => ({
      ...w,
      quote: quotes.find((q) => q.symbol === w.symbol) ?? null,
      matches: articles.filter((a) => matchesTicker(a, w.keywords)).slice(0, 12),
    }));
  }, [articles, quotes]);

  return (
    <div className="container">
      <div className="page-head">
        <h1>Watchlist</h1>
        <div className="sub">Live price + every correlated article, per holding</div>
      </div>

      {grouped.map((w) => {
        const up = (w.quote?.changePercent ?? 0) >= 0;
        const isOpen = open === w.symbol;
        return (
          <div className="watchlist-item" key={w.symbol}>
            <button className="watchlist-head" onClick={() => setOpen(isOpen ? null : w.symbol)}>
              <div className="watchlist-id">
                <span className="mono strong">{w.symbol}</span>
                <span className="dim">{w.name}</span>
              </div>
              {w.quote ? (
                <div className="watchlist-quote">
                  <span className="mono">{w.quote.price?.toFixed(2)}</span>
                  <span className={`mono ${up ? "text-bull" : "text-bear"}`}>
                    {w.quote.changePercent != null ? `${up ? "+" : ""}${w.quote.changePercent.toFixed(2)}%` : "--"}
                  </span>
                  <Sparkline points={w.quote.spark} up={up} width={90} height={26} />
                </div>
              ) : <span className="dim">loading quote&hellip;</span>}
              <span className="watchlist-count">{w.matches.length} articles</span>
              <svg className={`chevron ${isOpen ? "open" : ""}`} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
            </button>
            {isOpen && (
              <div className="watchlist-body">
                {loading && <div className="skeleton skeleton-article" />}
                {!loading && w.matches.length === 0 && <div className="empty">No correlated articles yet for {w.symbol}.</div>}
                {w.matches.map((a) => <ArticleRow key={a.id} article={a} compact />)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
