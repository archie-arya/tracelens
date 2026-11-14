import { clsx } from "clsx";
import { AlertTriangle, ArrowLeft, ChevronDown, ChevronRight, Clock, Coins, FlaskConical, Hash, Play, User } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, useApi } from "../api";
import { DataView } from "../components/DataView";
import { Badge, CopyButton, ErrorBox, Loading, ScoreChip, Select, Spinner, StatusDot, typeStyle, TypeBadge } from "../components/ui";
import { fmtCost, fmtMs, fmtNum, fmtTime } from "../format";
import { useApp } from "../state";

type Obs = any & { children: Obs[]; depth: number };

function buildTree(obs: any[]): Obs[] {
  const map = new Map<string, Obs>();
  obs.forEach((o) => map.set(o.id, { ...o, children: [], depth: 0 }));
  const roots: Obs[] = [];
  map.forEach((o) => {
    const p = o.parent_id && map.get(o.parent_id);
    if (p) p.children.push(o);
    else roots.push(o);
  });
  const sort = (arr: Obs[], d: number) => {
    arr.sort((a, b) => (a.start_time || 0) - (b.start_time || 0));
    arr.forEach((n) => {
      n.depth = d;
      sort(n.children, d + 1);
    });
  };
  sort(roots, 0);
  return roots;
}

function flatten(nodes: Obs[], collapsed: Set<string>, out: Obs[] = []): Obs[] {
  nodes.forEach((n) => {
    out.push(n);
    if (!collapsed.has(n.id)) flatten(n.children, collapsed, out);
  });
  return out;
}

export default function TraceDetail() {
  const { id } = useParams();
  const { readOnly } = useApp();
  const { data: t, error, reload } = useApi<any>(`/api/v1/traces/${id}`);
  const [sel, setSel] = useState<string>("trace");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const tree = useMemo(() => (t ? buildTree(t.observations) : []), [t]);
  if (error) return <ErrorBox error={error} />;
  if (!t) return <Loading />;
  const rows = flatten(tree, collapsed);
  const t0 = Math.min(t.start_time || Infinity, ...t.observations.map((o: any) => o.start_time || Infinity));
  const t1 = Math.max(t.end_time || 0, ...t.observations.map((o: any) => o.end_time || o.start_time || 0));
  const total = Math.max(0.001, t1 - t0);
  const selected = sel === "trace" ? null : t.observations.find((o: any) => o.id === sel);

  return (
    <div>
      <Link to="/traces" className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-300">
        <ArrowLeft className="h-4 w-4" /> Traces
      </Link>
      <div className="card mb-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <StatusDot status={t.status} />
              <h1 className="text-lg font-semibold">{t.name || "(unnamed trace)"}</h1>
              {t.status === "error" && <Badge className="border-rose-500/40 bg-rose-500/10 text-rose-300">error</Badge>}
            </div>
            <div className="mt-1 flex items-center gap-1 font-mono text-xs text-zinc-500">
              {t.id} <CopyButton text={t.id} />
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(t.tags || []).map((g: string) => <Badge key={g}>{g}</Badge>)}
            {t.environment && <Badge className="text-sky-300">env: {t.environment}</Badge>}
            {t.release && <Badge className="text-sky-300">release: {t.release}</Badge>}
            {t.sdk && <Badge className="text-zinc-400">{t.sdk}</Badge>}
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-6">
          <Meta icon={<Clock className="h-3.5 w-3.5" />} label="Started" value={fmtTime(t.start_time)} />
          <Meta icon={<Clock className="h-3.5 w-3.5" />} label="Latency" value={fmtMs(t.latency_ms)} />
          <Meta icon={<Coins className="h-3.5 w-3.5" />} label="Cost" value={fmtCost(t.total_cost)} />
          <Meta icon={<Hash className="h-3.5 w-3.5" />} label="Tokens" value={`${fmtNum(t.prompt_tokens)} → ${fmtNum(t.completion_tokens)}`} />
          <Meta icon={<User className="h-3.5 w-3.5" />} label="User" value={t.user_id ? <Link className="text-indigo-300 hover:underline" to={`/traces?user_id=${encodeURIComponent(t.user_id)}`}>{t.user_id}</Link> : "–"} />
          <Meta icon={<User className="h-3.5 w-3.5" />} label="Session" value={t.session_id ? <Link className="text-indigo-300 hover:underline" to={`/traces?session_id=${encodeURIComponent(t.session_id)}`}>{t.session_id}</Link> : "–"} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5 text-sm font-semibold">
            <span>Execution · {t.observations.length} observations</span>
            <span className="text-xs font-normal text-zinc-500">{fmtMs(total * 1000)}</span>
          </div>
          <div className="max-h-[70vh] overflow-auto py-1">
            <button onClick={() => setSel("trace")} className={clsx("flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm", sel === "trace" ? "bg-indigo-500/10" : "hover:bg-zinc-800/50")}>
              <span className="font-medium">Trace</span>
              <span className="text-xs text-zinc-500">input / output / scores</span>
            </button>
            {rows.map((o) => {
              const left = ((o.start_time || t0) - t0) / total;
              const width = Math.max(0.004, ((o.end_time || o.start_time || t0) - (o.start_time || t0)) / total);
              const st = typeStyle(o.type);
              return (
                <div key={o.id} onClick={() => setSel(o.id)} className={clsx("grid cursor-pointer grid-cols-[minmax(0,1fr)_38%] items-center gap-2 px-3 py-1.5", sel === o.id ? "bg-indigo-500/10" : "hover:bg-zinc-800/50")}>
                  <div className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: o.depth * 14 }}>
                    {o.children.length ? (
                      <button
                        className="text-zinc-500 hover:text-zinc-200"
                        onClick={(e) => {
                          e.stopPropagation();
                          const n = new Set(collapsed);
                          n.has(o.id) ? n.delete(o.id) : n.add(o.id);
                          setCollapsed(n);
                        }}
                      >
                        {collapsed.has(o.id) ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </button>
                    ) : (
                      <span className="w-3.5" />
                    )}
                    <TypeBadge type={o.type} />
                    <span className={clsx("truncate text-sm", o.level === "ERROR" && "text-rose-300")}>{o.name}</span>
                    {o.level === "ERROR" && <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-400" />}
                    {o.model && <span className="hidden truncate font-mono text-[11px] text-zinc-500 2xl:inline">{o.model}</span>}
                  </div>
                  <div className="relative h-5">
                    <div className="absolute inset-y-[7px] left-0 right-0 rounded bg-zinc-800/60" />
                    <div className={clsx("absolute inset-y-1 rounded", o.level === "ERROR" ? "bg-rose-500" : st.bar)} style={{ left: `${left * 100}%`, width: `${width * 100}%`, minWidth: 3 }} />
                    <span className="absolute right-0 top-0 text-[10px] leading-5 text-zinc-400">{fmtMs(o.latency_ms)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-4">
          {selected ? <ObservationPanel o={selected} /> : <TracePanel t={t} readOnly={readOnly} onScored={reload} />}
        </div>
      </div>
    </div>
  );
}

function Meta({ icon, label, value }: { icon: any; label: string; value: any }) {
  return (
    <div>
      <div className="flex items-center gap-1 text-xs text-zinc-500">
        {icon} {label}
      </div>
      <div className="mt-0.5 truncate font-medium">{value}</div>
    </div>
  );
}

function ObservationPanel({ o }: { o: any }) {
  const ttft = o.completion_start_time && o.start_time ? (o.completion_start_time - o.start_time) * 1000 : null;
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <TypeBadge type={o.type} />
        <span className="font-semibold">{o.name}</span>
        {o.model && <Badge className="font-mono text-violet-300">{o.model}</Badge>}
        {o.metadata?.provider && <Badge>{o.metadata.provider}</Badge>}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
        <Meta icon={<Clock className="h-3.5 w-3.5" />} label="Latency" value={fmtMs(o.latency_ms)} />
        <Meta icon={<Clock className="h-3.5 w-3.5" />} label="TTFT" value={fmtMs(ttft)} />
        <Meta icon={<Hash className="h-3.5 w-3.5" />} label="Input tok" value={fmtNum(o.prompt_tokens)} />
        <Meta icon={<Hash className="h-3.5 w-3.5" />} label="Output tok" value={fmtNum(o.completion_tokens)} />
        <Meta icon={<Coins className="h-3.5 w-3.5" />} label="Cost" value={fmtCost(o.cost)} />
      </div>
      {o.level === "ERROR" && (
        <div className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">
          <div className="font-semibold">Error</div>
          <div className="mt-1 whitespace-pre-wrap font-mono text-xs">{o.status_message}</div>
          {o.metadata?.exception?.traceback && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-rose-300">traceback</summary>
              <pre className="mt-1 max-h-64 overflow-auto text-[11px]">{o.metadata.exception.traceback}</pre>
            </details>
          )}
        </div>
      )}
      <div className="mt-4 space-y-4">
        {o.model_parameters && Object.keys(o.model_parameters).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(o.model_parameters).map(([k, v]) => (
              <Badge key={k} className="font-mono">
                {k}={typeof v === "object" ? JSON.stringify(v) : String(v)}
              </Badge>
            ))}
          </div>
        )}
        <DataView title="Input" value={o.input} />
        <DataView title="Output" value={o.output} />
        {o.metadata && Object.keys(o.metadata).filter((k) => k !== "exception").length > 0 && (
          <DataView title="Metadata" value={Object.fromEntries(Object.entries(o.metadata).filter(([k]) => k !== "exception"))} />
        )}
      </div>
    </div>
  );
}

function TracePanel({ t, readOnly, onScored }: { t: any; readOnly: boolean; onScored: () => void }) {
  const { data: evs } = useApi<any>("/api/v1/evaluators");
  const [ev, setEv] = useState("llm_relevance");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/v1/traces/${t.id}/evaluate`, { method: "POST", body: { evaluator: ev } });
      onScored();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <FlaskConical className="h-4 w-4 text-indigo-300" /> Scores
          </div>
          <div className="flex items-center gap-2">
            <Select value={ev} onChange={setEv} options={(evs?.data || []).map((e: any) => ({ value: e.name, label: `${e.name}${e.kind === "llm" ? " (LLM judge)" : ""}` }))} />
            <button className="btn btn-primary" disabled={readOnly || busy} onClick={run} title={readOnly ? "Read-only demo - use your own project" : "Run evaluator on this trace"}>
              {busy ? <Spinner className="text-white" /> : <Play className="h-3.5 w-3.5" />} Evaluate
            </button>
          </div>
        </div>
        {err && <ErrorBox error={err} />}
        {t.scores.length === 0 ? (
          <div className="text-sm text-zinc-500">No scores yet.</div>
        ) : (
          <div className="space-y-2">
            {t.scores.map((s: any) => (
              <div key={s.id} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <ScoreChip name={s.name} value={s.value} label={s.string_value} />
                  {s.string_value && <span className="text-xs text-zinc-400">{s.string_value}</span>}
                  <span className="ml-auto text-[11px] text-zinc-500">
                    {s.source}
                    {s.evaluator ? ` · ${s.evaluator}` : ""}
                    {s.metadata?.judge_model ? ` · ${s.metadata.judge_model}` : ""}
                  </span>
                </div>
                {s.comment && <div className="mt-1.5 text-sm text-zinc-300">{s.comment}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="card space-y-4 p-4">
        <DataView title="Input" value={t.input} />
        <DataView title="Output" value={t.output} />
        {t.metadata && Object.keys(t.metadata).length > 0 && <DataView title="Metadata" value={t.metadata} />}
      </div>
    </>
  );
}
