import { NextResponse } from "next/server";
import { serverSupabase, HOMELAB_ONLINE_SECONDS } from "@/lib/serverSupabase";

// Status now comes from the homelab's own heartbeat (pipeline_status) and the commands queue.
export const dynamic = "force-dynamic";

function lastFinished(...texts: (string | undefined)[]): string | null {
  let best: string | null = null;
  for (const t of texts) {
    const m = /finished (\S+)/.exec(t ?? "");
    if (m && (!best || m[1] > best)) best = m[1];
  }
  return best;
}

function nextCycle(): string {
  // the full cycle runs at :07 and :37 past each hour
  const now = new Date();
  const next = new Date(now);
  next.setSeconds(0, 0);
  const m = now.getMinutes();
  if (m < 7) next.setMinutes(7);
  else if (m < 37) next.setMinutes(37);
  else { next.setHours(next.getHours() + 1); next.setMinutes(7); }
  return next.toISOString();
}

export async function GET() {
  const db = serverSupabase();
  try {
    const [{ data: ps }, { data: cmds }] = await Promise.all([
      db.from("pipeline_status").select("*").eq("id", 1).maybeSingle(),
      db.from("commands").select("*").order("requested_at", { ascending: false }).limit(6),
    ]);
    const d = ps?.data ?? null;
    const ageSec = ps ? (Date.now() - new Date(ps.updated_at).getTime()) / 1000 : null;
    const online = ageSec !== null && ageSec < HOMELAB_ONLINE_SECONDS;
    const activeCmd = (cmds ?? []).find((c: any) => c.status === "pending" || c.status === "running");
    const busy = !!activeCmd || !!d?.cycle?.running || !!d?.fast?.running || !!d?.digest?.running;

    let message = "Homelab idle";
    if (!ps) message = "Waiting for the first homelab heartbeat (apply migration 005)";
    else if (!online) message = "Homelab offline (no heartbeat for " + Math.round((ageSec ?? 0) / 60) + " min)";
    else if (activeCmd?.kind === "digest") message = activeCmd.status === "pending" ? "Digest queued..." : "Claude is writing the digest on the homelab...";
    else if (activeCmd?.kind === "sync") message = activeCmd.status === "pending" ? "Sync queued..." : "Polling every source, scoring and filling AI fields...";
    else if (d?.cycle?.running) message = "Full 30-minute cycle running on the homelab...";
    else if (d?.fast?.running) message = "Fast lane running...";
    else message = d?.sync?.text || d?.fast?.text || d?.cycle?.text || message;

    let status: "idle" | "running" | "success" | "failed" = "idle";
    if (busy) status = "running";
    else if (online) status = "success";
    else if (ps) status = "failed";

    return NextResponse.json({
      pipeline: { status, last_run: lastFinished(d?.cycle?.text, d?.fast?.text) ?? ps?.updated_at ?? null, message },
      schedule: { interval_minutes: 30, next_run: nextCycle() },
      homelab: { online, updated_at: ps?.updated_at ?? null, age_seconds: ageSec, counts: d?.counts ?? null, finbert: d?.finbert ?? null },
      command_busy: !!activeCmd,
      commands: cmds ?? [],
    });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to read status" }, { status: 500 });
  }
}
