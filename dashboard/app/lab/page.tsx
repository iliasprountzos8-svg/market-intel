"use client";

import { useLabReport } from "@/lib/useHomelab";

const pct = (v: number | null | undefined, d = 2) => (v == null ? "-" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const BOOK_LABEL: Record<string, string> = {
  mom: "Momentum (12-1 month)",
  rev: "Short-term reversal",
  lowvol: "Low volatility",
  hgb: "Machine-learning model (price features)",
  news_ens: "News sentiment: ensemble",
  news_finbert: "News sentiment: FinBERT",
  news_lex: "News sentiment: lexicon",
  news_old: "News sentiment: original labels",
};

export default function LabPage() {
  const { lab, loading } = useLabReport();
  const r = lab?.report;
  const books: string[] = r ? Array.from(new Set((r.top ?? []).map((x: any) => x.book))) : [];

  return (
    <div className="container">
      <div className="page-head">
        <h1>Prediction Lab</h1>
        <div className="sub">
          Strategies ranked across the S&amp;P 500 every night, tracked as paper trades against the index
          {lab ? ` · as of ${lab.as_of}` : ""}
        </div>
      </div>

      <div className="hl-note">
        <b>Research and paper trading only. Not investment advice.</b> A strategy earns trust only after many settled paper positions with positive
        <i> net</i> excess return over the S&amp;P 500. The historical tests use today&rsquo;s S&amp;P 500 members (survivorship bias), which flatters some strategies.
      </div>

      {loading && <div className="skeleton skeleton-article" />}
      {!loading && !r && <div className="empty">No lab report yet. The homelab publishes it after each nightly run once migration 005 is applied.</div>}

      {r && (
        <>
          <div className="track-summary-stats" style={{ marginBottom: 24 }}>
            <div className="stat-box"><div className="stat-box-num">{books.length}</div><div className="stat-box-label">Strategies</div></div>
            <div className="stat-box"><div className="stat-box-num">{r.open_positions ?? "-"}</div><div className="stat-box-label">Open paper positions</div></div>
            <div className="stat-box"><div className="stat-box-num">{r.track?.n ?? "-"}</div><div className="stat-box-label">Decided calls</div></div>
          </div>

          <div className="section-head"><h2>Today&rsquo;s ideas by strategy</h2></div>
          <div className="grid-2col">
            {books.map((b) => {
              const top = (r.top ?? []).filter((x: any) => x.book === b);
              const avoid = (r.avoid ?? []).filter((x: any) => x.book === b);
              const led = (r.ledger ?? []).filter((x: any) => x.book === b);
              return (
                <div className="stat-card" key={b}>
                  <div className="card-head"><h2>{BOOK_LABEL[b] ?? b}</h2></div>
                  <div className="stat-desc">Top picks</div>
                  <div className="themes">{top.map((t: any) => <span className="theme-pill" key={t.symbol}>{t.symbol}</span>)}</div>
                  <div className="stat-desc" style={{ marginTop: 8 }}>Avoid</div>
                  <div className="themes">{avoid.map((t: any) => <span className="theme-pill" key={t.symbol}>{t.symbol}</span>)}</div>
                  {led.map((l: any) => (
                    <div key={l.side} className="dim" style={{ marginTop: 6, fontSize: 13 }}>
                      {l.side}: {l.n} settled, {l.open} open
                      {l.n > 0 ? <> &middot; net vs S&amp;P <span className={Number(l.net) >= 0 ? "text-bull" : "text-bear"}>{pct(l.net)}</span> &middot; hit {(Number(l.hit) * 100).toFixed(0)}%</> : " · first results ~5 trading days after entry"}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>

          {[5, 20].map((h) => {
            const w = r.walkforward?.[h];
            if (!w) return null;
            return (
              <div key={h} style={{ marginTop: 28 }}>
                <div className="section-head"><h2>Walk-forward test &middot; {h}-day horizon</h2></div>
                <div className="market-table-wrap">
                  <table className="market-table">
                    <thead><tr><th>Strategy</th><th align="right">Rank correlation (IC)</th><th align="right">t-stat</th><th align="right">Top-minus-bottom decile, net</th><th align="right">Verdict</th></tr></thead>
                    <tbody>
                      {(w.results ?? []).filter((x: any) => x.ic_mean !== undefined).map((x: any) => (
                        <tr key={x.name}>
                          <td>{BOOK_LABEL[x.name] ?? x.name}</td>
                          <td className="mono" align="right">{Number(x.ic_mean).toFixed(3)}</td>
                          <td className="mono" align="right">{Number(x.ic_t).toFixed(1)}</td>
                          <td className={`mono ${Number(x.spread_net_pct) >= 0 ? "text-bull" : "text-bear"}`} align="right">{pct(x.spread_net_pct)}</td>
                          <td align="right" className="dim">{x.skill ? "marginal edge (unproven)" : "no edge"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}

          {r.signal_eval?.length > 0 && (
            <div className="stat-card" style={{ marginTop: 28 }}>
              <div className="card-head"><h2>Does the news signal predict prices?</h2></div>
              <p className="stat-desc">{r.signal_eval[r.signal_eval.length - 1]}</p>
            </div>
          )}

          {r.track && (
            <div className="stat-card" style={{ marginTop: 16 }}>
              <div className="card-head"><h2>Track record of AI calls</h2></div>
              <p className="stat-desc">{r.track.verdict ?? "No report yet."}</p>
            </div>
          )}

          {r.note && <p className="dim" style={{ marginTop: 20, fontSize: 12 }}>{r.note}</p>}
        </>
      )}
    </div>
  );
}
