"use client";

import { useEffect } from "react";

export function UIFeedback() {
  useEffect(() => {
    // Only initialize AudioContext after first interaction to bypass autoplay restrictions
    let audioCtx: AudioContext | null = null;
    let initialized = false;

    const playClick = () => {
      if (!audioCtx) return;
      if (audioCtx.state === "suspended") audioCtx.resume();
      
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      
      osc.type = "sine";
      osc.frequency.setValueAtTime(800, audioCtx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(300, audioCtx.currentTime + 0.05);
      
      gain.gain.setValueAtTime(0.02, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.05);
      
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      
      osc.start();
      osc.stop(audioCtx.currentTime + 0.05);
    };

    const playKeystroke = () => {
      if (!audioCtx) return;
      if (audioCtx.state === "suspended") audioCtx.resume();

      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();

      osc.type = "square";
      osc.frequency.setValueAtTime(150, audioCtx.currentTime);
      
      gain.gain.setValueAtTime(0.01, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.02);
      
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      
      osc.start();
      osc.stop(audioCtx.currentTime + 0.02);
    };

    const handleInteraction = (e: Event) => {
      if (!initialized) {
        audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
        initialized = true;
      }
    };

    const handleClick = (e: MouseEvent) => {
      // Play on link clicks or button clicks
      const target = e.target as HTMLElement;
      if (target.closest("a") || target.closest("button") || target.closest(".cmd-item")) {
        playClick();
      }
    };

    const handleKeydown = (e: KeyboardEvent) => {
      // Play soft keystroke sound on command palette overlay or when navigating
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter") {
        playKeystroke();
      }
    };

    window.addEventListener("mousedown", handleInteraction, { once: true });
    window.addEventListener("keydown", handleInteraction, { once: true });
    
    window.addEventListener("click", handleClick);
    window.addEventListener("keydown", handleKeydown);

    return () => {
      window.removeEventListener("mousedown", handleInteraction);
      window.removeEventListener("keydown", handleInteraction);
      window.removeEventListener("click", handleClick);
      window.removeEventListener("keydown", handleKeydown);
      if (audioCtx) audioCtx.close();
    };
  }, []);

  return null; // Invisible component
}
