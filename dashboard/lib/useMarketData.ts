"use client";

import { useEffect, useState, useCallback } from "react";
import { supabase, Article, Digest, CallLog } from "@/lib/supabase";

export function useMarketData() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [calls, setCalls] = useState<CallLog[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    const [articlesRes, digestRes, callsRes] = await Promise.all([
      supabase.from("articles").select("*").order("published_at", { ascending: false }).limit(300),
      supabase.from("digests").select("*").order("created_at", { ascending: false }).limit(1).maybeSingle(),
      supabase.from("ai_calls_log").select("*").order("created_at", { ascending: false }).limit(100),
    ]);
    if (articlesRes.data) setArticles(articlesRes.data as Article[]);
    if (digestRes.data) setDigest(digestRes.data as Digest);
    if (callsRes.data) setCalls(callsRes.data as CallLog[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
    const channel = supabase
      .channel("market-intel-live")
      .on("postgres_changes", { event: "*", schema: "public", table: "articles" }, loadData)
      .on("postgres_changes", { event: "*", schema: "public", table: "digests" }, loadData)
      .on("postgres_changes", { event: "*", schema: "public", table: "ai_calls_log" }, loadData)
      .subscribe();
    return () => { supabase.removeChannel(channel); };
  }, [loadData]);

  return { articles, digest, calls, loading };
}
