"use client";

import { useEffect, useState } from "react";
import {
  getSettings,
  updateSettings,
  savePushSubscription,
  removePushSubscription,
  type AppSettings,
} from "./actions";

const VAPID_PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;

function urlBase64ToUint8Array(base64String: string) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pushSupported, setPushSupported] = useState(false);
  const [pushSubscribed, setPushSubscribed] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    getSettings().then((s) => {
      setSettings(s);
      setLoading(false);
    });

    if ("serviceWorker" in navigator && "PushManager" in window) {
      setPushSupported(true);
      navigator.serviceWorker.ready.then(async (reg) => {
        const sub = await reg.pushManager.getSubscription();
        setPushSubscribed(!!sub);
      });
    }
  }, []);

  const flash = (text: string) => {
    setMessage(text);
    setTimeout(() => setMessage(null), 3000);
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true);
    const res = await updateSettings(settings);
    setSaving(false);
    flash(res.success ? "Settings saved." : `Failed to save: ${res.error}`);
  };

  const handlePushToggle = async () => {
    if (!VAPID_PUBLIC_KEY) {
      flash("Push isn't configured (missing VAPID public key).");
      return;
    }
    setPushBusy(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      if (pushSubscribed) {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await removePushSubscription(sub.endpoint);
          await sub.unsubscribe();
        }
        setPushSubscribed(false);
        flash("Push notifications turned off on this device.");
      } else {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          flash("Notification permission was not granted.");
          setPushBusy(false);
          return;
        }
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
        });
        const json = sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } };
        await savePushSubscription({ endpoint: json.endpoint, keys: json.keys });
        setPushSubscribed(true);
        flash("Push notifications enabled on this device.");
      }
    } catch (e: any) {
      flash(`Push setup failed: ${e.message ?? e}`);
    }
    setPushBusy(false);
  };

  if (loading || !settings) {
    return (
      <div className="container">
        <div className="page-head"><h1>Settings</h1></div>
        <div className="skeleton" style={{ height: 200 }} />
      </div>
    );
  }

  return (
    <div className="container">
      <div className="page-head">
        <h1>Settings</h1>
        <div className="sub">Notifications and alert thresholds</div>
      </div>

      <div className="section-head"><h2>Push Notifications</h2></div>
      <p className="dim" style={{ marginBottom: 12, fontSize: 13 }}>
        Get a push notification on this device when a high-relevance article about
        NVDA/MSFT/GOOGL/ASML is found, or when a new daily digest is written.
        Requires installing this as an app (see your browser&apos;s &quot;Add to Home
        Screen&quot; / &quot;Install&quot; option) for reliable delivery on mobile.
      </p>
      {!pushSupported && (
        <div className="empty">Push notifications aren&apos;t supported in this browser.</div>
      )}
      {pushSupported && (
        <button
          onClick={handlePushToggle}
          disabled={pushBusy}
          style={{
            background: pushSubscribed ? "var(--ink)" : "transparent",
            color: pushSubscribed ? "#fff" : "var(--text)",
            border: "1px solid var(--border-strong)",
            padding: "8px 16px",
            borderRadius: "999px",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            textTransform: "uppercase",
            marginBottom: 24,
          }}
        >
          {pushBusy ? "Working..." : pushSubscribed ? "Push Enabled on This Device" : "Enable Push on This Device"}
        </button>
      )}

      <div className="section-head"><h2>Email Digest</h2></div>
      <div style={{ marginBottom: 24, display: "flex", flexDirection: "column", gap: 12, maxWidth: 420 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={settings.email_enabled}
            onChange={(e) => setSettings({ ...settings, email_enabled: e.target.checked })}
          />
          Email me high-relevance articles and new digests
        </label>
        <input
          type="email"
          placeholder="you@example.com"
          value={settings.notify_email ?? ""}
          onChange={(e) => setSettings({ ...settings, notify_email: e.target.value })}
          style={{ padding: "8px 10px", fontSize: 13, border: "1px solid var(--border-strong)" }}
        />
      </div>

      <div className="section-head"><h2>Alert Threshold</h2></div>
      <div style={{ marginBottom: 24, maxWidth: 420 }}>
        <div style={{ fontSize: 13, marginBottom: 8 }}>
          Only notify for articles with relevance &ge; <strong>{settings.relevance_threshold}</strong>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={settings.relevance_threshold}
          onChange={(e) => setSettings({ ...settings, relevance_threshold: parseInt(e.target.value, 10) })}
          style={{ width: "100%" }}
        />
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        style={{
          background: "var(--ink)",
          color: "#fff",
          border: "none",
          padding: "10px 20px",
          borderRadius: "999px",
          fontSize: 12,
          fontWeight: 700,
          cursor: "pointer",
          textTransform: "uppercase",
        }}
      >
        {saving ? "Saving..." : "Save Settings"}
      </button>
      {message && <span style={{ marginLeft: 12, fontSize: 12 }} className="dim">{message}</span>}
    </div>
  );
}
