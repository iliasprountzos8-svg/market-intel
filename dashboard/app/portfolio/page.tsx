"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useMarketData } from "@/lib/useMarketData";
import { useQuotes } from "@/lib/useQuotes";
import { Sparkline } from "@/components/TickerBar";
import { ArticleRow } from "@/components/ArticleRow";
import { Article } from "@/lib/supabase";
import { usePortfolio } from "@/lib/holdings";

function matchesHolding(article: Article, keywords: string[]): boolean {
  const tags = [...(article.ai_affected_tickers ?? []), ...(article.tickers_raw ?? [])].map((t) => t.toLowerCase());
  const text = `${article.title} ${article.ai_summary ?? ""}`.toLowerCase();
  return keywords.some((k) => tags.includes(k) || text.includes(k));
}

export default function PortfolioPage() {
  const { articles, loading } = useMarketData();
  const { quotes, loaded: quotesLoaded, fetchedAt } = useQuotes();
  const { holdings: HOLDINGS, meta, loading: portfolioLoading } = usePortfolio();

  const eurUsd = quotes.find((q) => q.symbol === "EURUSD=X")?.price ?? null;

  const rows = useMemo(() => {
    return HOLDINGS.map((h) => {
      const quote = quotes.find((q) => q.symbol === h.symbol) ?? null;
      const matches = articles.filter((a) => matchesHolding(a, h.keywords));
      const highRelevanceCount = matches.filter((a) => (a.ai_relevance_score ?? 0) >= 55).length;
      const bearishCount = matches.filter((a) => a.ai_suggested_action === "sell" || a.ai_sentiment === "negative").length;
      const bullishCount = matches.filter((a) => a.ai_suggested_action === "buy" || a.ai_sentiment === "positive").length;

      let valueEur: number | null = null;
      if (quote?.price != null) {
        if (quote.currency === "USD" && eurUsd) {
          valueEur = (h.shares * quote.price) / eurUsd;
        } else {
          valueEur = h.shares * quote.price;
        }
      }

      return { ...h, quote, matches, highRelevanceCount, bearishCount, bullishCount, valueEur };
    });
  }, [articles, quotes, eurUsd]);

  const totalValueEur = rows.reduce((sum, r) => sum + (r.valueEur ?? 0), 0);
  const allPriced = rows.every((r) => r.valueEur != null);
  const costBasisEur = meta?.costBasisEur ?? null;
  const unrealized = allPriced && costBasisEur != null ? totalValueEur - costBasisEur : null;
  const unrealizedPct = unrealized != null && costBasisEur ? (unrealized / costBasisEur) * 100 : null;
  const unrealizedUp = (unrealized ?? 0) >= 0;
  const totalMatches = rows.reduce((sum, r) => sum + r.matches.length, 0);

  return (
    <div className="container">
      <div className="page-head">
        <h1>Portfolio</h1>
        <div className="sub">
          Your actual Trading212 holdings &middot; value computed live from real-time prices
          {fetchedAt ? ` · priced ${new Date(fetchedAt).toLocaleTimeString()}` : ""}
          {meta?.asOf ? ` · shares as of ${meta.asOf}` : ""}
        </div>
      </div>

      <div className="track-summary">
        <div>
          <div className="stat-box-num" style={{ fontSize: 30 }}>
            {allPriced ? `€${totalValueEur.toFixed(2)}` : <span className="skeleton" style={{ display: "inline-block", width: 100, height: 30 }} />}
          </div>
          <div className="stat-box-label">Live value</div>
        </div>
        <div className="track-summary-stats">
          <StatBox label="Cost Basis" value={costBasisEur != null ? `€${costBasisEur.toFixed(2)}` : "--"} tone="dim" />
          <StatBox
            label="Unrealized"
            value={unrealized != null ? `${unrealizedUp ? "+" : ""}€${unrealized.toFixed(2)}` : "—"}
            tone={unrealizedUp ? "bull" : "bear"}
          />
          <StatBox
            label="Return"
            value={unrealizedPct != null ? `${unrealizedUp ? "+" : ""}${unrealizedPct.toFixed(2)}%` : "—"}
            tone={unrealizedUp ? "bull" : "bear"}
          />
          <StatBox label="News matched" value={String(totalMatches)} tone="accent" />
        </div>
      </div>

      <div className="market-table-wrap" style={{ marginBottom: 12 }}>
        <table className="market-table">
          <thead>
            <tr>
              <th>Holding</th>
              <th align="right">Shares</th>
              <th align="right">Weight</th>
              <th align="right">Live Value</th>
              <th align="right">Today</th>
              <th align="right">5-Day</th>
              <th align="right">AI Risk</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const up = (r.quote?.changePercent ?? 0) >= 0;
              const weightPct = allPriced && r.valueEur != null ? (r.valueEur / totalValueEur) * 100 : null;
              return (
                <tr key={r.symbol}>
                  <td>
                    <div className="mono strong">{r.symbol.replace(".AS", "").replace(".DE", "")}</div>
                    <div className="dim" style={{ fontSize: 11.5 }}>{r.name}</div>
                  </td>
                  <td className="mono dim" align="right" style={{ fontSize: 12 }}>{r.shares}</td>
                  <td className="mono" align="right">{weightPct != null ? `${weightPct.toFixed(2)}%` : "--"}</td>
                  <td className="mono" align="right">{r.valueEur != null ? `€${r.valueEur.toFixed(2)}` : "--"}</td>
                  <td className={`mono ${up ? "text-bull" : "text-bear"}`} align="right">
                    {r.quote?.changePercent != null ? `${up ? "+" : ""}${r.quote.changePercent.toFixed(2)}%` : "--"}
                  </td>
                  <td align="right">{r.quote && <Sparkline points={r.quote.spark} up={up} width={100} height={26} />}</td>
                  <td align="right">
                    {r.bearishCount > 0 ? (
                      <span className="badge badge-bear blink">RISK</span>
                    ) : r.bullishCount > 0 ? (
                      <span className="badge badge-bull">BULLISH</span>
                    ) : (
                      <span className="dim">NEUTRAL</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {eurUsd && (
        <div className="dim" style={{ fontSize: 11, fontFamily: "var(--font-mono)", marginBottom: 28 }}>
          EUR/USD {eurUsd.toFixed(4)} &middot; NVDA/MSFT/GOOGL converted from USD live &middot; VWCE/ASML priced EUR-native (Euronext)
        </div>
      )}

      {!quotesLoaded && <div className="dim" style={{ marginBottom: 28 }}>Loading live prices&hellip;</div>}

      <div className="portfolio-weight-bar">
        {rows.map((r) => {
          const weightPct = allPriced && r.valueEur != null ? (r.valueEur / totalValueEur) * 100 : 0;
          return (
            <span
              key={r.symbol}
              style={{ width: `${weightPct}%` }}
              title={`${r.symbol} ${weightPct.toFixed(2)}%`}
              className={`weight-seg weight-${r.symbol.replace(/[^A-Z]/g, "")}`}
            />
          );
        })}
      </div>
      <div className="portfolio-weight-legend">
        {rows.map((r) => {
          const weightPct = allPriced && r.valueEur != null ? (r.valueEur / totalValueEur) * 100 : 0;
          return (
            <span key={r.symbol}>
              <i className={`dot weight-${r.symbol.replace(/[^A-Z]/g, "")}`} />
              {r.symbol.replace(".AS", "").replace(".DE", "")} {weightPct.toFixed(1)}%
            </span>
          );
        })}
      </div>

      {rows.map((r) => (
        r.matches.length > 0 && (
          <div key={r.symbol} className="portfolio-holding-news">
            <div className="section-head">
              <h2>{r.symbol.replace(".AS", "").replace(".DE", "")} &mdash; {r.matches.length} article{r.matches.length === 1 ? "" : "s"}</h2>
              <Link href={`/watchlist`} className="link-more">Full watchlist &rarr;</Link>
            </div>
            {r.matches.slice(0, 3).map((a) => <ArticleRow key={a.id} article={a} compact />)}
          </div>
        )
      ))}

      {(loading || portfolioLoading) && <div className="skeleton skeleton-article" />}
    </div>
  );
}

function StatBox({ label, value, tone }: { label: string; value: string; tone: "bull" | "bear" | "dim" | "accent" }) {
  return (
    <div className="stat-box">
      <div className={`stat-box-num tone-${tone}`}>{value}</div>
      <div className="stat-box-label">{label}</div>
    </div>
  );
}
