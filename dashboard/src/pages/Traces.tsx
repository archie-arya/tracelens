import { ChevronLeft, ChevronRight, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { isStatic, qs, useApi } from "../api";
import { Badge, Empty, ErrorBox, Loading, PageHeader, ScoreChip, Select, StatusDot } from "../components/ui";
import { fmtAgo, fmtCost, fmtMs, fmtNum, fmtTime, preview } from "../format";
import { useApp } from "../state";

const RANGE_S: Record<string, number> = { "1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400 };
const PAGE = 50;

export default function Traces() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const { range, project } = useApp();
  const [q, setQ] = useState(sp.get("q") || "");
  const page = Number(sp.get("page") || 0);
  const f = Object.fromEntries(["name", "model", "tag", "status", "user_id", "session_id", "order_by"].map((k) => [k, sp.get(k) || ""]));
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    n.delete("page");
    setSp(n);
  };
  useEffect(() => setQ(sp.get("q") || ""), [sp]);
  const start = isStatic() ? undefined : Math.floor(Date.now() / 1000 - (RANGE_S[range] || RANGE_S["7d"]));
  const { data, error, loading, reload } = useApi<any>(`/api/v1/traces${qs({ ...f, q: sp.get("q"), limit: PAGE, offset: page * PAGE, start })}`, [project?.id]);
  const { data: opts } = useApi<any>("/api/v1/filters", [project?.id]);
  const active = Object.entries(f).filter(([k, v]) => v && k !== "order_by");

  return (
    <div>
      <PageHeader
        title="Traces"
        subtitle={data ? `${fmtNum(data.total)} traces · last ${range}` : " "}
        actions={
          <button className="btn btn-ghost" onClick={reload}>
            <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} /> Refresh
          </button>
        }
      />
      <div className="card mb-3 flex flex-wrap items-center gap-2 p-3">
        <form
          className="relative min-w-[240px] flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            set("q", q);
          }}
        >
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-zinc-500" />
          <input className="input w-full pl-8" placeholder="Search name, input, output, user, trace id…  (Enter)" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <Select value={f.name} onChange={(v) => set("name", v)} options={opts?.names || []} placeholder="All workflows" />
        <Select value={f.model} onChange={(v) => set("model", v)} options={opts?.models || []} placeholder="All models" />
        <Select value={f.tag} onChange={(v) => set("tag", v)} options={opts?.tags || []} placeholder="All tags" />
        <Select value={f.status} onChange={(v) => set("status", v)} options={[{ value: "ok", label: "OK" }, { value: "error", label: "Error" }]} placeholder="Any status" />
        <Select value={f.order_by} onChange={(v) => set("order_by", v)} options={[{ value: "latency", label: "Slowest" }, { value: "cost", label: "Most expensive" }, { value: "tokens", label: "Most tokens" }]} placeholder="Newest" />
      </div>
      {active.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {active.map(([k, v]) => (
            <button key={k} onClick={() => set(k, "")} className="inline-flex items-center gap-1 rounded-full border border-indigo-500/40 bg-indigo-500/10 px-2.5 py-0.5 text-xs text-indigo-200">
              {k}: {v} <X className="h-3 w-3" />
            </button>
          ))}
        </div>
      )}
      {error && <ErrorBox error={error} />}
      {!data ? (
        <Loading />
      ) : data.data.length === 0 ? (
        <div className="card">
          <Empty title="No traces match">Try a wider time range or clear filters. New here? Grab an API key in Settings and send your first trace.</Empty>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                {["", "Time", "Name", "Input → Output", "User", "Latency", "Tokens", "Cost", "Scores"].map((h, i) => (
                  <th key={i} className="th">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.data.map((t: any) => (
                <tr key={t.id} onClick={() => nav(`/traces/${t.id}`)} className="cursor-pointer border-b border-zinc-800/60 transition last:border-0 hover:bg-zinc-800/40">
                  <td className="td w-4"><StatusDot status={t.status} /></td>
                  <td className="td whitespace-nowrap text-zinc-400" title={fmtTime(t.start_time)}>{fmtAgo(t.start_time)}</td>
                  <td className="td">
                    <div className="font-medium">{t.name || "(unnamed)"}</div>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {(t.tags || []).slice(0, 3).map((g: string) => <Badge key={g}>{g}</Badge>)}
                      {(t.models || []).slice(0, 2).map((m: string) => <Badge key={m} className="font-mono text-violet-300">{m}</Badge>)}
                    </div>
                  </td>
                  <td className="td max-w-[420px]">
                    <div className="truncate text-zinc-300">{preview(t.input, 90)}</div>
                    <div className="truncate text-zinc-500">→ {preview(t.output, 90) || "–"}</div>
                  </td>
                  <td className="td whitespace-nowrap text-zinc-400">{t.user_id || "–"}</td>
                  <td className="td whitespace-nowrap">{fmtMs(t.latency_ms)}</td>
                  <td className="td whitespace-nowrap text-zinc-400">{fmtNum(t.total_tokens)}</td>
                  <td className="td whitespace-nowrap text-zinc-400">{fmtCost(t.total_cost)}</td>
                  <td className="td">
                    <div className="flex flex-wrap gap-1">{(t.scores || []).slice(0, 3).map((s: any, i: number) => <ScoreChip key={i} name={s.name} value={s.value} label={s.string_value} />)}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between border-t border-zinc-800 px-3 py-2 text-xs text-zinc-500">
            <span>
              {page * PAGE + 1}–{Math.min((page + 1) * PAGE, data.total)} of {fmtNum(data.total)}
            </span>
            <div className="flex gap-1">
              <button className="btn btn-ghost px-2" disabled={page === 0} onClick={() => setSp(new URLSearchParams({ ...Object.fromEntries(sp), page: String(page - 1) }))}>
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button className="btn btn-ghost px-2" disabled={(page + 1) * PAGE >= data.total} onClick={() => setSp(new URLSearchParams({ ...Object.fromEntries(sp), page: String(page + 1) }))}>
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
