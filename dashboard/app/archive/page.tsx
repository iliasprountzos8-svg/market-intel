"use client";

import { useEffect, useState } from "react";
import { supabase, Digest } from "@/lib/supabase";

export default function ArchivePage() {
  const [digests, setDigests] = useState<Digest[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const { data } = await supabase.from("digests").select("*").order("created_at", { ascending: false }).limit(60);
      if (!cancelled && data) setDigests(data as Digest[]);
      setLoading(false);
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="container">
      <div className="page-head">
        <h1>Archive</h1>
        <div className="sub">Every daily digest ever generated &mdash; the read record over time</div>
      </div>

      {loading && Array.from({ length: 3 }).map((_, i) => <div className="skeleton" style={{ height: 140, marginBottom: 16 }} key={i} />)}
      {!loading && digests.length === 0 && <div className="empty">No digests yet.</div>}

      {digests.map((d) => (
        <div className="archive-entry" key={d.id}>
          <div className="archive-date">
            {new Date(d.created_at).toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
            <span className="archive-time">{new Date(d.created_at).toLocaleTimeString()}</span>
          </div>
          <p className="archive-summary">{d.summary}</p>
          {d.key_themes && d.key_themes.length > 0 && (
            <div className="themes">
              {d.key_themes.map((t) => <span className="theme-pill" key={t}>{t}</span>)}
            </div>
          )}
          {d.guidance && <p className="archive-guidance"><strong>Guidance &mdash; </strong>{d.guidance}</p>}
        </div>
      ))}
    </div>
  );
}
