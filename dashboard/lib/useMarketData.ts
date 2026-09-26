"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { supabase, Article, Digest, CallLog } from "@/lib/supabase";

export function useMarketData() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [calls, setCalls] = useState<CallLog[]>([]);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadData = useCallback(async () => {
    const [articlesRes, digestRes, callsRes] = await Promise.all([
      supabase.from("articles").select("*").order("published_at", { ascending: false }).limit(400),
      supabase.from("digests").select("*").order("created_at", { ascending: false }).limit(1).maybeSingle(),
      supabase.from("ai_calls_log").select("*").order("created_at", { ascending: false }).limit(100),
    ]);
    if (articlesRes.data) setArticles(articlesRes.data as Article[]);
    if (digestRes.data) setDigest(digestRes.data as Digest);
    if (callsRes.data) setCalls(callsRes.data as CallLog[]);
    setLoading(false);
  }, []);

  // The homelab syncs in batches (hundreds of row events at once): coalesce them into one reload.
  const scheduleLoad = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(loadData, 1500);
  }, [loadData]);

  useEffect(() => {
    loadData();
    const channel = supabase
      .channel("market-intel-live")
      .on("postgres_changes", { event: "*", schema: "public", table: "articles" }, scheduleLoad)
      .on("postgres_changes", { event: "*", schema: "public", table: "digests" }, scheduleLoad)
      .on("postgres_changes", { event: "*", schema: "public", table: "ai_calls_log" }, scheduleLoad)
      .subscribe();
    // safety net if the realtime socket drops (phones sleep): refresh every 60 s while visible
    const poll = setInterval(() => { if (document.visibilityState === "visible") loadData(); }, 60000);
    return () => {
      supabase.removeChannel(channel);
      clearInterval(poll);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadData, scheduleLoad]);

  return { articles, digest, calls, loading };
}
