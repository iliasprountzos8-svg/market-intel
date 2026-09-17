"use client";

import { useEffect, useState } from "react";
import type { Quote } from "@/app/api/quotes/route";

const REFRESH_MS = 60_000;

export function useQuotes() {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch("/api/quotes");
        const data = await res.json();
        if (cancelled) return;
        if (data.error) {
          setError(data.error);
        } else {
          setQuotes(data.quotes ?? []);
          setFetchedAt(data.fetchedAt ?? null);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return { quotes, fetchedAt, error, loaded };
}
