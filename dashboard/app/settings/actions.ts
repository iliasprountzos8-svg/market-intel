"use server";

import { createClient } from "@supabase/supabase-js";

export type AppSettings = {
  push_enabled: boolean;
  email_enabled: boolean;
  notify_email: string | null;
  relevance_threshold: number;
};

function getServiceClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!
  );
}

export async function getSettings(): Promise<AppSettings> {
  const supabase = getServiceClient();
  const { data } = await supabase.from("app_settings").select("*").eq("id", 1).maybeSingle();
  return {
    push_enabled: data?.push_enabled ?? false,
    email_enabled: data?.email_enabled ?? false,
    notify_email: data?.notify_email ?? null,
    relevance_threshold: data?.relevance_threshold ?? 70,
  };
}

export async function updateSettings(patch: Partial<AppSettings>) {
  try {
    const supabase = getServiceClient();
    const { error } = await supabase
      .from("app_settings")
      .update({ ...patch, updated_at: new Date().toISOString() })
      .eq("id", 1);
    if (error) throw error;
    return { success: true };
  } catch (error) {
    console.error("Error updating app_settings:", error);
    return { success: false, error: String(error) };
  }
}

export async function savePushSubscription(subscription: {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}) {
  try {
    const supabase = getServiceClient();
    const { error } = await supabase.from("push_subscriptions").upsert(
      {
        endpoint: subscription.endpoint,
        p256dh: subscription.keys.p256dh,
        auth: subscription.keys.auth,
      },
      { onConflict: "endpoint" }
    );
    if (error) throw error;
    return { success: true };
  } catch (error) {
    console.error("Error saving push subscription:", error);
    return { success: false, error: String(error) };
  }
}

export async function removePushSubscription(endpoint: string) {
  try {
    const supabase = getServiceClient();
    const { error } = await supabase.from("push_subscriptions").delete().eq("endpoint", endpoint);
    if (error) throw error;
    return { success: true };
  } catch (error) {
    console.error("Error removing push subscription:", error);
    return { success: false, error: String(error) };
  }
}
