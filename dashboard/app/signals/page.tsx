"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useTickerSignals } from "@/lib/useHomelab";
import { TickerSignal } from "@/lib/supabase";
import { MiniLine, IdxBar } from "@/components/MiniLine";

type SortKey = "symbol" | "ens" | "ens_z" | "stories_24h" | "attention_z" | "d1";
const FILTERS = ["all", "holdings", "movers", "positive", "negative", "macro"] as const;
type Filter = (typeof FILTERS)[number];
const HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"];
const MACRO = ["RATES", "OIL", "GOLD", "USD", "MARKET"];

export default function SignalsPage() {
  const { rows, loading } = useTickerSignals();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("ens_z");
  const [desc, setDesc] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  const val = (r: TickerSignal, k: SortKey): number | string => {
    if (k === "symbol") return r.symbol;
    if (k === "d1") return r.perf?.d1 ?? -999;
    // a big z on 1-2 stories is noise: only rank stocks with at least 3 stories by "unusualness"
    if (k === "ens_z") return (r.stories_24h ?? 0) >= 3 ? Math.abs(r.ens_z ?? 0) : -1;
    return (r[k] as number | null) ?? -999;
  };

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    let x = rows.filter((r) => {
      if (q && !r.symbol.toLowerCase().includes(q) && !(r.name ?? "").toLowerCase().includes(q) && !(r.sector ?? "").toLowerCase().includes(q)) return false;
      switch (filter) {
        case "holdings": return HOLDINGS.includes(r.symbol);
        case "macro": return MACRO.includes(r.symbol);
        case "movers": return Math.abs(r.ens_z ?? 0) >= 1.5 && (r.stories_24h ?? 0) >= 4;
        case "positive": return (r.ens ?? 0) > 0.15;
        case "negative": return (r.ens ?? 0) < -0.15;
        default: return true;
      }
    });
    x = [...x].sort((a, b) => {
      const va = val(a, sort), vb = val(b, sort);
      const c = typeof va === "string" ? String(va).localeCompare(String(vb)) : (va as number) - (vb as number);
      return desc ? -c : c;
    });
    return x;
  }, [rows, filter, query, sort, desc]);

  const head = (k: SortKey, label: string, align: "left" | "right" = "right") => (
    <th align={align} style={{ cursor: "pointer" }} onClick={() => { if (sort === k) setDesc(!desc); else { setSort(k); setDesc(true); } }}>
      {label}{sort === k ? (desc ? " ↓" : " ↑") : ""}
    </th>
  );
  const updated = rows.find((r) => r.updated_at)?.updated_at;

  return (
    <div className="container">
      <div className="page-head">
        <h1>News Signals</h1>
        <div className="sub">
          AI-scored news sentiment for the whole S&amp;P 500, measured against each stock&rsquo;s own normal
          {updated ? ` · updated ${new Date(updated).toLocaleTimeString()}` : ""}
        </div>
      </div>

      <div className="hl-note">
        Index runs from &minus;1 to +1 and is relative to each source&rsquo;s usual tone. <b>z</b> compares today with this stock&rsquo;s last 14 days
        (|z| &ge; 2 is unusual). Small samples: a research signal, not advice. In our own tests it has not yet shown a measurable edge over chance.
      </div>

      <div className="news-toolbar">
        <input className="search-box" placeholder="Search ticker, company or sector" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="filters">
          {FILTERS.map((f) => (
            <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f}</button>
          ))}
        </div>
      </div>

      {loading && <div className="skeleton skeleton-article" />}
      {!loading && rows.length === 0 && <div className="empty">No signals yet. The homelab publishes them every 30 minutes once migration 005 is applied.</div>}

      {list.length > 0 && (
        <div className="market-table-wrap">
          <table className="market-table">
            <thead>
              <tr>
                {head("symbol", "Ticker", "left")}
                <th align="left">Company</th>
                {head("ens", "Index")}
                {head("ens_z", "z")}
                {head("stories_24h", "Stories 24h")}
                {head("attention_z", "Attention")}
                {head("d1", "1d")}
                <th align="right">14 days</th>
              </tr>
            </thead>
            <tbody>
              {list.slice(0, 120).map((r) => {
                const isOpen = open === r.symbol;
                return (
                  <>
                    <tr key={r.symbol} onClick={() => setOpen(isOpen ? null : r.symbol)} style={{ cursor: "pointer" }}>
                      <td className="mono strong">{r.symbol}</td>
                      <td className="dim">{r.name}</td>
                      <td align="right"><IdxBar v={r.ens} /> <span className={`mono ${(r.ens ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>{(r.ens ?? 0).toFixed(2)}</span></td>
                      <td align="right" className={`mono ${Math.abs(r.ens_z ?? 0) >= 2 ? "strong" : "dim"}`}>{r.ens_z == null ? "n/a" : r.ens_z.toFixed(1)}</td>
                      <td align="right" className="mono">{r.stories_24h ?? 0}</td>
                      <td align="right" className="mono dim">{r.attention_z == null ? "-" : r.attention_z.toFixed(1)}</td>
                      <td align="right" className={`mono ${(r.perf?.d1 ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>{r.perf ? `${r.perf.d1 >= 0 ? "+" : ""}${r.perf.d1.toFixed(1)}%` : "-"}</td>
                      <td align="right"><MiniLine values={(r.series ?? []).map((s) => s[1])} width={90} height={22} color="var(--accent)" /></td>
                    </tr>
                    {isOpen && (
                      <tr key={r.symbol + "-d"}>
                        <td colSpan={8} style={{ background: "var(--panel-hover)" }}>
                          <div style={{ padding: "10px 4px" }}>
                            <div className="dim" style={{ marginBottom: 6 }}>{r.sector} &middot; sentiment index, last 14 days (ensemble)</div>
                            <MiniLine values={(r.series ?? []).map((s) => s[1])} width={520} height={70} />
                            <div className="mono" style={{ marginTop: 8, fontSize: 12 }}>
                              ensemble {fmt(r.ens)} &nbsp;&middot;&nbsp; FinBERT {fmt(r.finbert)} &nbsp;&middot;&nbsp; lexicon {fmt(r.lex)} &nbsp;&middot;&nbsp; old labels {fmt(r.old)}
                            </div>
                            <div style={{ marginTop: 8 }}>
                              <Link className="link-more" href={`/news?q=${encodeURIComponent(r.name?.split(" ")[0] ?? r.symbol)}`}>Read the news on {r.symbol} &rarr;</Link>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {list.length > 120 && <div className="dim" style={{ marginTop: 8 }}>Showing 120 of {list.length}. Use the search or a filter to narrow down.</div>}
    </div>
  );
}

const fmt = (v: number | null | undefined) => (v == null ? "n/a" : (v >= 0 ? "+" : "") + v.toFixed(2));
