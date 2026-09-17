"use client";

import { useEffect, useState } from "react";

export default function BootSequence() {
  const [booting, setBooting] = useState(true);
  const [lines, setLines] = useState<string[]>([]);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Only play once per session
    if (sessionStorage.getItem("booted")) {
      setBooting(false);
      return;
    }
    
    setVisible(true);
    sessionStorage.setItem("booted", "true");

    const bootSequence = [
      "INITIALIZING TERMINAL...",
      "AUTHENTICATING SECURE CONNECTION [OK]",
      "LOADING AI PIPELINE MODELS [OK]",
      "CONNECTING TO LIVE MARKET FEED [OK]",
      "ACCESS GRANTED.",
    ];

    let step = 0;
    const interval = setInterval(() => {
      setLines((prev) => [...prev, bootSequence[step]]);
      step++;
      if (step >= bootSequence.length) {
        clearInterval(interval);
        setTimeout(() => setVisible(false), 800);
        setTimeout(() => setBooting(false), 1200);
      }
    }, 300);

    return () => clearInterval(interval);
  }, []);

  if (!booting) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "#000",
        color: "#4ade80",
        fontFamily: "var(--font-mono)",
        fontSize: "14px",
        zIndex: 9999,
        padding: "2rem",
        opacity: visible ? 1 : 0,
        transition: "opacity 0.4s ease",
        display: "flex",
        flexDirection: "column",
        pointerEvents: "none",
      }}
    >
      <div className="scanlines" style={{ position: "absolute", inset: 0, opacity: 0.2, pointerEvents: "none" }} />
      {lines.map((line, i) => (
        <div key={i} style={{ marginBottom: "0.5rem", textShadow: "0 0 8px rgba(74, 222, 128, 0.5)" }}>
          {line}
        </div>
      ))}
      {visible && lines.length < 5 && (
        <div style={{ marginTop: "0.5rem", width: "1ch", height: "1.2em", backgroundColor: "#4ade80", animation: "blink 1s step-end infinite" }} />
      )}
    </div>
  );
}
