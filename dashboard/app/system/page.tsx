"use client";

import { useHomelab } from "@/lib/useHomelab";
import { MiniBars } from "@/components/MiniLine";

const KIND_LABEL: Record<string, string> = {
  yahoo_ticker: "Yahoo Finance (per company)",
  nasdaq_ticker: "Nasdaq.com (per company)",
  sa_ticker: "Seeking Alpha (per company)",
  rss: "Outlets, central banks, regulators",
  sec: "SEC EDGAR filings",
};

export default function SystemPage() {
  const { data, commands, loading, online, ageSec } = useHomelab();
  const c = data?.counts;
  const fb = data?.finbert;

  return (
    <div className="container">
      <div className="page-head">
        <h1>System</h1>
        <div className="sub">The homelab that pulls, stores and scores everything you read here</div>
      </div>

      {loading && <div className="skeleton skeleton-article" />}
      {!loading && !data && <div className="empty">No heartbeat yet. Apply migration 005 in Supabase; the homelab reports here every 15 seconds.</div>}

      {data && (
        <>
          <div className="track-summary-stats" style={{ marginBottom: 24 }}>
            <div className="stat-box"><div className={`stat-box-num ${online ? "tone-bull" : "tone-bear"}`}>{online ? "ONLINE" : "OFFLINE"}</div><div className="stat-box-label">Homelab · heartbeat {ageSec != null ? Math.round(ageSec) : "?"}s ago</div></div>
            <div className="stat-box"><div className="stat-box-num">{c?.total?.toLocaleString() ?? "-"}</div><div className="stat-box-label">Articles stored</div></div>
            <div className="stat-box"><div className="stat-box-num">{c?.h24?.toLocaleString() ?? "-"}</div><div className="stat-box-label">Arrived in 24h</div></div>
            <div className="stat-box"><div className="stat-box-num">{c?.worthy24 ?? "-"}</div><div className="stat-box-label">Key stories (relevance 40+)</div></div>
          </div>

          <div className="grid-2col">
            <div>
              <div className="section-head"><h2>Pipeline</h2></div>
              <div className="stat-card">
                <Row label="Full cycle (every 30 min)" v={data.cycle?.running ? "running..." : data.cycle?.text} />
                <Row label="Fast lane (every 5 min)" v={data.fast?.running ? "running..." : data.fast?.text} />
                <Row label="Last manual sync" v={data.sync?.text} />
                <Row label="Digest job" v={data.digest?.running ? "running..." : data.digest?.text} />
                <Row label="Database" v={data.db_size} />
                <Row label="AI scoring coverage (24h, English)" v={fb ? `${fb.scored} of ${fb.n} articles scored by FinBERT` : "-"} />
                <Row label="Prediction lab" v={data.lab_as_of ? `as of ${data.lab_as_of}` : "-"} />
              </div>

              <div className="section-head" style={{ marginTop: 24 }}><h2>Articles per day &middot; 14 days</h2></div>
              <div className="stat-card"><MiniBars values={(data.per_day ?? []).map((d: any) => d.n)} /></div>

              <div className="section-head" style={{ marginTop: 24 }}><h2>New articles per ingest run &middot; last 12h</h2></div>
              <div className="stat-card"><MiniBars values={(data.ingest ?? []).map((d: any) => d.new)} color="var(--bull)" /></div>
            </div>

            <div>
              <div className="section-head"><h2>Sources</h2></div>
              <div className="market-table-wrap">
                <table className="market-table">
                  <thead><tr><th>Type</th><th align="right">Feeds</th><th align="right">Enabled</th><th align="right">Failing</th></tr></thead>
                  <tbody>
                    {(data.sources ?? []).map((k: any) => (
                      <tr key={k.kind}>
                        <td>{KIND_LABEL[k.kind] ?? k.kind}</td>
                        <td className="mono" align="right">{k.n}</td>
                        <td className="mono" align="right">{k.en}</td>
                        <td className={`mono ${k.failing ? "text-bear" : "dim"}`} align="right">{k.failing}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="section-head" style={{ marginTop: 24 }}><h2>Sentiment mix &middot; last 24h</h2></div>
              <div className="stat-card">
                {Object.entries(data.mix24 ?? {}).map(([k, n]) => (
                  <Row key={k} label={k} v={String(n)} />
                ))}
              </div>

              <div className="section-head" style={{ marginTop: 24 }}><h2>Commands from this site</h2></div>
              <div className="stat-card">
                {commands.length === 0 && <div className="dim">No commands yet. Use Sync Now or Request Digest on the Overview.</div>}
                {commands.map((cm) => (
                  <Row key={cm.id} label={`${cm.kind} · ${new Date(cm.requested_at).toLocaleTimeString()}`} v={`${cm.status}${cm.result ? ": " + cm.result : ""}`} />
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ label, v }: { label: string; v?: string | null }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "5px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}>
      <span className="dim">{label}</span>
      <span className="mono" style={{ textAlign: "right" }}>{v || "-"}</span>
    </div>
  );
}
