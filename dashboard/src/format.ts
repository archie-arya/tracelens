export function fmtCost(v?: number | null): string {
  if (v === null || v === undefined) return "–";
  if (v === 0) return "$0";
  if (v < 0.0001) return `$${v.toExponential(1)}`;
  if (v < 0.01) return `$${v.toFixed(5)}`;
  if (v < 1) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}
export function fmtMs(v?: number | null): string {
  if (v === null || v === undefined) return "–";
  if (v < 1000) return `${Math.round(v)} ms`;
  if (v < 60000) return `${(v / 1000).toFixed(2)} s`;
  return `${(v / 60000).toFixed(1)} min`;
}
export function fmtNum(v?: number | null): string {
  if (v === null || v === undefined) return "–";
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
  return v.toLocaleString();
}
export function fmtPct(v?: number | null): string {
  if (v === null || v === undefined) return "–";
  return `${(v * 100).toFixed(1)}%`;
}
export function fmtTime(t?: number | null, withDate = true): string {
  if (!t) return "–";
  const d = new Date(t * 1000);
  return withDate ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : d.toLocaleTimeString();
}
export function fmtAgo(t?: number | null): string {
  if (!t) return "–";
  const s = Date.now() / 1000 - t;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
export function preview(v: any, n = 120): string {
  if (v === null || v === undefined) return "";
  let s: string;
  if (typeof v === "string") s = v;
  else if (Array.isArray(v) && v.length && v[v.length - 1]?.content !== undefined) s = String(v[v.length - 1].content);
  else if (typeof v === "object" && Object.keys(v).length === 1) s = typeof Object.values(v)[0] === "string" ? (Object.values(v)[0] as string) : JSON.stringify(v);
  else s = JSON.stringify(v);
  return s.length > n ? s.slice(0, n) + "…" : s;
}
export function scoreColor(v: number | null | undefined, higherIsBetter = true): string {
  if (v === null || v === undefined) return "bg-zinc-800 text-zinc-400";
  const g = higherIsBetter ? v : 1 - v;
  if (g >= 0.8) return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
  if (g >= 0.5) return "bg-amber-500/15 text-amber-300 border-amber-500/30";
  return "bg-rose-500/15 text-rose-300 border-rose-500/30";
}
export const LOWER_IS_BETTER = new Set(["toxicity"]);
