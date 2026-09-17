"use client";

import { useMemo, useState } from "react";
import { useMarketData } from "@/lib/useMarketData";
import { ArticleRow } from "@/components/ArticleRow";
import { supabase } from "@/lib/supabase";

const FILTERS = ["all", "bullish", "bearish", "high relevance", "unprocessed"] as const;
type Filter = (typeof FILTERS)[number];

export default function NewsPage() {
  const { articles, loading } = useMarketData();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const sources = useMemo(
    () => ["all", ...Array.from(new Set(articles.map((a) => a.source))).sort()],
    [articles]
  );

  const counts = useMemo(() => ({
    all: articles.length,
    bullish: articles.filter((a) => a.ai_sentiment === "bullish").length,
    bearish: articles.filter((a) => a.ai_sentiment === "bearish").length,
    "high relevance": articles.filter((a) => (a.ai_relevance_score ?? 0) >= 70).length,
    unprocessed: articles.filter((a) => !a.ai_processed).length,
  }), [articles]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return articles.filter((a) => {
      if (source !== "all" && a.source !== source) return false;
      if (q && !a.title.toLowerCase().includes(q) && !(a.ai_summary ?? "").toLowerCase().includes(q)) return false;
      switch (filter) {
        case "bullish": return a.ai_sentiment === "bullish";
        case "bearish": return a.ai_sentiment === "bearish";
        case "high relevance": return (a.ai_relevance_score ?? 0) >= 70;
        case "unprocessed": return !a.ai_processed;
        default: return true;
      }
    });
  }, [articles, filter, query, source]);

  const handleToggleSelect = (id: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const handleBulkAction = async (actionType: "bullish" | "bearish" | "dismiss") => {
    if (selectedIds.size === 0) return;
    
    const updates = Array.from(selectedIds).map(id => {
      const payload: any = { ai_processed: true, ai_processed_at: new Date().toISOString() };
      if (actionType === "bullish") {
        payload.ai_sentiment = "bullish";
        payload.ai_relevance_score = 70;
      } else if (actionType === "bearish") {
        payload.ai_sentiment = "bearish";
        payload.ai_relevance_score = 70;
      } else if (actionType === "dismiss") {
        payload.ai_relevance_score = 0;
        payload.ai_suggested_action = "dismiss";
      }
      return supabase.from("articles").update(payload).eq("id", id);
    });

    await Promise.all(updates);
    setSelectedIds(new Set());
  };

  return (
    <div className="container">
      <div className="page-head">
        <h1>News</h1>
        <div className="sub">{articles.length} articles tracked &middot; {filtered.length} matching current filters</div>
      </div>

      <div className="news-toolbar">
        <div className="search-box">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.35-4.35" />
          </svg>
          <input
            type="text"
            placeholder="Search titles and summaries&hellip;"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <select className="source-select" value={source} onChange={(e) => setSource(e.target.value)}>
          {sources.map((s) => <option key={s} value={s}>{s === "all" ? "All sources" : s}</option>)}
        </select>
      </div>

      <div className="filters">
        {FILTERS.map((f) => (
          <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
            {f}<span className="count">{counts[f]}</span>
          </button>
        ))}
      </div>

      {loading && Array.from({ length: 6 }).map((_, i) => <div className="skeleton skeleton-article" key={i} />)}
      {!loading && filtered.length === 0 && <div className="empty">No articles match this filter.</div>}
      {filtered.map((a) => (
        <ArticleRow 
          key={a.id} 
          article={a} 
          selectable 
          selected={selectedIds.has(a.id)}
          onToggleSelect={handleToggleSelect}
        />
      ))}

      {selectedIds.size > 0 && (
        <div style={{
          position: "fixed",
          bottom: "30px",
          left: "50%",
          transform: "translateX(-50%)",
          background: "var(--ink)",
          color: "#fff",
          padding: "12px 24px",
          borderRadius: "999px",
          display: "flex",
          alignItems: "center",
          gap: "16px",
          boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.3)",
          zIndex: 100
        }}>
          <span style={{ fontSize: "13px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
            {selectedIds.size} selected
          </span>
          <div style={{ width: "1px", height: "20px", background: "rgba(255,255,255,0.2)" }} />
          <button onClick={() => handleBulkAction("bullish")} style={{ background: "none", border: "none", color: "var(--bull)", cursor: "pointer", fontWeight: 700, fontSize: "12px", textTransform: "uppercase" }}>Mark Bullish</button>
          <button onClick={() => handleBulkAction("bearish")} style={{ background: "none", border: "none", color: "var(--bear)", cursor: "pointer", fontWeight: 700, fontSize: "12px", textTransform: "uppercase" }}>Mark Bearish</button>
          <button onClick={() => handleBulkAction("dismiss")} style={{ background: "none", border: "none", color: "#ccc", cursor: "pointer", fontWeight: 700, fontSize: "12px", textTransform: "uppercase" }}>Dismiss</button>
        </div>
      )}
    </div>
  );
}
