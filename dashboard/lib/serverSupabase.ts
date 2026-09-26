import { createClient } from "@supabase/supabase-js";

/** Server-side client. Uses the service role key when configured, otherwise the anon key (migration 005 lets it queue commands). */
export function serverSupabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  return createClient(url, key, { auth: { persistSession: false } });
}

export const HOMELAB_ONLINE_SECONDS = 180;

export async function homelabOnline(client: ReturnType<typeof serverSupabase>) {
  const { data } = await client.from("pipeline_status").select("updated_at").eq("id", 1).maybeSingle();
  if (!data) return false;
  return Date.now() - new Date(data.updated_at).getTime() < HOMELAB_ONLINE_SECONDS * 1000;
}
