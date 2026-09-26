import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(url, anonKey);

export type Article = {
  id: string;
  source: string;
  url: string;
  title: string;
  summary_raw: string | null;
  full_text: string | null;
  published_at: string;
  category: string | null;
  tickers_raw: string[] | null;
  ai_processed: boolean;
  ai_sentiment: string | null;
  ai_relevance_score: number | null;
  ai_affected_tickers: string[] | null;
  ai_summary: string | null;
  ai_suggested_action: string | null;
  ai_risk_flag: string | null;
  ai_confidence: number | null;
  ai_correlated_article_ids: string[] | null;
  // filled by the homelab (migration 005)
  fb_score?: number | null;
  event?: string | null;
  lang?: string | null;
};

export type Digest = {
  id: string;
  created_at: string;
  period_start: string;
  period_end: string;
  articles_covered: number;
  summary: string;
  key_themes: string[] | null;
  guidance: string | null;
  watchlist_notes: Record<string, string> | null;
};

export type CallLog = {
  id: string;
  created_at: string;
  ticker_or_theme: string;
  call: string;
  rationale: string | null;
  outcome: string | null;
  outcome_checked_at: string | null;
  confidence?: number | null;
  horizon_days?: number | null;
  symbol?: string | null;
  invalidation?: string | null;
  asset_return_pct?: number | null;
  excess_return_pct?: number | null;
};

// ---- homelab edition (migration 005) ----
export type PipelineStatus = { id: number; updated_at: string; data: any };
export type Command = {
  id: string;
  kind: "sync" | "digest";
  status: "pending" | "running" | "done" | "failed" | "rejected" | "expired";
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: string | null;
};
export type TickerSignal = {
  symbol: string;
  name: string | null;
  sector: string | null;
  updated_at: string | null;
  ens: number | null;
  ens_z: number | null;
  finbert: number | null;
  lex: number | null;
  old: number | null;
  stories_24h: number | null;
  attention_z: number | null;
  series: [string, number][] | null;
  perf: { last: number; d1: number; d5: number } | null;
};
export type LabReport = { as_of: string; updated_at: string; report: any };
