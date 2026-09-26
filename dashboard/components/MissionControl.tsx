"use client";

import { useEffect, useState } from "react";

type JobStatus = {
  pipeline: {
    status: "idle" | "running" | "success" | "failed";
    last_run: string | null;
    message: string;
  };
  schedule: {
    interval_minutes: number;
    next_run: string | null;
  };
  homelab?: {
    online: boolean;
    updated_at: string | null;
    age_seconds: number | null;
    counts: { total: number; h1: number; h24: number; worthy24: number } | null;
    finbert: { scored: number; n: number } | null;
  };
  commands?: { id: string; kind: string; status: string; result: string | null; finished_at: string | null; requested_at: string }[];
};

export function MissionControl() {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [countdown, setCountdown] = useState<string>("--:--");
  const [triggering, setTriggering] = useState(false);
  const [digestBusy, setDigestBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // Poll API for status (heartbeat from the homelab)
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/pipeline/status", { cache: "no-store" });
        if (res.ok) setStatus(await res.json());
      } catch (e) {
        console.error("Failed to fetch API status", e);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  // Countdown to the next full cycle
  useEffect(() => {
    if (!status?.schedule?.next_run) return;
    const tick = () => {
      const distance = new Date(status.schedule.next_run!).getTime() - Date.now();
      if (distance < 0) { setCountdown("00:00"); return; }
      const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
      const seconds = Math.floor((distance % (1000 * 60)) / 1000);
      setCountdown(`${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`);
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [status?.schedule?.next_run]);

  // Tell the user what the last command did (for two minutes)
  const lastDone = status?.commands?.find(
    (c) => c.finished_at && Date.now() - new Date(c.finished_at).getTime() < 120000 && ["done", "failed", "rejected"].includes(c.status)
  );

  const post = async (url: string, busy: (b: boolean) => void, label: string) => {
    busy(true);
    setNote(null);
    try {
      const res = await fetch(url, { method: "POST" });
      const json = await res.json();
      setNote(res.ok ? `${label}: ${json.message}` : `${label} failed: ${json.error ?? res.status}`);
    } catch {
      setNote(`${label} failed`);
    }
    setTimeout(() => busy(false), 2500);
    setTimeout(() => setNote(null), 12000);
  };

  const online = status?.homelab?.online ?? false;
  const isRunning = status?.pipeline.status === "running" || triggering || digestBusy;
  const hb = status?.homelab?.age_seconds;
  const hl = status?.homelab;

  return (
    <div className="mission-control">
      <div className="mc-scanlines"></div>

      <div className="mc-header">
        <h3 className="mc-title">Mission Control</h3>
        <div className={`mc-indicator ${isRunning ? "running" : online ? "idle" : ""}`} />
      </div>

      <div className="mc-body">
        <div className="mc-row">
          <span className="mc-label">Homelab:</span>
          <span className={`mc-value ${online ? "glow-green" : "glow-amber"}`}>
            {status == null ? "CONNECTING..." : online ? `ONLINE · heartbeat ${hb != null ? Math.round(hb) : "?"}s ago` : "OFFLINE"}
          </span>
        </div>

        <div className="mc-row">
          <span className="mc-label">Status:</span>
          <span className={`mc-value ${isRunning ? "glow-amber" : "glow-green"}`}>
            {isRunning ? "PROCESSING..." : online ? "AUTOPILOT ARMED" : "STANDBY"}
          </span>
        </div>

        <div className="mc-row">
          <span className="mc-label">Last Sync:</span>
          <span className="mc-value dim">
            {status?.pipeline.last_run ? new Date(status.pipeline.last_run).toLocaleTimeString() : "Never"}
          </span>
        </div>

        <div className="mc-row">
          <span className="mc-label">Next Cycle:</span>
          <span className="mc-value mono">{countdown}</span>
        </div>

        {hl?.counts && (
          <div className="mc-row">
            <span className="mc-label">Intake:</span>
            <span className="mc-value dim">
              {hl.counts.total.toLocaleString()} articles · +{hl.counts.h1}/h · {hl.counts.worthy24} key today
            </span>
          </div>
        )}

        {status?.pipeline.message && (
          <div className="mc-log">
            {"> "} {status.pipeline.message}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="mc-btn-sync"
          onClick={() => post("/api/pipeline/trigger", setTriggering, "Sync")}
          disabled={isRunning || !online}
          style={{ flex: 1 }}
        >
          {triggering ? "[ QUEUING... ]" : isRunning ? "[ WORKING ]" : "[ SYNC NOW ]"}
        </button>
        <button
          className="mc-btn-sync"
          onClick={() => post("/api/digest/request", setDigestBusy, "Digest")}
          disabled={isRunning || !online}
          style={{ flex: 1 }}
        >
          {digestBusy ? "[ QUEUING... ]" : "[ REQUEST DIGEST ]"}
        </button>
      </div>
      {note && <div className="mc-log" style={{ marginTop: 8 }}>{"> "} {note}</div>}
      {!note && lastDone && (
        <div className="mc-log" style={{ marginTop: 8 }}>
          {"> "} {lastDone.kind} {lastDone.status}: {lastDone.result}
        </div>
      )}
    </div>
  );
}
