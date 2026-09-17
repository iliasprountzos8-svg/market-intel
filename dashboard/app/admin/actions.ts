"use server";

import fs from "fs/promises";
import path from "path";
import { createClient } from "@supabase/supabase-js";

const CONFIG_PATH = path.join(process.cwd(), "..", "config.json");

// Server-side only -- service role key bypasses RLS, needed to write
// app_settings / source_overrides / push_subscriptions.
function getServiceClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!
  );
}

export async function getSourcesConfig() {
  try {
    const data = await fs.readFile(CONFIG_PATH, "utf8");
    const config = JSON.parse(data);

    // Enabled/disabled state lives in Supabase, not config.json -- see
    // migration 002_notifications_and_settings.sql. A git-committed file
    // can't be toggled at runtime from a Vercel serverless function; the
    // scraper (GitHub Actions) merges these same overrides at scrape time.
    const supabase = getServiceClient();
    const { data: overrides } = await supabase.from("source_overrides").select("name,enabled");
    const overrideMap = new Map((overrides ?? []).map((o: any) => [o.name, o.enabled]));

    config.sources = (config.sources ?? []).map((src: any) => ({
      ...src,
      enabled: overrideMap.has(src.name) ? overrideMap.get(src.name) : src.enabled,
    }));

    return config;
  } catch (error) {
    console.error("Error reading sources config:", error);
    return { sources: [] };
  }
}

export async function toggleSourceEnabled(sourceName: string, enabled: boolean) {
  try {
    const supabase = getServiceClient();
    const { error } = await supabase
      .from("source_overrides")
      .upsert({ name: sourceName, enabled, updated_at: new Date().toISOString() });
    if (error) throw error;
    return { success: true };
  } catch (error) {
    console.error("Error updating source_overrides:", error);
    return { success: false, error: String(error) };
  }
}
