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
};
