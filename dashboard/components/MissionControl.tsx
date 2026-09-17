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
};

export function MissionControl() {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [countdown, setCountdown] = useState<string>("--:--");
  const [triggering, setTriggering] = useState(false);
  const [digestBusy, setDigestBusy] = useState(false);
  const [digestMsg, setDigestMsg] = useState<string | null>(null);

  // Poll API for status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/pipeline/status");
        if (res.ok) {
          const data = await res.json();
          setStatus(data);
        }
      } catch (e) {
        console.error("Failed to fetch API status", e);
      }
    };
    
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  // Compute countdown timer
  useEffect(() => {
    if (!status?.schedule?.next_run) return;
    
    const tick = () => {
      const nextRunTime = new Date(status.schedule.next_run!).getTime();
      const now = new Date().getTime();
      const distance = nextRunTime - now;

      if (distance < 0) {
        setCountdown("00:00");
        return;
      }

      const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
      const seconds = Math.floor((distance % (1000 * 60)) / 1000);
      setCountdown(`${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`);
    };

    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [status?.schedule?.next_run]);

  const handleSyncNow = async () => {
    if (status?.pipeline.status === "running") return;
    setTriggering(true);
    try {
      await fetch("/api/pipeline/trigger", { method: "POST" });
      // The polling will pick up the "running" state in ~3s
      setTimeout(() => setTriggering(false), 3000);
    } catch (e) {
      console.error(e);
      setTriggering(false);
    }
  };

  const handleRequestDigest = async () => {
    setDigestBusy(true);
    setDigestMsg(null);
    try {
      const res = await fetch("/api/digest/request", { method: "POST" });
      const json = await res.json();
      setDigestMsg(res.ok ? `Push: ${json.push}` : "Request failed");
    } catch (e) {
      setDigestMsg("Request failed");
    }
    setDigestBusy(false);
    setTimeout(() => setDigestMsg(null), 5000);
  };

  const isRunning = status?.pipeline.status === "running" || triggering;

  return (
    <div className="mission-control">
      <div className="mc-scanlines"></div>
      
      <div className="mc-header">
        <h3 className="mc-title">Mission Control</h3>
        <div className={`mc-indicator ${isRunning ? "running" : "idle"}`} />
      </div>

      <div className="mc-body">
        <div className="mc-row">
          <span className="mc-label">Status:</span>
          <span className={`mc-value ${isRunning ? "glow-amber" : "glow-green"}`}>
            {isRunning ? "PROCESSING PIPELINE..." : "AUTOPILOT ARMED"}
          </span>
        </div>
        
        <div className="mc-row">
          <span className="mc-label">Last Sync:</span>
          <span className="mc-value dim">
            {status?.pipeline.last_run ? new Date(status.pipeline.last_run).toLocaleTimeString() : "Never"}
          </span>
        </div>
        
        <div className="mc-row">
          <span className="mc-label">Next Sync:</span>
          <span className="mc-value mono">{countdown}</span>
        </div>
        
        {isRunning && status?.pipeline.message && (
          <div className="mc-log">
            {"> "} {status.pipeline.message}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="mc-btn-sync"
          onClick={handleSyncNow}
          disabled={isRunning}
          style={{ flex: 1 }}
        >
          {isRunning ? "[ SYNC IN PROGRESS ]" : "[ SYNC NOW ]"}
        </button>
        <button
          className="mc-btn-sync"
          onClick={handleRequestDigest}
          disabled={digestBusy}
          style={{ flex: 1 }}
        >
          {digestBusy ? "[ REQUESTING... ]" : "[ REQUEST DIGEST ]"}
        </button>
      </div>
      {digestMsg && (
        <div className="mc-log" style={{ marginTop: 8 }}>
          {"> "} {digestMsg}
        </div>
      )}
    </div>
  );
}
