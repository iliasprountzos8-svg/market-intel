"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, IChartApi, ISeriesApi, Time, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";

interface TradingChartProps {
  ticker: string;
  call: string;
  callTime: string;
}

export function TradingChart({ ticker, call, callTime }: TradingChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Initialize chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#111" },
        textColor: "#aaa",
      },
      grid: {
        vertLines: { color: "#222" },
        horzLines: { color: "#222" },
      },
      width: chartContainerRef.current.clientWidth,
      height: 300,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });
    chartRef.current = chart;

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#4ade80",
      downColor: "#f87171",
      borderVisible: false,
      wickUpColor: "#4ade80",
      wickDownColor: "#f87171",
    });
    seriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener("resize", handleResize);

    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        // Fetch from new FastAPI endpoint
        const res = await fetch(`/api/market-history?ticker=${ticker}&days=30`);
        if (!res.ok) throw new Error("Failed to fetch market data");
        const json = await res.json();
        
        if (json.data && json.data.length > 0) {
          candlestickSeries.setData(json.data.map((d: any) => ({
            time: d.time as Time,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
          })));

          // Add marker for the AI call
          const callTimestamp = Math.floor(new Date(callTime).getTime() / 1000) as Time;
          
          createSeriesMarkers(candlestickSeries, [
            {
              time: callTimestamp,
              position: call === "bullish" ? "belowBar" : call === "bearish" ? "aboveBar" : "inBar",
              color: call === "bullish" ? "#4ade80" : call === "bearish" ? "#f87171" : "#fbbf24",
              shape: call === "bullish" ? "arrowUp" : call === "bearish" ? "arrowDown" : "circle",
              text: `AI CALL: ${call.toUpperCase()}`,
            },
          ]);
          
          chart.timeScale().fitContent();
        } else {
          setError("No price data available.");
        }
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };

    fetchData();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [ticker, call, callTime]);

  return (
    <div style={{ position: "relative", width: "100%", height: 300, border: "1px solid #333", borderRadius: 4, overflow: "hidden", marginTop: 12 }}>
      {loading && <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", color: "#4ade80", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading market data...</div>}
      {error && <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", color: "#f87171", fontFamily: "var(--font-mono)", fontSize: 12 }}>{error}</div>}
      <div ref={chartContainerRef} style={{ width: "100%", height: "100%", opacity: loading ? 0.3 : 1, transition: "opacity 0.3s" }} />
    </div>
  );
}
