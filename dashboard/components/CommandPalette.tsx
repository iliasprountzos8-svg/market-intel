"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";

type Command = {
  id: string;
  label: string;
  action: () => void;
  icon?: string;
};

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // Define commands
  const commands: Command[] = [
    { id: "sync", label: "Trigger Manual Sync", icon: "⚡", action: async () => {
      try {
        await fetch("/api/pipeline/trigger", { method: "POST" });
      } catch (e) { console.error(e); }
    }},
    { id: "nav-overview", label: "Go to Overview", icon: "📊", action: () => router.push("/") },
    { id: "nav-track", label: "Go to Track Record", icon: "🎯", action: () => router.push("/track-record") },
    { id: "nav-portfolio", label: "Go to Portfolio", icon: "💼", action: () => router.push("/portfolio") },
    { id: "nav-markets", label: "Go to Markets", icon: "📈", action: () => router.push("/markets") },
    { id: "nav-news", label: "Go to News", icon: "📰", action: () => router.push("/news") },
  ];

  const filteredCommands = commands.filter(c => c.label.toLowerCase().includes(query.toLowerCase()));

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen(o => !o);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex(i => (i + 1) % filteredCommands.length);
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex(i => (i - 1 + filteredCommands.length) % filteredCommands.length);
    }
    if (e.key === "Enter" && filteredCommands.length > 0) {
      e.preventDefault();
      filteredCommands[selectedIndex].action();
      setOpen(false);
    }
  };

  if (!open) return null;

  return (
    <div className="cmd-overlay" onClick={() => setOpen(false)}>
      <div className="cmd-modal" onClick={e => e.stopPropagation()}>
        <div className="cmd-header">
          <input
            ref={inputRef}
            className="cmd-input"
            placeholder="Type a command or search..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className="cmd-list">
          {filteredCommands.length === 0 && <div className="cmd-empty">No results found.</div>}
          {filteredCommands.map((cmd, idx) => (
            <div
              key={cmd.id}
              className={`cmd-item ${idx === selectedIndex ? "selected" : ""}`}
              onMouseEnter={() => setSelectedIndex(idx)}
              onClick={() => {
                cmd.action();
                setOpen(false);
              }}
            >
              <span className="cmd-icon">{cmd.icon}</span>
              <span className="cmd-label">{cmd.label}</span>
              {idx === selectedIndex && <span className="cmd-hint">Enter ↵</span>}
            </div>
          ))}
        </div>
        <div className="cmd-footer">
          <span className="mono text-dim" style={{ fontSize: 10 }}>Press ESC to close</span>
        </div>
      </div>
    </div>
  );
}
