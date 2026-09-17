"use client";

import { useState, useMemo } from "react";
import { useQuotes } from "@/lib/useQuotes";
import { Sparkline } from "@/components/TickerBar";

const LABELS: Record<string, { name: string; group: string }> = {
  "NVDA": { name: "NVIDIA Corp", group: "Watchlist" },
  "MSFT": { name: "Microsoft Corp", group: "Watchlist" },
  "GOOGL": { name: "Alphabet Inc", group: "Watchlist" },
  "ASML.AS": { name: "ASML Holding (Euronext)", group: "Watchlist" },
  "CL=F": { name: "WTI Crude Oil", group: "Commodities" },
  "BZ=F": { name: "Brent Crude Oil", group: "Commodities" },
  "GC=F": { name: "Gold", group: "Commodities" },
  "^TNX": { name: "US 10-Year Treasury Yield", group: "Macro" },
  "VWCE.DE": { name: "Vanguard All-World ETF", group: "Holdings" },
  "EURUSD=X": { name: "Euro / US Dollar", group: "FX" },
};

type SortKey = "symbol" | "price" | "changePercent";

export default function MarketsPage() {
  const { quotes, fetchedAt, error, loaded } = useQuotes();
  const [sortKey, setSortKey] = useState<SortKey>("changePercent");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  const sorted = useMemo(() => {
    const copy = [...quotes];
    copy.sort((a, b) => {
      const av = a[sortKey] ?? 0;
      const bv = b[sortKey] ?? 0;
      if (typeof av === "string" || typeof bv === "string") {
        return String(av).localeCompare(String(bv)) * sortDir;
      }
      return ((av as number) - (bv as number)) * sortDir;
    });
    return copy;
  }, [quotes, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(key); setSortDir(-1); }
  }

  return (
    <div className="container">
      <div className="page-head">
        <h1>Markets</h1>
        <div className="sub">Live quotes from Yahoo Finance &middot; refreshes every 60s{fetchedAt ? ` · last update ${new Date(fetchedAt).toLocaleTimeString()}` : ""}</div>
      </div>

      {error && <div className="empty">Live quotes unavailable: {error}</div>}

      {!loaded && !error && (
        <div className="market-table-wrap">
          {Array.from({ length: 9 }).map((_, i) => <div className="skeleton" style={{ height: 52, marginBottom: 4 }} key={i} />)}
        </div>
      )}

      {loaded && quotes.length > 0 && (
        <div className="market-table-wrap">
          <table className="market-table">
            <thead>
              <tr>
                <Th label="Symbol" active={sortKey === "symbol"} dir={sortDir} onClick={() => toggleSort("symbol")} />
                <th>Name</th>
                <th>Group</th>
                <Th label="Price" active={sortKey === "price"} dir={sortDir} onClick={() => toggleSort("price")} align="right" />
                <Th label="Chg %" active={sortKey === "changePercent"} dir={sortDir} onClick={() => toggleSort("changePercent")} align="right" />
                <th align="right">5-Day</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((q) => {
                const up = (q.changePercent ?? 0) >= 0;
                const meta = LABELS[q.symbol];
                const isYield = q.symbol === "^TNX";
                return (
                  <tr key={q.symbol}>
                    <td className="mono strong">{q.symbol}</td>
                    <td className="dim">{meta?.name ?? "—"}</td>
                    <td><span className="group-pill">{meta?.group ?? "—"}</span></td>
                    <td className="mono" align="right">
                      {q.price != null ? q.price.toFixed(isYield ? 3 : 2) : "--"}{isYield ? "%" : ""}
                    </td>
                    <td className={`mono ${up ? "text-bull" : "text-bear"}`} align="right">
                      {q.changePercent != null ? `${up ? "+" : ""}${q.changePercent.toFixed(2)}%` : "--"}
                    </td>
                    <td align="right"><Sparkline points={q.spark} up={up} width={120} height={30} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({ label, active, dir, onClick, align }: { label: string; active: boolean; dir: 1 | -1; onClick: () => void; align?: "right" }) {
  return (
    <th onClick={onClick} className={`sortable ${active ? "active" : ""}`} style={align ? { textAlign: align } : undefined}>
      {label}{active && <span className="sort-arrow">{dir === 1 ? " ▲" : " ▼"}</span>}
    </th>
  );
}
