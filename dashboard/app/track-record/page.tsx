"use client";

import { useState, useMemo } from "react";
import { useMarketData } from "@/lib/useMarketData";
import { HitRateRing } from "@/components/TrackRecord";
import { TradingChart } from "@/components/TradingChart";

export default function TrackRecordPage() {
  const { calls, loading } = useMarketData();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // The same view logged several times (same symbol, direction and scored window) is ONE call.
  const views = useMemo(() => {
    const seen = new Set<string>();
    return calls.filter((c) => {
      if (!c.outcome || c.entry_price == null || c.exit_price == null) return true;
      const key = `${c.symbol ?? c.ticker_or_theme}|${c.call}|${Number(c.entry_price).toFixed(4)}|${Number(c.exit_price).toFixed(4)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [calls]);
  const viewIds = useMemo(() => new Set(views.map((v) => v.id)), [views]);
  const decided = views.filter((c) => c.outcome === "correct" || c.outcome === "incorrect");
  const correct = views.filter((c) => c.outcome === "correct").length;
  const incorrect = views.filter((c) => c.outcome === "incorrect").length;
  const unclear = views.filter((c) => c.outcome === "unclear").length;
  const pending = views.filter((c) => !c.outcome).length;
  const hitRate = decided.length > 0 ? (correct / decided.length) * 100 : null;
  const ci = useMemo(() => {
    const n = decided.length;
    if (!n) return null;
    const z = 1.96, p = correct / n, d = 1 + (z * z) / n;
    const c = p + (z * z) / (2 * n);
    const m = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
    return [((c - m) / d) * 100, ((c + m) / d) * 100];
  }, [decided.length, correct]);

  const byTheme = useMemo(() => {
    const map = new Map<string, { correct: number; incorrect: number; total: number }>();
    for (const c of decided) {
      const key = c.ticker_or_theme;
      const entry = map.get(key) ?? { correct: 0, incorrect: 0, total: 0 };
      entry.total += 1;
      if (c.outcome === "correct") entry.correct += 1;
      if (c.outcome === "incorrect") entry.incorrect += 1;
      map.set(key, entry);
    }
    return Array.from(map.entries()).sort((a, b) => b[1].total - a[1].total);
  }, [decided]);

  return (
    <div className="container">
      <div className="page-head">
        <h1>Track Record</h1>
        <div className="sub">Every logged directional call, checked against real prices after a fixed window. Re-logged views count once; stocks are judged vs the VT benchmark, yields in bp.{ci && ` Hit rate ${hitRate?.toFixed(0)}% (95% CI ${ci[0].toFixed(0)}-${ci[1].toFixed(0)}%, n=${decided.length}): too few calls to prove skill.`}</div>
      </div>

      <div className="track-summary">
        <div className="track-summary-ring"><HitRateRing rate={hitRate} size={96} /></div>
        <div className="track-summary-stats">
          <StatBox label="Correct" value={correct} tone="bull" />
          <StatBox label="Incorrect" value={incorrect} tone="bear" />
          <StatBox label="Unclear" value={unclear} tone="dim" />
          <StatBox label="Pending" value={pending} tone="accent" />
        </div>
      </div>

      {byTheme.length > 0 && (
        <>
          <div className="section-head"><h2>By ticker / theme</h2></div>
          <div className="market-table-wrap" style={{ marginBottom: 24 }}>
            <table className="market-table">
              <thead><tr><th>Theme</th><th align="right">Correct</th><th align="right">Incorrect</th><th align="right">Hit rate</th></tr></thead>
              <tbody>
                {byTheme.map(([theme, s]) => (
                  <tr key={theme}>
                    <td>{theme}</td>
                    <td className="mono text-bull" align="right">{s.correct}</td>
                    <td className="mono text-bear" align="right">{s.incorrect}</td>
                    <td className="mono" align="right">{((s.correct / s.total) * 100).toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="section-head"><h2>Full call log</h2></div>
      {loading && <div className="skeleton skeleton-article" />}
      {!loading && calls.length === 0 && <div className="empty">No calls logged yet.</div>}
      {calls.map((c) => {
        const isExpanded = expandedId === c.id;
        return (
          <div className="call-row" key={c.id} style={{ cursor: "pointer" }} onClick={() => setExpandedId(isExpanded ? null : c.id)}>
            <div className="call-row-top">
              <span className={`tag ${c.call}`}>{c.call}</span>
              <span className="mono strong">{c.ticker_or_theme}</span>
              {c.outcome && (
                <span className={`theme-pill ${c.outcome === "correct" ? "call-correct" : c.outcome === "incorrect" ? "call-incorrect" : ""}`}>
                  {c.outcome}
                </span>
              )}
              {!c.outcome && <span className="theme-pill">pending</span>}
              {!viewIds.has(c.id) && <span className="theme-pill" title="Same view, symbol and window as an earlier call: not counted twice">repeat, not counted</span>}
              <span className="call-date">{new Date(c.created_at).toLocaleDateString()}</span>
            </div>
            {c.rationale && <div className="ai-summary" style={{ marginTop: 8 }}>{c.rationale}</div>}
            {isExpanded && (
              <div className="call-row-expanded" style={{ marginTop: 16 }} onClick={(e) => e.stopPropagation()}>
                <TradingChart ticker={c.ticker_or_theme} call={c.call} callTime={c.created_at} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatBox({ label, value, tone }: { label: string; value: number; tone: "bull" | "bear" | "dim" | "accent" }) {
  return (
    <div className="stat-box">
      <div className={`stat-box-num tone-${tone}`}>{value}</div>
      <div className="stat-box-label">{label}</div>
    </div>
  );
}
