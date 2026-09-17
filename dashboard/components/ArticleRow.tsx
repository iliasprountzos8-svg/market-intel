import Link from "next/link";
import { Article } from "@/lib/supabase";

export function ArticleRow({ 
  article, 
  compact = false,
  selectable = false,
  selected = false,
  onToggleSelect
}: { 
  article: Article; 
  compact?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string, selected: boolean) => void;
}) {
  const sentClass = article.ai_sentiment === "bullish" ? "sent-bullish" : article.ai_sentiment === "bearish" ? "sent-bearish" : "";
  return (
    <div className={`article ${sentClass} ${compact ? "compact" : ""}`} style={{ display: 'flex', gap: '16px' }}>
      {selectable && (
        <div style={{ paddingTop: '4px' }}>
          <input 
            type="checkbox" 
            checked={selected}
            onChange={(e) => onToggleSelect?.(article.id, e.target.checked)}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
        </div>
      )}
      <div style={{ flex: 1 }}>
        <div className="row1">
        <Link className="title-link" href={`/news/${article.id}`}>{article.title}</Link>
        {!article.ai_processed && <span className="badge-unprocessed">unprocessed</span>}
      </div>
      <div className="meta">
        <span>{article.source}</span>
        <span>&middot;</span>
        <span>{new Date(article.published_at).toLocaleString()}</span>
        {article.category && <><span>&middot;</span><span>{article.category}</span></>}
      </div>
      {!compact && article.ai_summary && <div className="ai-summary">{article.ai_summary}</div>}
      <div className="tags">
        {article.ai_sentiment && <span className={`tag ${article.ai_sentiment}`}>{article.ai_sentiment}</span>}
        {article.ai_relevance_score != null && (
          <span className="relevance-meter">
            <span className="relevance-bar"><span style={{ width: `${article.ai_relevance_score}%` }} /></span>
            {article.ai_relevance_score}
          </span>
        )}
        {article.ai_risk_flag && article.ai_risk_flag !== "none" && (
          <span className="tag risk">risk: {article.ai_risk_flag}</span>
        )}
        {article.ai_suggested_action && <span className="tag">{article.ai_suggested_action}</span>}
        {(article.ai_affected_tickers ?? article.tickers_raw ?? []).slice(0, 6).map((t) => (
          <span className="tag ticker" key={t}>{t}</span>
        ))}
      </div>
      </div>
    </div>
  );
}
