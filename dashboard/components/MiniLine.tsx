export function MiniLine({ values, width = 120, height = 28, color = "var(--accent)" }: { values: number[]; width?: number; height?: number; color?: string }) {
  const v = values.filter((x) => typeof x === "number" && !isNaN(x));
  if (v.length < 2) return <svg width={width} height={height} />;
  const mn = Math.min(...v), mx = Math.max(...v), r = mx - mn || 1;
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * width).toFixed(1)},${(height - 3 - ((y - mn) / r) * (height - 6)).toFixed(1)}`).join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function MiniBars({ values, width = 240, height = 44, color = "var(--accent)" }: { values: number[]; width?: number; height?: number; color?: string }) {
  if (!values.length) return <svg width={width} height={height} />;
  const mx = Math.max(1, ...values), bw = width / values.length;
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: "block" }}>
      {values.map((y, i) => {
        const bh = (y / mx) * (height - 2);
        return <rect key={i} x={i * bw + 1} y={height - bh} width={Math.max(1, bw - 2)} height={bh} fill={color} />;
      })}
    </svg>
  );
}

/** A centred bar: green to the right for positive, red to the left for negative. */
export function IdxBar({ v, width = 90 }: { v: number | null | undefined; width?: number }) {
  const x = Math.max(-1, Math.min(1, v ?? 0));
  return (
    <span style={{ display: "inline-block", width, height: 8, background: "var(--panel-hover)", position: "relative", verticalAlign: "middle" }}>
      <span style={{ position: "absolute", top: 0, bottom: 0, background: x >= 0 ? "var(--bull)" : "var(--bear)", width: `${Math.abs(x) * 50}%`, [x >= 0 ? "left" : "right"]: "50%" } as React.CSSProperties} />
    </span>
  );
}
