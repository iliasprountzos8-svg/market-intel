"use client";

import { useEffect, useState, useCallback } from "react";
import { supabase, PipelineStatus, TickerSignal, LabReport, Command } from "@/lib/supabase";

/** Live homelab status (heartbeat, pipeline progress, throughput, sources). */
export function useHomelab() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [commands, setCommands] = useState<Command[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    const [s, c] = await Promise.all([
      supabase.from("pipeline_status").select("*").eq("id", 1).maybeSingle(),
      supabase.from("commands").select("*").order("requested_at", { ascending: false }).limit(8),
    ]);
    if (s.data) setStatus(s.data as PipelineStatus);
    if (c.data) setCommands(c.data as Command[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const ch = supabase
      .channel("homelab-status")
      .on("postgres_changes", { event: "*", schema: "public", table: "pipeline_status" }, load)
      .on("postgres_changes", { event: "*", schema: "public", table: "commands" }, load)
      .subscribe();
    const poll = setInterval(load, 15000);
    const tick = setInterval(() => setNow(Date.now()), 5000);
    return () => { supabase.removeChannel(ch); clearInterval(poll); clearInterval(tick); };
  }, [load]);

  const ageSec = status ? Math.max(0, (now - new Date(status.updated_at).getTime()) / 1000) : null;
  return { status, data: status?.data ?? null, commands, loading, online: ageSec !== null && ageSec < 180, ageSec };
}

export function useTickerSignals() {
  const [rows, setRows] = useState<TickerSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    const { data } = await supabase.from("ticker_signals").select("*").limit(1000);
    if (data) setRows(data as TickerSignal[]);
    setLoading(false);
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 120000);
    return () => clearInterval(poll);
  }, [load]);
  return { rows, loading };
}

export function useLabReport() {
  const [lab, setLab] = useState<LabReport | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    const { data } = await supabase.from("lab_report").select("*").order("as_of", { ascending: false }).limit(1).maybeSingle();
    if (data) setLab(data as LabReport);
    setLoading(false);
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 300000);
    return () => clearInterval(poll);
  }, [load]);
  return { lab, loading };
}
