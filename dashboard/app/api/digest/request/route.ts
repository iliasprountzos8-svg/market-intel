import { NextResponse } from "next/server";
import { serverSupabase, homelabOnline } from "@/lib/serverSupabase";

// "Request Digest": the homelab now writes a real digest itself (headless Claude reads the AI-scored news,
// market prices and signals, then writes the digest and its falsifiable calls). This queues that job.

export const dynamic = "force-dynamic";

export async function POST() {
  const db = serverSupabase();
  try {
    if (!(await homelabOnline(db))) {
      return NextResponse.json({ error: "Homelab is offline: can't write a digest right now." }, { status: 503 });
    }
    const { data: open } = await db.from("commands").select("id").eq("kind", "digest").in("status", ["pending", "running"]).limit(1);
    if (open && open.length) return NextResponse.json({ message: "A digest is already being written.", status: "running", id: open[0].id });
    const { data, error } = await db.from("commands").insert({ kind: "digest", status: "pending", requested_by: "site" }).select("id").single();
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    return NextResponse.json({ message: "Digest queued: the homelab is writing it (about a minute).", status: "running", id: data.id });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to queue digest" }, { status: 500 });
  }
}
