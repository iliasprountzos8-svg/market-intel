"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function DigestEditorPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    summary: "",
    themes: "",
    guidance: "",
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    const periodEnd = new Date();
    const periodStart = new Date();
    periodStart.setHours(periodStart.getHours() - 24); // 24h default window

    const payload = {
      period_start: periodStart.toISOString(),
      period_end: periodEnd.toISOString(),
      articles_covered: 0, // In a real app we'd compute this from the DB
      summary: formData.summary,
      key_themes: formData.themes.split(",").map(t => t.trim()).filter(Boolean),
      guidance: formData.guidance,
    };

    const { error } = await supabase.from("digests").insert(payload);
    
    if (error) {
      alert("Failed to save digest: " + error.message);
      setLoading(false);
    } else {
      router.push("/");
    }
  };

  return (
    <div className="container" style={{ maxWidth: '600px', marginTop: '40px' }}>
      <div className="page-head">
        <h1>Write Digest</h1>
        <div className="sub">Publish a new market summary</div>
      </div>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontWeight: 700, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Market Summary</label>
          <textarea 
            required
            rows={6}
            value={formData.summary}
            onChange={(e) => setFormData({ ...formData, summary: e.target.value })}
            style={{ padding: '12px', fontFamily: 'var(--font-serif)', fontSize: '15px', border: '1px solid var(--border-strong)' }}
            placeholder="What happened in the market today?"
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontWeight: 700, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Key Themes</label>
          <input 
            type="text"
            required
            value={formData.themes}
            onChange={(e) => setFormData({ ...formData, themes: e.target.value })}
            style={{ padding: '12px', fontFamily: 'var(--font-body)', fontSize: '14px', border: '1px solid var(--border-strong)' }}
            placeholder="e.g. Fed Rate Cut, Nvidia Earnings, Energy Rally"
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontWeight: 700, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Strategic Guidance</label>
          <textarea 
            rows={4}
            value={formData.guidance}
            onChange={(e) => setFormData({ ...formData, guidance: e.target.value })}
            style={{ padding: '12px', fontFamily: 'var(--font-serif)', fontSize: '15px', border: '1px solid var(--border-strong)' }}
            placeholder="What should we do about it? (Optional)"
          />
        </div>

        <button 
          type="submit" 
          disabled={loading}
          style={{
            background: "var(--ink)",
            color: "#fff",
            padding: "12px 24px",
            border: "none",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            cursor: loading ? "not-allowed" : "pointer",
            marginTop: "10px"
          }}
        >
          {loading ? "Publishing..." : "Publish Digest"}
        </button>
      </form>
    </div>
  );
}
