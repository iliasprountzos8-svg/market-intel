"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useTickerSignals } from "@/lib/useHomelab";
import { IdxBar } from "@/components/MiniLine";

const HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"];

export function SignalsCard() {
  const { rows, loading } = useTickerSignals();
  const held = useMemo(() => HOLDINGS.map((s) => rows.find((r) => r.symbol === s)).filter(Boolean), [rows]);
  const movers = useMemo(
    () => rows.filter((r) => !HOLDINGS.includes(r.symbol) && (r.stories_24h ?? 0) >= 4 && r.ens_z != null).sort((a, b) => Math.abs(b.ens_z!) - Math.abs(a.ens_z!)).slice(0, 5),
    [rows]
  );
  return (
    <div className="stat-card">
      <div className="card-head"><h2>News Signals</h2></div>
      {loading && <div className="skeleton skeleton-article" />}
      {!loading && rows.length === 0 && <p className="stat-desc">Waiting for the homelab&rsquo;s first signal run.</p>}
      {[...held, ...movers].map((r) => r && (
        <div key={r.symbol} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 13 }}>
          <span className="mono strong" style={{ width: 52 }}>{r.symbol}</span>
          <IdxBar v={r.ens} width={70} />
          <span className="mono dim" style={{ marginLeft: "auto" }}>z {r.ens_z == null ? "n/a" : r.ens_z.toFixed(1)}</span>
        </div>
      ))}
      <Link href="/signals" className="link-more">All S&amp;P 500 signals &rarr;</Link>
    </div>
  );
}
