// API client. Talks to a TraceLens server, or - in the static GitHub Pages build - to a bundled
// read-only snapshot of the demo project (VITE_STATIC_DEMO=1) until a server is connected in Settings.
import { useCallback, useEffect, useState } from "react";

const LS_SERVER = "tl.server";
const LS_KEY = "tl.key";

export const STATIC_BUILD = import.meta.env.VITE_STATIC_DEMO === "1";

export function getServer(): string {
  return (localStorage.getItem(LS_SERVER) || import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
}
export function setServer(url: string | null) {
  if (url) localStorage.setItem(LS_SERVER, url.replace(/\/$/, ""));
  else localStorage.removeItem(LS_SERVER);
}
export function getKey(): string | null {
  return localStorage.getItem(LS_KEY);
}
export function setKey(key: string | null) {
  if (key) localStorage.setItem(LS_KEY, key);
  else localStorage.removeItem(LS_KEY);
  window.dispatchEvent(new Event("tl-auth"));
}
export function isStatic(): boolean {
  return STATIC_BUILD && !localStorage.getItem(LS_SERVER);
}
/** Public URL that SDKs should send traces to. */
export function ingestHost(): string {
  const s = getServer();
  if (s) return s;
  if (isStatic()) return "http://localhost:8000";
  return window.location.origin;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// ---------------------------------------------------------------- static snapshot adapter
const cache = new Map<string, Promise<any>>();
function loadStatic(file: string): Promise<any> {
  if (!cache.has(file)) {
    const url = `${import.meta.env.BASE_URL}demo-data/${file}`;
    cache.set(file, fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new ApiError(r.status, "not in demo snapshot")))));
  }
  return cache.get(file)!;
}

async function staticGet(path: string): Promise<any> {
  const u = new URL(path, "http://x");
  const p = u.pathname.replace(/^\/api\/v1\//, "");
  const q = u.searchParams;
  if (p === "traces") {
    const all: any[] = (await loadStatic("traces.json")).data;
    let rows = all.filter((t) => {
      const s = q.get("q")?.toLowerCase();
      if (s && !JSON.stringify([t.name, t.id, t.user_id, t.input, t.output]).toLowerCase().includes(s)) return false;
      if (q.get("name") && t.name !== q.get("name")) return false;
      if (q.get("user_id") && t.user_id !== q.get("user_id")) return false;
      if (q.get("session_id") && t.session_id !== q.get("session_id")) return false;
      if (q.get("tag") && !(t.tags || []).includes(q.get("tag"))) return false;
      if (q.get("model") && !(t.models || []).includes(q.get("model"))) return false;
      if (q.get("status") && t.status !== q.get("status")) return false;
      return true;
    });
    const ob = q.get("order_by") || "start_time";
    const key = ({ cost: "total_cost", latency: "latency_ms", tokens: "total_tokens" } as any)[ob] || ob;
    rows = [...rows].sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0));
    if (q.get("order") === "asc") rows.reverse();
    const limit = Number(q.get("limit") || 50), offset = Number(q.get("offset") || 0);
    return { data: rows.slice(offset, offset + limit), total: rows.length, limit, offset };
  }
  if (p.startsWith("traces/")) return loadStatic(`traces/${p.slice(7)}.json`);
  if (p === "metrics") {
    const r = q.get("range") || "7d";
    return loadStatic(`metrics-${["24h", "7d", "30d"].includes(r) ? r : "30d"}.json`);
  }
  if (p === "scores") {
    const all = await loadStatic("scores.json");
    const name = q.get("name");
    const rows = name ? all.data.filter((s: any) => s.name === name) : all.data;
    return { data: rows.slice(0, Number(q.get("limit") || 100)), total: rows.length };
  }
  const m = p.match(/^datasets\/([^/]+)(\/runs)?$/);
  if (m) return loadStatic(`datasets/${m[1]}${m[2] ? "-runs" : ""}.json`);
  const simple: Record<string, string> = {
    project: "project.json", sessions: "sessions.json", filters: "filters.json", evaluators: "evaluators.json", "eval-rules": "eval-rules.json",
    datasets: "datasets.json", "alerts/rules": "alert-rules.json", "alerts/events": "alert-events.json", "../health": "health.json",
  };
  if (simple[p]) return loadStatic(simple[p]);
  if (u.pathname === "/api/health") return loadStatic("health.json");
  throw new ApiError(404, "not available in static demo");
}

// ---------------------------------------------------------------- public API
export async function api<T = any>(path: string, opts: { method?: string; body?: any; key?: string | null } = {}): Promise<T> {
  const method = opts.method || "GET";
  if (isStatic()) {
    if (method !== "GET") throw new ApiError(403, "This is a static read-only demo. Connect your own TraceLens server in Settings to make changes.");
    return staticGet(path);
  }
  const headers: Record<string, string> = {};
  const key = opts.key !== undefined ? opts.key : getKey();
  if (key) headers["Authorization"] = `Bearer ${key}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(getServer() + path, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new ApiError(r.status, (data && (data.detail?.toString?.() || data.detail)) || r.statusText);
  return data as T;
}

export function useApi<T = any>(path: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    api<T>(path)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e.message || String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, loading, reload, setData };
}

export function qs(params: Record<string, any>): string {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const str = s.toString();
  return str ? `?${str}` : "";
}
