"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useMarketData } from "@/lib/useMarketData";
import { HitRateRing } from "@/components/TrackRecord";
import { ArticleRow } from "@/components/ArticleRow";
import { MissionControl } from "@/components/MissionControl";
import { SectorHeatmap } from "@/components/SectorHeatmap";

export default function Overview() {
  const { articles, digest, calls, loading } = useMarketData();

  const decided = calls.filter((c) => c.outcome === "correct" || c.outcome === "incorrect");
  const correct = calls.filter((c) => c.outcome === "correct").length;
  const hitRate = decided.length > 0 ? (correct / decided.length) * 100 : null;

  const topSignals = useMemo(
    () => articles.filter((a) => (a.ai_relevance_score ?? 0) >= 60),
    [articles]
  );

  const whatsNews = topSignals.slice(0, 7);
  const headlineRail = topSignals.slice(0, 6);

  const sentimentCounts = useMemo(() => ({
    bullish: articles.filter((a) => a.ai_sentiment === "bullish").length,
    bearish: articles.filter((a) => a.ai_sentiment === "bearish").length,
    neutral: articles.filter((a) => a.ai_sentiment === "neutral").length,
  }), [articles]);

  return (
    <div className="container">
      <div className="page-head">
        <h1>Overview</h1>
        <div className="sub">Daily digest, top signals, and system health at a glance</div>
      </div>

      <div className="top-widgets">
        <div 
          className="terminal-widget" 
          style={{ 
            "--terminal-color": (hitRate ?? 0) > 50 ? "#4ade80" : "#f87171",
            "--terminal-glow": (hitRate ?? 0) > 50 ? "rgba(74, 222, 128, 0.15)" : "rgba(248, 113, 113, 0.15)" 
          } as React.CSSProperties}
        >
          <div className="terminal-stats">
            <div className="t-stat">
              <div className="t-val">{hitRate ? hitRate.toFixed(1) : "--"}%</div>
              <div className="t-label">Hit Rate</div>
            </div>
            <div className="t-stat">
              <div className="t-val">{sentimentCounts.bullish}</div>
              <div className="t-label">Bullish Signals</div>
            </div>
            <div className="t-stat">
              <div className="t-val">{sentimentCounts.bearish}</div>
              <div className="t-label">Bearish Signals</div>
            </div>
          </div>
          <div className="terminal-status">
            <div className="terminal-orb" />
            <span>Live Analysis Active</span>
          </div>
        </div>
        
        <MissionControl />
      </div>

      <div className="stat-card" style={{ marginBottom: "2rem" }}>
        <div className="card-head"><h2>Live Sector Heatmap</h2></div>
        <p className="stat-desc">Visualizing real-time momentum across global markets based on AI signal density.</p>
        <SectorHeatmap articles={articles} />
      </div>

      <div className="grid-2col">
        <div>
          {digest && (
            <div className="digest-card">
              <div className="card-head"><h2>Latest Digest</h2></div>
              <div className="meta">
                {new Date(digest.created_at).toLocaleString()} &middot; covering {digest.articles_covered} articles
              </div>
              <p>{digest.summary}</p>
              {digest.key_themes && digest.key_themes.length > 0 && (
                <div className="themes">
                  {digest.key_themes.map((t) => <span className="theme-pill" key={t}>{t}</span>)}
                </div>
              )}
              {digest.guidance && <p><strong>Guidance &mdash; </strong>{digest.guidance}</p>}
              <Link href="/archive" className="link-more">View digest archive &rarr;</Link>
            </div>
          )}

          <div className="section-head">
            <h2>Headlines</h2>
            <Link href="/news" className="link-more">View all news &rarr;</Link>
          </div>
          {loading && Array.from({ length: 4 }).map((_, i) => <div className="skeleton skeleton-article" key={i} />)}
          {!loading && headlineRail.length === 0 && <div className="empty">No high-relevance signals yet.</div>}
          <div className="headline-rail">
            {headlineRail.map((a) => (
              <Link href={`/news/${a.id}`} className="headline-rail-item" key={a.id}>
                <span className={`headline-rail-tag ${a.ai_sentiment ?? ""}`}>{a.ai_sentiment ?? "neutral"}</span>
                <span className="headline-rail-title">{a.title}</span>
                <span className="headline-rail-meta">{a.source}</span>
              </Link>
            ))}
          </div>
        </div>

        <aside className="side-col">
          <div className="stat-card whats-news">
            <div className="card-head"><h2>What&rsquo;s News</h2></div>
            <ul className="whats-news-list">
              {whatsNews.map((a) => (
                <li key={a.id}>
                  <Link href={`/news/${a.id}`}>{a.title}</Link>
                </li>
              ))}
              {whatsNews.length === 0 && <li className="dim">Nothing high-relevance yet.</li>}
            </ul>
          </div>

          <div className="stat-card">
            <div className="card-head"><h2>Track Record</h2></div>
            <div className="stat-card-body">
              <HitRateRing rate={hitRate} size={64} />
              <div>
                <div className="stat-big">{correct}/{decided.length}</div>
                <div className="stat-label">calls verified correct</div>
              </div>
            </div>
            <Link href="/track-record" className="link-more">Full history &rarr;</Link>
          </div>

          <div className="stat-card">
            <div className="card-head"><h2>Sentiment Mix</h2></div>
            <SentimentBar counts={sentimentCounts} />
            <div className="sentiment-legend">
              <span><i className="dot bull" />{sentimentCounts.bullish} bullish</span>
              <span><i className="dot bear" />{sentimentCounts.bearish} bearish</span>
              <span><i className="dot neutral" />{sentimentCounts.neutral} neutral</span>
            </div>
          </div>

          <div className="stat-card">
            <div className="card-head"><h2>Watchlist</h2></div>
            <p className="stat-desc">NVDA, MSFT, GOOGL, ASML plus live macro/energy tickers.</p>
            <Link href="/watchlist" className="link-more">Open watchlist &rarr;</Link>
          </div>
        </aside>
      </div>
    </div>
  );
}

function SentimentBar({ counts }: { counts: { bullish: number; bearish: number; neutral: number } }) {
  const total = counts.bullish + counts.bearish + counts.neutral || 1;
  return (
    <div className="sentiment-bar">
      <span style={{ width: `${(counts.bullish / total) * 100}%`, background: "var(--bull)" }} />
      <span style={{ width: `${(counts.neutral / total) * 100}%`, background: "var(--neutral)" }} />
      <span style={{ width: `${(counts.bearish / total) * 100}%`, background: "var(--bear)" }} />
    </div>
  );
}
