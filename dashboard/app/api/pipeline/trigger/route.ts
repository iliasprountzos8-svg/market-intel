import { NextResponse } from "next/server";

// Replaces the FastAPI's POST /api/v1/jobs/run-cycle: dispatches the
// cycle.yml GitHub Actions workflow on demand instead of shelling out to a
// Python subprocess on a server that would need to run 24/7.

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_OWNER = process.env.GITHUB_OWNER;
const GITHUB_REPO = process.env.GITHUB_REPO;
const WORKFLOW_FILE = "cycle.yml";
const REF = process.env.GITHUB_BRANCH || "main";

export async function POST() {
  if (!GITHUB_TOKEN || !GITHUB_OWNER || !GITHUB_REPO) {
    return NextResponse.json(
      { error: "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO not configured" },
      { status: 500 }
    );
  }

  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: REF }),
      }
    );
    if (res.status !== 204) {
      const body = await res.text();
      return NextResponse.json({ error: `GitHub API error ${res.status}: ${body}` }, { status: 502 });
    }
    return NextResponse.json({ message: "Pipeline run dispatched via GitHub Actions.", status: "running" });
  } catch (e: any) {
    return NextResponse.json({ error: e.message ?? "Failed to reach GitHub" }, { status: 500 });
  }
}
