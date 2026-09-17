import { NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";
import webpush from "web-push";

// Honest about what this does: there's no live, unattended AI reasoning
// pass (see README "3. On-demand analysis" -- deliberate, to stay at $0 in
// API cost). This route can't produce a digest by itself. What it CAN do
// is immediately notify you on your enabled channels so you can trigger
// the real thing (open a Claude Code session, run /digest) -- turning "go
// remember to ask Claude" into "get pinged, then say /digest."

function getServiceClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!
  );
}

export async function POST() {
  const supabase = getServiceClient();

  const { data: settings } = await supabase.from("app_settings").select("*").eq("id", 1).maybeSingle();
  const pushEnabled = settings?.push_enabled ?? false;
  const emailEnabled = settings?.email_enabled ?? false;

  const result = { push: "skipped" as string, email: "skipped" as string };

  if (pushEnabled) {
    const vapidPrivateKey = process.env.VAPID_PRIVATE_KEY;
    const vapidPublicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
    const vapidSubject = process.env.VAPID_SUBJECT;

    if (!vapidPrivateKey || !vapidPublicKey || !vapidSubject) {
      result.push = "not configured (missing VAPID env vars)";
    } else {
      webpush.setVapidDetails(vapidSubject, vapidPublicKey, vapidPrivateKey);
      const { data: subs } = await supabase.from("push_subscriptions").select("*");
      const payload = JSON.stringify({
        title: "Digest requested",
        body: "Open Claude Code in market-intel and run /digest to fill this in.",
        url: "/",
      });
      let sent = 0;
      for (const sub of subs ?? []) {
        try {
          await webpush.sendNotification(
            { endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } },
            payload
          );
          sent++;
        } catch (e: any) {
          if (e.statusCode === 404 || e.statusCode === 410) {
            await supabase.from("push_subscriptions").delete().eq("endpoint", sub.endpoint);
          }
        }
      }
      result.push = `sent to ${sent} device(s)`;
    }
  } else {
    result.push = "disabled in settings";
  }

  if (emailEnabled) {
    // Email sending only exists on the Python/GitHub Actions side today
    // (analysis/notify.py, via Gmail SMTP) -- not wired here yet.
    result.email = "email_enabled is on, but this route doesn't send email yet -- use notify.py's path";
  } else {
    result.email = "disabled in settings";
  }

  return NextResponse.json({ requested_at: new Date().toISOString(), ...result });
}
