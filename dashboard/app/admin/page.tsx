"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { getSourcesConfig, toggleSourceEnabled } from "./actions";

interface SourceHealth {
  id: string;
  source: string;
  entries_fetched: number;
  errors: number;
  full_text_fetched: number;
  full_text_failed: number;
  skipped_domains: number;
  created_at: string;
}

interface SourceConfig {
  name: string;
  url: string;
  category: string;
  enabled: boolean;
}

export default function AdminPage() {
  const [sources, setSources] = useState<SourceConfig[]>([]);
  const [healthLogs, setHealthLogs] = useState<SourceHealth[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      const config = await getSourcesConfig();
      setSources(config.sources || []);
      
      const { data } = await supabase
        .from("source_health")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(50);
        
      if (data) {
        setHealthLogs(data as SourceHealth[]);
      }
      setLoading(false);
    }
    load();
  }, []);

  const handleToggle = async (name: string, currentEnabled: boolean) => {
    // Optimistic update
    setSources(s => s.map(x => x.name === name ? { ...x, enabled: !currentEnabled } : x));
    await toggleSourceEnabled(name, !currentEnabled);
  };

  if (loading) {
    return (
      <div className="container">
        <div className="page-head"><h1>System Admin</h1></div>
        <div className="skeleton" style={{ height: 200 }} />
      </div>
    );
  }

  return (
    <div className="container">
      <div className="page-head">
        <h1>System Admin</h1>
        <div className="sub">Manage RSS sources and view scraper health</div>
      </div>

      <div className="section-head">
        <h2>Source Configuration</h2>
      </div>
      
      <div className="market-table-wrap">
        <table className="market-table">
          <thead>
            <tr>
              <th>Source Name</th>
              <th>Category</th>
              <th>URL</th>
              <th style={{ textAlign: "right" }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((src) => (
              <tr key={src.name} style={{ opacity: src.enabled ? 1 : 0.5 }}>
                <td className="strong">{src.name}</td>
                <td><span className="group-pill">{src.category}</span></td>
                <td className="dim mono">{src.url}</td>
                <td style={{ textAlign: "right" }}>
                  <button 
                    onClick={() => handleToggle(src.name, src.enabled)}
                    style={{
                      background: src.enabled ? "var(--ink)" : "transparent",
                      color: src.enabled ? "#fff" : "var(--text)",
                      border: "1px solid var(--border-strong)",
                      padding: "4px 10px",
                      borderRadius: "999px",
                      fontSize: "11px",
                      fontWeight: 700,
                      cursor: "pointer",
                      textTransform: "uppercase"
                    }}
                  >
                    {src.enabled ? "Enabled" : "Disabled"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section-head" style={{ marginTop: "40px" }}>
        <h2>Scraper Health Logs (Last 50)</h2>
      </div>

      <div className="market-table-wrap">
        <table className="market-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th style={{ textAlign: "right" }}>RSS Fetched</th>
              <th style={{ textAlign: "right" }}>RSS Errors</th>
              <th style={{ textAlign: "right" }}>Full-Text Fetched</th>
              <th style={{ textAlign: "right" }}>Full-Text Failed</th>
            </tr>
          </thead>
          <tbody>
            {healthLogs.map((log) => (
              <tr key={log.id}>
                <td className="dim mono">{new Date(log.created_at).toLocaleString()}</td>
                <td className="strong">{log.source}</td>
                <td style={{ textAlign: "right" }} className="mono">{log.entries_fetched ?? "-"}</td>
                <td style={{ textAlign: "right", color: log.errors > 0 ? "var(--bear)" : "inherit" }} className="mono">
                  {log.errors ?? "-"}
                </td>
                <td style={{ textAlign: "right" }} className="mono">{log.full_text_fetched ?? "-"}</td>
                <td style={{ textAlign: "right", color: log.full_text_failed > 0 ? "var(--bear)" : "inherit" }} className="mono">
                  {log.full_text_failed ?? "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {healthLogs.length === 0 && <div className="empty">No health logs found. Make sure you ran the SQL migration.</div>}
      </div>
    </div>
  );
}
