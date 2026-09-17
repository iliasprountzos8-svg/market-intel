export function HitRateRing({ rate, size = 72 }: { rate: number | null; size?: number }) {
  const r = size * 0.39;
  const c = 2 * Math.PI * r;
  const pct = rate ?? 0;
  const offset = c - (pct / 100) * c;
  const color = rate === null ? "var(--text-dim)" : rate >= 50 ? "var(--bull)" : "var(--bear)";
  const center = size / 2;
  const fontSize = size * 0.21;
  return (
    <svg className="track-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={center} cy={center} r={r} fill="none" stroke="var(--border)" strokeWidth={size * 0.08} />
      <circle
        cx={center} cy={center} r={r} fill="none" stroke={color} strokeWidth={size * 0.08}
        strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
        transform={`rotate(-90 ${center} ${center})`}
        style={{ transition: "stroke-dashoffset 400ms ease" }}
      />
      <text x={center} y={center + fontSize * 0.35} textAnchor="middle" fontSize={fontSize} fontWeight="700" fill="var(--text)" fontFamily="var(--font-mono)">
        {rate === null ? "—" : `${rate.toFixed(0)}%`}
      </text>
    </svg>
  );
}
