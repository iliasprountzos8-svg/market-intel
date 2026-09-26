import { NextResponse } from "next/server";
import { serverSupabase, homelabOnline } from "@/lib/serverSupabase";

// "Sync Now": the homelab is the primary pipeline. This route queues a `sync` command that the homelab
// picks up within seconds (it polls outbound, so the homelab never has to be reachable from the internet).
// If the homelab has been silent for 3+ minutes, it falls back to dispatching the GitHub Actions cycle.

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_OWNER = process.env.GITHUB_OWNER;
const GITHUB_REPO = process.env.GITHUB_REPO;
const REF = process.env.GITHUB_BRANCH || "main";

export const dynamic = "force-dynamic";

export async function POST() {
  const db = serverSupabase();
  try {
    if (!(await homelabOnline(db))) {
      if (GITHUB_TOKEN && GITHUB_OWNER && GITHUB_REPO) {
        const res = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/cycle.yml/dispatches`, {
          method: "POST",
          headers: { Authorization: `Bearer ${GITHUB_TOKEN}`, Accept: "application/vnd.github+json", "Content-Type": "application/json" },
          body: JSON.stringify({ ref: REF }),
        });
        if (res.status === 204) return NextResponse.json({ message: "Homelab is offline: ran the backup pipeline on GitHub Actions.", status: "running", via: "github" });
      }
      return NextResponse.json({ error: "Homelab is offline and no fallback is configured." }, { status: 503 });
    }
    const { data: open } = await db.from("commands").select("id").eq("kind", "sync").in("status", ["pending", "running"]).limit(1);
    if (open && open.length) return NextResponse.json({ message: "A sync is already queued or running.", status: "running", id: open[0].id });
    const { data, error } = await db.from("commands").insert({ kind: "sync", status: "pending", requested_by: "site" }).select("id").single();
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    return NextResponse.json({ message: "Sync queued: the homelab is picking it up.", status: "running", id: data.id, via: "homelab" });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to queue sync" }, { status: 500 });
  }
}
