import { NextResponse } from "next/server";

// Replaces the FastAPI's /api/v1/jobs/status: reports the latest run of the
// cycle.yml GitHub Actions workflow (the actual, free, always-on scheduler --
// see .github/workflows/cycle.yml) instead of a Python-side APScheduler that
// would need a 24/7 host to exist.

export const dynamic = "force-dynamic";

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_OWNER = process.env.GITHUB_OWNER;
const GITHUB_REPO = process.env.GITHUB_REPO;
const WORKFLOW_FILE = "cycle.yml";
const INTERVAL_MINUTES = 30;

export async function GET() {
  if (!GITHUB_TOKEN || !GITHUB_OWNER || !GITHUB_REPO) {
    return NextResponse.json(
      { error: "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO not configured" },
      { status: 500 }
    );
  }

  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/runs?per_page=1`,
      {
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
        },
        cache: "no-store",
      }
    );
    if (!res.ok) {
      return NextResponse.json({ error: `GitHub API error ${res.status}` }, { status: 502 });
    }
    const json = await res.json();
    const run = json?.workflow_runs?.[0];

    let status: "idle" | "running" | "success" | "failed" = "idle";
    if (run) {
      if (run.status === "in_progress" || run.status === "queued") status = "running";
      else if (run.conclusion === "success") status = "success";
      else if (run.conclusion) status = "failed";
    }

    // cron runs land on the interval boundary; approximate the next one from
    // the wall clock rather than parsing the cron expression.
    const now = new Date();
    const next = new Date(now);
    next.setMinutes(Math.ceil((now.getMinutes() + 1) / INTERVAL_MINUTES) * INTERVAL_MINUTES, 0, 0);

    return NextResponse.json({
      pipeline: {
        status,
        last_run: run?.updated_at ?? null,
        message: run ? `Last run: ${run.status}${run.conclusion ? ` (${run.conclusion})` : ""}` : "No runs yet",
      },
      schedule: {
        interval_minutes: INTERVAL_MINUTES,
        next_run: next.toISOString(),
      },
    });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to reach GitHub" }, { status: 500 });
  }
}
