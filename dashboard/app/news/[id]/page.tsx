"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { supabase, Article } from "@/lib/supabase";
import { ArticleRow } from "@/components/ArticleRow";

export default function ArticleDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [article, setArticle] = useState<Article | null>(null);
  const [related, setRelated] = useState<Article[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [isEditing, setIsEditing] = useState(false);
  const [editData, setEditData] = useState<{
    sentiment: string;
    relevance: number;
    action: string;
  }>({ sentiment: "", relevance: 0, action: "" });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const { data } = await supabase.from("articles").select("*").eq("id", id).maybeSingle();
      if (cancelled) return;
      setArticle(data as Article | null);
      if (data) {
        setEditData({
          sentiment: (data as Article).ai_sentiment || "neutral",
          relevance: (data as Article).ai_relevance_score || 0,
          action: (data as Article).ai_suggested_action || "",
        });
      }

      const correlatedIds = (data as Article | null)?.ai_correlated_article_ids ?? [];
      if (correlatedIds.length > 0) {
        const { data: rel } = await supabase.from("articles").select("*").in("id", correlatedIds);
        if (!cancelled && rel) setRelated(rel as Article[]);
      }
      setLoading(false);
    }
    load();
    return () => { cancelled = true; };
  }, [id]);

  if (loading) {
    return (
      <div className="container">
        <div className="skeleton" style={{ height: 32, width: "70%", marginBottom: 16 }} />
        <div className="skeleton" style={{ height: 200, marginBottom: 16 }} />
      </div>
    );
  }

  if (!article) {
    return <div className="container"><div className="empty">Article not found.</div></div>;
  }

  const tickers = article.ai_affected_tickers ?? article.tickers_raw ?? [];
  const body = article.full_text || article.summary_raw;

  const handleSaveEdit = async () => {
    const payload = {
      ai_sentiment: editData.sentiment,
      ai_relevance_score: editData.relevance,
      ai_suggested_action: editData.action,
      ai_processed: true,
      ai_processed_at: new Date().toISOString()
    };
    await supabase.from("articles").update(payload).eq("id", article.id);
    setArticle({ ...article, ...payload } as Article);
    setIsEditing(false);
  };

  return (
    <div className="container article-detail">
      <Link href="/news" className="link-more" style={{ display: "inline-block", marginBottom: 18 }}>&larr; Back to News</Link>

      <div className="article-detail-meta">
        {article.category && <span className="group-pill">{article.category}</span>}
        <span>{article.source}</span>
        <span>&middot;</span>
        <span>{new Date(article.published_at).toLocaleString()}</span>
      </div>
      <h1 className="article-detail-title">{article.title}</h1>

      <div className="tags" style={{ marginBottom: 24, display: 'flex', alignItems: 'center' }}>
        {isEditing ? (
          <>
            <select value={editData.sentiment} onChange={e => setEditData({...editData, sentiment: e.target.value})} style={{ padding: '4px', fontSize: '11px' }}>
              <option value="bullish">bullish</option>
              <option value="bearish">bearish</option>
              <option value="neutral">neutral</option>
              <option value="mixed">mixed</option>
            </select>
            <input 
              type="number" 
              value={editData.relevance} 
              onChange={e => setEditData({...editData, relevance: parseInt(e.target.value) || 0})}
              style={{ width: '60px', padding: '4px', fontSize: '11px' }} 
              placeholder="Rel 0-100"
            />
            <input 
              type="text" 
              value={editData.action} 
              onChange={e => setEditData({...editData, action: e.target.value})}
              style={{ width: '120px', padding: '4px', fontSize: '11px' }} 
              placeholder="Suggested action"
            />
            <button onClick={handleSaveEdit} style={{ fontSize: '11px', cursor: 'pointer', padding: '4px 12px' }}>Save</button>
            <button onClick={() => setIsEditing(false)} style={{ fontSize: '11px', cursor: 'pointer', padding: '4px 12px', background: 'none', border: 'none', textDecoration: 'underline' }}>Cancel</button>
          </>
        ) : (
          <>
            {article.ai_sentiment && <span className={`tag ${article.ai_sentiment}`}>{article.ai_sentiment}</span>}
            {article.ai_relevance_score != null && (
              <span className="relevance-meter">
                <span className="relevance-bar"><span style={{ width: `${article.ai_relevance_score}%` }} /></span>
                relevance {article.ai_relevance_score}
              </span>
            )}
            {article.ai_confidence != null && <span className="tag">confidence {article.ai_confidence}</span>}
            {article.ai_risk_flag && article.ai_risk_flag !== "none" && <span className="tag risk">risk: {article.ai_risk_flag}</span>}
            {article.ai_suggested_action && <span className="tag">{article.ai_suggested_action}</span>}
            <button onClick={() => setIsEditing(true)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', textDecoration: 'underline', color: 'var(--accent)' }}>Edit Analysis</button>
          </>
        )}
        
        <span style={{ margin: '0 8px', color: 'var(--border-strong)' }}>|</span>
        {tickers.map((t: string) => <span className="tag ticker" key={t}>{t}</span>)}
      </div>

      {article.ai_summary && (
        <div className="ai-analysis-box">
          <div className="ai-analysis-label">AI Analysis</div>
          <p>{article.ai_summary}</p>
        </div>
      )}

      {body && (
        <div className="article-body">
          {body.split("\n").filter(Boolean).map((para: string, i: number) => <p key={i}>{para}</p>)}
        </div>
      )}

      <a href={article.url} target="_blank" rel="noreferrer" className="link-more" style={{ display: "inline-block", marginTop: 8 }}>
        Read original at {article.source} &#8599;
      </a>

      {related.length > 0 && (
        <>
          <div className="section-head"><h2>Correlated Articles</h2></div>
          {related.map((a) => <ArticleRow key={a.id} article={a} compact />)}
        </>
      )}
    </div>
  );
}
