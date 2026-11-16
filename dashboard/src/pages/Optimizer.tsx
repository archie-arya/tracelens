import { clsx } from "clsx";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronRight, CircleDollarSign, Play, Scale, Square, Trash2, Wrench, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Label, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api, useApi } from "../api";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Select, Spinner, Stat } from "../components/ui";
import { fmtAgo, fmtCost, fmtMs } from "../format";
import { useApp } from "../state";

const pct = (v?: number | null, digits = 0) => (v === null || v === undefined ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
const qual = (v?: number | null) => (v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(0)}%`);

const STATUS: Record<string, string> = {
  completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  running: "border-indigo-500/30 bg-indigo-500/10 text-indigo-300",
  queued: "border-zinc-600 bg-zinc-800 text-zinc-300",
  failed: "border-red-500/30 bg-red-500/10 text-red-300",
  cancelled: "border-zinc-600 bg-zinc-800 text-zinc-400",
  interrupted: "border-amber-500/30 bg-amber-500/10 text-amber-300",
};

function StatusBadge({ s }: { s: string }) {
  return <span className={clsx("rounded-full border px-2 py-0.5 text-[11px] font-medium", STATUS[s] || STATUS.queued)}>{s}</span>;
}

function EstimateNote({ text }: { text?: string }) {
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs text-amber-200/90">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        <b>Estimates, not guarantees.</b>{" "}
        {text || "Costs use list prices from the pricing table; quality is judged relative to the original outputs on a sample of real prompts."} Only LLM
        generations are replayed. Tool-calling steps are replayed one call at a time with the recorded tool results; tools are never executed, and prompts
        are redacted first when redaction is enabled.
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ list + new run
export default function Optimizer() {
  const { project, readOnly } = useApp();
  const runs = useApi<any>("/api/v1/optimizer/runs", [project?.id]);
  const steps = useApi<any>("/api/v1/optimizer/steps", [project?.id]);
  const [showForm, setShowForm] = useState(false);
  const [preset, setPreset] = useState<string | null>(null);
  useEffect(() => {
    if (!runs.data?.data?.some((r: any) => r.status === "running" || r.status === "queued")) return;
    const t = setInterval(runs.reload, 3000);
    return () => clearInterval(t);
  }, [runs.data, runs.reload]);
  if (runs.error) return <ErrorBox error={runs.error} />;
  if (!runs.data) return <Loading />;
  return (
    <div>
      <PageHeader
        title="Cost Optimizer"
        subtitle="Replay real LLM calls on cheaper models, score them against the originals, and get switch recommendations per step."
        actions={
          !readOnly && (
            <button className="btn btn-primary" onClick={() => setShowForm(!showForm)}>
              <Play className="h-4 w-4" /> New run
            </button>
          )
        }
      />
      <EstimateNote />
      {showForm && <NewRun steps={steps.data?.data || []} preset={preset} onDone={() => setShowForm(false)} />}
      <div className="grid gap-4 xl:grid-cols-[1fr_1.1fr]">
        <Card title="Runs">
          {runs.data.data.length === 0 ? (
            <Empty title="No optimizer runs yet">
              <code className="text-xs">tracelens optimize --step "my-app / llm" --candidates gemini:gemma-4-26b-a4b-it,ollama:llama3.1</code>
            </Empty>
          ) : (
            <div className="divide-y divide-zinc-800">
              {runs.data.data.map((r: any) => (
                <Link key={r.id} to={`/optimizer/${r.id}`} className="block py-2.5 hover:bg-zinc-900/60">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-zinc-100">{r.name}</span>
                    <StatusBadge s={r.status} />
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-zinc-500">
                    <span>{(r.config?.steps || []).length} step(s)</span>
                    <span>{(r.config?.candidates || []).join(", ")}</span>
                    <span>
                      {r.progress?.done ?? 0}/{r.progress?.total ?? 0} replays
                    </span>
                    <span>spent {fmtCost(r.progress?.spend_usd)}</span>
                    <span>{fmtAgo(r.created_at)}</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>
        <Card title="LLM steps by spend (last 30 days)">
          {!steps.data ? (
            <Loading />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Step</th>
                  <th className="th">Calls</th>
                  <th className="th">Replayable</th>
                  <th className="th">Est. / month</th>
                  {!readOnly && <th className="th" />}
                </tr>
              </thead>
              <tbody>
                {steps.data.data.slice(0, 12).map((s: any) => (
                  <tr key={s.step} className="border-t border-zinc-800/70">
                    <td className="td max-w-[340px]">
                      <div className="truncate font-mono text-xs" title={s.step}>{s.step}</div>
                      <div className="truncate font-mono text-[10px] text-zinc-500">{Object.keys(s.models).join(", ")}</div>
                    </td>
                    <td className="td">{s.calls}</td>
                    <td className="td" title={Object.entries(s.skipped || {}).map(([k, v]) => `${v}× ${k}`).join("\n")}>
                      {Math.max(s.replayable, s.replayable_with_tools ?? 0)}
                      {Math.max(s.replayable, s.replayable_with_tools ?? 0) < s.calls && <span className="text-zinc-600"> / {s.calls}</span>}
                      {(s.tool_calls ?? 0) > 0 && (
                        <span className="ml-1.5 inline-flex items-center gap-0.5 rounded border border-sky-500/30 px-1 text-[10px] text-sky-300" title={`${s.tool_calls} tool-calling LLM calls (replayed with allow_tool_steps)`}>
                          <Wrench className="h-2.5 w-2.5" /> tools
                        </span>
                      )}
                    </td>
                    <td className="td whitespace-nowrap">{fmtCost(s.monthly_cost_est)}</td>
                    {!readOnly && (
                      <td className="td">
                        {Math.max(s.replayable, s.replayable_with_tools ?? 0) > 0 && (
                          <button className="text-xs text-indigo-300 hover:underline" onClick={() => (setPreset(s.step), setShowForm(true))}>
                            optimize
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}

function NewRun({ steps, preset, onDone }: { steps: any[]; preset: string | null; onDone: () => void }) {
  const nav = useNavigate();
  const plugins = useApi<any>("/api/v1/optimizer/plugins");
  const [sel, setSel] = useState<string[]>(preset ? [preset] : []);
  const [cands, setCands] = useState("gemini:gemma-4-26b-a4b-it");
  const [size, setSize] = useState(10);
  const [scorer, setScorer] = useState("auto");
  const [policy, setPolicy] = useState("quality_threshold");
  const [spend, setSpend] = useState(0.5);
  const [redact, setRedact] = useState(false);
  const [tools, setTools] = useState(() => !!(preset && steps.find((s) => s.step === preset && (s.tool_calls ?? 0) > 0)));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (preset) setSel((s) => (s.includes(preset) ? s : [...s, preset]));
  }, [preset]);
  const suggestions = ["gemini:gemma-4-26b-a4b-it", "gemini:gemini-flash-lite-latest", "groq:llama-3.1-8b-instant", "ollama:llama3.1", "openai:gpt-4o-mini"];
  const providers: any[] = plugins.data?.providers || [];
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const run = await api<any>("/api/v1/optimizer/runs", {
        method: "POST",
        body: { steps: sel, candidates: cands.split(",").map((c) => c.trim()).filter(Boolean), sample_size: size, scorer, policy, max_spend_usd: spend, redact, allow_tool_steps: tools },
      });
      onDone();
      nav(`/optimizer/${run.id}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="New optimization run" className="mb-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <div className="label mb-1.5">Steps to optimize</div>
          <div className="max-h-56 space-y-1 overflow-auto rounded-lg border border-zinc-800 p-2">
            {steps.filter((s) => (tools ? s.replayable_with_tools ?? s.replayable : s.replayable) > 0).map((s) => (
              <label key={s.step} className="flex cursor-pointer items-center gap-2 text-xs">
                <input type="checkbox" checked={sel.includes(s.step)} onChange={(e) => setSel(e.target.checked ? [...sel, s.step] : sel.filter((x) => x !== s.step))} />
                <span className="font-mono">{s.step}</span>
                <span className="text-zinc-600">
                  {tools ? s.replayable_with_tools ?? s.replayable : s.replayable} calls · {Object.keys(s.models)[0]}
                  {(s.tool_calls ?? 0) > 0 && " · tools"}
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <div className="label mb-1.5">Candidate models (provider:model, comma-separated)</div>
            <input className="input w-full font-mono" value={cands} onChange={(e) => setCands(e.target.value)} />
            <div className="mt-1.5 flex flex-wrap gap-1">
              {suggestions.map((s) => {
                const p = providers.find((x) => x.name === s.split(":")[0]);
                return (
                  <button
                    key={s}
                    className={clsx("rounded border px-1.5 py-0.5 font-mono text-[10px]", p?.available === false ? "border-zinc-800 text-zinc-600" : "border-zinc-700 text-zinc-300 hover:border-indigo-500")}
                    title={p?.available === false ? `${p.name} has no credentials on this server` : p?.description}
                    onClick={() => setCands(cands ? `${cands},${s}` : s)}
                  >
                    + {s}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <label className="text-xs">
              <div className="label mb-1">Samples / step</div>
              <input type="number" className="input w-full" value={size} min={1} max={500} onChange={(e) => setSize(Number(e.target.value))} />
            </label>
            <label className="text-xs">
              <div className="label mb-1">Max spend $</div>
              <input type="number" step="0.1" className="input w-full" value={spend} min={0} onChange={(e) => setSpend(Number(e.target.value))} />
            </label>
            <div className="text-xs">
              <div className="label mb-1">Scorer</div>
              <Select value={scorer} onChange={setScorer} options={(plugins.data?.scorers || []).map((s: any) => s.name).filter((n: string) => !n.includes("<"))} className="w-full" />
            </div>
            <div className="text-xs">
              <div className="label mb-1">Policy</div>
              <Select value={policy} onChange={setPolicy} options={(plugins.data?.policies || []).map((s: any) => s.name)} className="w-full" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={redact} onChange={(e) => setRedact(e.target.checked)} /> Redact PII (emails, keys, cards, phones) in prompts before replaying
          </label>
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={tools} onChange={(e) => setTools(e.target.checked)} /> Replay tool-calling steps (agents): one call per step with the
            recorded tool results, scored on the tool calls. Tools are never executed.
          </label>
          {err && <ErrorBox error={err} />}
          <button className="btn btn-primary" disabled={busy || !sel.length || !cands.trim()} onClick={submit}>
            {busy ? <Spinner /> : <Play className="h-4 w-4" />} Start run
          </button>
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ run detail
export function OptimizerRun() {
  const { id } = useParams();
  const { readOnly } = useApp();
  const { data: run, error, reload } = useApi<any>(`/api/v1/optimizer/runs/${id}`);
  const nav = useNavigate();
  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
  }, [run, reload]);
  if (error) return <ErrorBox error={error} />;
  if (!run) return <Loading />;
  const p = run.progress || {};
  const sum = run.results?.summary;
  return (
    <div>
      <Link to="/optimizer" className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300">
        <ArrowLeft className="h-3.5 w-3.5" /> Cost Optimizer
      </Link>
      <PageHeader
        title={run.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge s={run.status} /> {fmtAgo(run.created_at)} · scorer <b>{run.config.scorer}</b> · policy <b>{run.config.policy}</b> · sampler{" "}
            <b>{run.config.sampler}</b> · cap {fmtCost(run.config.max_spend_usd)}
          </span>
        }
        actions={
          !readOnly && (
            <>
              {["running", "queued"].includes(run.status) && (
                <button className="btn btn-ghost" onClick={() => api(`/api/v1/optimizer/runs/${id}/cancel`, { method: "POST" }).then(reload)}>
                  <Square className="h-4 w-4" /> Cancel
                </button>
              )}
              <button className="btn btn-ghost" onClick={() => api(`/api/v1/optimizer/runs/${id}`, { method: "DELETE" }).then(() => nav("/optimizer"))}>
                <Trash2 className="h-4 w-4" /> Delete
              </button>
            </>
          )
        }
      />
      {["running", "queued"].includes(run.status) && (
        <div className="card mb-4 p-4">
          <div className="mb-2 flex justify-between text-xs text-zinc-400">
            <span>
              Replaying… {p.done ?? 0}/{p.total ?? 0}
            </span>
            <span>
              spent {fmtCost(p.spend_usd)} · {p.cache_hits ?? 0} cached · {p.errors ?? 0} errors
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-zinc-800">
            <div className="h-full bg-indigo-500 transition-all" style={{ width: `${p.total ? (100 * p.done) / p.total : 3}%` }} />
          </div>
        </div>
      )}
      {run.error && <ErrorBox error={run.error} />}
      <EstimateNote text={sum?.disclaimer} />
      {sum && (
        <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Steps analysed" value={sum.steps} icon={<Scale className="h-4 w-4" />} />
          <Stat label="Recommended switches" value={sum.switches} tone={sum.switches ? "text-emerald-300" : undefined} icon={<CheckCircle2 className="h-4 w-4" />} />
          <Stat label="Est. monthly savings" value={fmtCost(sum.monthly_savings_usd)} sub="at current volume" icon={<CircleDollarSign className="h-4 w-4" />} />
          <Stat label="Replays" value={sum.replays} sub={`${sum.cache_hits} from cache · ${sum.errors} errors`} />
          <Stat label="Replay spend" value={fmtCost(sum.spend_usd)} sub={sum.redacted_inputs ? "prompts redacted" : "list price"} />
        </div>
      )}
      <div className="space-y-5">
        {(run.results?.steps || []).map((s: any) => (
          <StepResult key={s.step} s={s} />
        ))}
        {(run.results?.trajectories || []).length > 0 && <Trajectories items={run.results.trajectories} candidates={run.config.candidates || []} />}
      </div>
    </div>
  );
}

const agreeTone = (v?: number | null) => (v === null || v === undefined ? "text-zinc-500" : v >= 0.95 ? "text-emerald-300" : v >= 0.8 ? "text-amber-300" : "text-red-300");

function Calls({ calls, text, tone }: { calls: string[]; text?: string | null; tone?: string }) {
  if (!calls?.length)
    return <span className="text-[11px] italic text-zinc-500">{text ? `answers: ${text.length > 90 ? text.slice(0, 90) + "…" : text}` : "answers without a tool call"}</span>;
  return (
    <span className="flex flex-col gap-0.5">
      {calls.map((c, i) => (
        <code key={i} className={clsx("break-all text-[11px]", tone || "text-zinc-300")}>
          {c}
        </code>
      ))}
    </span>
  );
}

function Trajectories({ items, candidates }: { items: any[]; candidates: string[] }) {
  const [open, setOpen] = useState<string | null>(items[0]?.trace_id ?? null);
  const used = candidates.filter((c) => items.some((t) => t.agreement?.[c] !== null && t.agreement?.[c] !== undefined));
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-sky-300" /> Tool-call trajectories
        </span>
      }
      actions={<span className="text-xs text-zinc-500">original run vs. each candidate's decision at every replayed step</span>}
    >
      <p className="mb-3 text-xs text-zinc-500">
        Each replayed step gets the original history (including the recorded tool results) and is compared on the tool calls it makes. This shows
        step-by-step decision agreement; it does not simulate where a candidate's own run would have gone after a different decision.
      </p>
      <table className="w-full">
        <thead>
          <tr>
            <th className="th">Trace</th>
            <th className="th">LLM steps</th>
            {used.map((c) => (
              <th key={c} className="th font-mono normal-case">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <Fragment key={t.trace_id}>
              <tr className="cursor-pointer border-t border-zinc-800/70 hover:bg-zinc-900/60" onClick={() => setOpen(open === t.trace_id ? null : t.trace_id)}>
                <td className="td">
                  <span className="flex items-center gap-1 font-mono text-xs">
                    {open === t.trace_id ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    {t.trace_name || "trace"} <span className="text-zinc-600">{t.trace_id.slice(0, 8)}</span>
                  </span>
                </td>
                <td className="td text-xs text-zinc-400">
                  {t.steps.length} · {t.steps.filter((s: any) => s.calls.length).length} with tool calls
                </td>
                {used.map((c) => (
                  <td key={c} className={clsx("td font-medium", agreeTone(t.agreement?.[c]))}>
                    {t.agreement?.[c] === null || t.agreement?.[c] === undefined ? "–" : `${Math.round(t.agreement[c] * 100)}%`}
                  </td>
                ))}
              </tr>
              {open === t.trace_id && (
                <tr>
                  <td colSpan={2 + used.length} className="bg-zinc-950/40 px-3 pb-3">
                    <div className="space-y-2 pt-2">
                      {t.steps.map((st: any, i: number) => (
                        <div key={st.observation_id} className="grid gap-2 rounded-lg border border-zinc-800 p-2 md:grid-cols-[110px_1fr_1.4fr]">
                          <div className="text-[11px] text-zinc-500">
                            step {i + 1}
                            <div className="font-mono text-zinc-600">{st.model}</div>
                          </div>
                          <div>
                            <div className="label mb-0.5">Original</div>
                            <Calls calls={st.calls} text={st.text} />
                          </div>
                          <div className="space-y-1">
                            {!st.replayed && <span className="text-[11px] text-zinc-600">not replayed</span>}
                            {Object.entries(st.candidates || {}).map(([c, v]: [string, any]) => (
                              <div key={c} className="flex items-start gap-1.5">
                                {v.agree ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" /> : <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />}
                                <div>
                                  <div className="font-mono text-[10px] text-zinc-500">{c}</div>
                                  <Calls calls={v.calls} text={v.text} tone={v.agree ? "text-emerald-200/90" : "text-red-200/90"} />
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                      <Link to={`/traces/${t.trace_id}`} className="text-xs text-indigo-300 hover:underline">
                        open trace
                      </Link>
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ToolMismatches({ candidates }: { candidates: any[] }) {
  const withM = candidates.filter((c) => c.tool_mismatches?.length);
  const [open, setOpen] = useState(false);
  if (!withM.length) return null;
  return (
    <div className="mt-4 border-t border-zinc-800 pt-3">
      <button className="flex items-center gap-1 text-xs font-medium text-zinc-300" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} Mismatched tool calls ({withM.reduce((a, c) => a + c.tool_mismatches.length, 0)})
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {withM.map((c) => (
            <div key={c.candidate}>
              <div className="mb-1.5 font-mono text-[11px] text-zinc-400">{c.candidate}</div>
              <div className="space-y-2">
                {c.tool_mismatches.map((m: any) => (
                  <div key={m.observation_id} className="rounded-lg border border-zinc-800 p-2">
                    <div className="mb-1.5 flex items-center justify-between gap-2 text-[11px]">
                      <span className="text-amber-300/90">{m.reason}</span>
                      <Link to={`/traces/${m.trace_id}`} className="text-indigo-300 hover:underline">
                        trace
                      </Link>
                    </div>
                    <div className="grid gap-2 md:grid-cols-2">
                      <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2">
                        <div className="label mb-1">Original</div>
                        <Calls calls={m.original[0]?.startsWith("(") ? [] : m.original} text={m.original_text} />
                      </div>
                      <div className="rounded-md border border-red-900/40 bg-red-500/[0.03] p-2">
                        <div className="label mb-1">Candidate</div>
                        <Calls calls={m.candidate[0]?.startsWith("(") ? [] : m.candidate} text={m.candidate_text} tone="text-red-200/90" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RecCard({ r }: { r: any }) {
  const tone =
    r.action === "switch" ? "border-emerald-500/30 bg-emerald-500/[0.06]" : r.action === "keep" ? "border-zinc-700 bg-zinc-900/60" : "border-amber-500/25 bg-amber-500/5";
  return (
    <div className={clsx("rounded-xl border p-4", tone)}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={clsx("rounded-md px-2 py-0.5 text-xs font-semibold uppercase", r.action === "switch" ? "bg-emerald-500/20 text-emerald-300" : r.action === "keep" ? "bg-zinc-800 text-zinc-300" : "bg-amber-500/15 text-amber-300")}>
          {r.action.replace("_", " ")}
        </span>
        {r.action === "switch" ? (
          <span className="flex flex-wrap items-center gap-1.5">
            <code className="text-zinc-300">{r.from_model}</code> <ArrowRight className="h-3.5 w-3.5 text-zinc-500" /> <code className="text-emerald-300">{r.to_model}</code>
          </span>
        ) : (
          <code className="text-zinc-300">{r.from_model || "–"}</code>
        )}
        {r.confidence && (
          <Badge className={r.confidence === "high" ? "text-emerald-300" : r.confidence === "medium" ? "text-sky-300" : "text-amber-300"}>
            {r.confidence} confidence · n={r.n}
          </Badge>
        )}
      </div>
      {r.to_model && r.action !== "switch" && (
        <div className="mt-3 text-xs text-zinc-500">
          Closest alternative: <code className="text-zinc-300">{r.to_model}</code>
        </div>
      )}
      {r.to_model && (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Cost per call" value={pct(r.cost_savings_pct === null || r.cost_savings_pct === undefined ? null : -r.cost_savings_pct)} good={(r.cost_savings_pct ?? 0) > 0} />
          <Metric label="Quality vs original" value={pct(r.quality_delta_pct, 1)} sub={r.quality_ci ? `95% CI ${qual(r.quality_ci[0])}–${qual(r.quality_ci[1])}` : undefined} good={(r.quality_delta_pct ?? -1) >= -5} />
          <Metric label="Latency (p50)" value={pct(r.latency_delta_pct)} good={(r.latency_delta_pct ?? 1) <= 0} bad={(r.latency_delta_pct ?? 0) > 50} />
          <Metric
            label={r.action === "switch" ? "Est. monthly savings" : "Monthly savings if switched"}
            value={fmtCost(r.monthly_savings_usd)}
            good={r.action === "switch" && (r.monthly_savings_usd ?? 0) > 0}
          />
        </div>
      )}
      {r.tool_agreement !== undefined && r.tool_agreement !== null && (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-zinc-400">
          <span className="flex items-center gap-1">
            <Wrench className="h-3.5 w-3.5 text-sky-300" /> Same tool calls in <b className={agreeTone(r.tool_agreement)}>{Math.round(r.tool_agreement * 100)}%</b> of {r.tool_n} replays
          </span>
          {r.decision_agreement !== null && r.decision_agreement !== undefined && <span>· call/answer decision agrees {Math.round(r.decision_agreement * 100)}%</span>}
        </div>
      )}
      <p className="mt-3 text-sm text-zinc-400">{r.rationale}</p>
    </div>
  );
}

function Metric({ label, value, sub, good, bad }: { label: string; value: string; sub?: string; good?: boolean; bad?: boolean }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={clsx("mt-0.5 text-lg font-semibold", good ? "text-emerald-300" : bad ? "text-amber-300" : "text-zinc-200")}>{value}</div>
      {sub && <div className="text-[11px] text-zinc-500">{sub}</div>}
    </div>
  );
}

function StepResult({ s }: { s: any }) {
  const base = s.baseline || {};
  const points = useMemo(() => {
    const pts: any[] = [];
    if (base.cost_per_call !== null && base.cost_per_call !== undefined) pts.push({ name: `${base.model} (current)`, cost: 100, quality: 100, n: base.n, current: true });
    for (const c of s.candidates) {
      if (c.quality_mean === null || c.cost_savings_pct === null || c.cost_savings_pct === undefined) continue;
      pts.push({ name: c.candidate, cost: Math.max(0, 100 - c.cost_savings_pct), quality: Math.round(c.quality_mean * 1000) / 10, n: c.n, lat: c.latency_delta_pct });
    }
    return pts;
  }, [s, base]);
  return (
    <Card
      title={<span className="font-mono text-[13px]">{s.step}</span>}
      actions={
        <span className="text-xs text-zinc-500">
          {s.sampled} sampled · baseline {base.model} · {fmtCost(base.cost_per_call)}/call · {fmtMs(base.latency_ms)}
        </span>
      }
    >
      <RecCard r={s.recommendation} />
      {Object.keys(s.skipped || {}).length > 0 && (
        <div className="mt-2 text-xs text-zinc-500">
          Not replayed: {Object.entries(s.skipped).map(([k, v]) => `${v}× ${k}`).join(" · ")}
        </div>
      )}
      {s.candidates.length > 0 && (
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(280px,1fr)_1.6fr]">
          <div className="h-64">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 0 }}>
                <CartesianGrid stroke="#27272a" />
                <XAxis type="number" dataKey="cost" domain={[0, (dataMax: number) => Math.max(110, Math.ceil(dataMax / 10) * 10 + 10)]} tick={{ fill: "#71717a", fontSize: 11 }} unit="%">
                  <Label value="cost vs current" position="insideBottom" offset={-12} fill="#71717a" fontSize={11} />
                </XAxis>
                <YAxis type="number" dataKey="quality" domain={[0, 100]} tick={{ fill: "#71717a", fontSize: 11 }} unit="%" width={44} />
                <ZAxis dataKey="n" range={[60, 220]} />
                <ReferenceLine y={95} stroke="#3f3f46" strokeDasharray="4 4" />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", fontSize: 12 }}
                  formatter={(v: any, k: any) => [`${v}%`, k === "cost" ? "cost vs current" : "quality vs original"]}
                  labelFormatter={() => ""}
                />
                <Scatter data={points.filter((p) => p.current)} fill="#a1a1aa" isAnimationActive={false} name="current" />
                <Scatter data={points.filter((p) => !p.current)} fill="#34d399" isAnimationActive={false} name="candidates" />
              </ScatterChart>
            </ResponsiveContainer>
            <div className="-mt-1 text-center text-[11px] text-zinc-500">grey = current model · green = candidates · up-left is better</div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Candidate</th>
                  <th className="th">n</th>
                  <th className="th">Quality</th>
                  <th className="th">Cost / call</th>
                  <th className="th">Δ cost</th>
                  <th className="th">p50 latency</th>
                  {s.tool_step && <th className="th" title="Replays where the candidate made the same tool calls (names, order, arguments) as the original">Tool agreement</th>}
                  <th className="th">Verdicts</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-zinc-800/70 text-zinc-400">
                  <td className="td font-mono text-xs">{base.model} (current)</td>
                  <td className="td">{base.n}</td>
                  <td className="td">100%</td>
                  <td className="td">{fmtCost(base.cost_per_call)}</td>
                  <td className="td">–</td>
                  <td className="td">{fmtMs(base.latency_ms)}</td>
                  {s.tool_step && <td className="td">100%</td>}
                  <td className="td" />
                </tr>
                {s.candidates.map((c: any) => (
                  <tr key={c.candidate} className="border-t border-zinc-800/70">
                    <td className="td font-mono text-xs">{c.candidate}</td>
                    <td className="td">
                      {c.n}
                      {c.errors > 0 && <span className="text-red-400" title={(c.error_samples || []).join("\n")}> ({c.errors} err)</span>}
                      {Object.keys(c.skipped || {}).length > 0 && <span className="text-amber-400" title={JSON.stringify(c.skipped)}> (skipped)</span>}
                    </td>
                    <td className="td">
                      {qual(c.quality_mean)}
                      {c.quality_ci && <span className="text-[11px] text-zinc-600"> ±{(((c.quality_ci[1] - c.quality_ci[0]) / 2) * 100).toFixed(0)}</span>}
                    </td>
                    <td className="td">{c.price_known ? fmtCost(c.cost_per_call) : <span className="text-zinc-600">unknown price</span>}</td>
                    <td className={clsx("td", (c.cost_savings_pct ?? 0) > 0 ? "text-emerald-300" : (c.cost_savings_pct ?? 0) < 0 ? "text-red-300" : "")}>
                      {pct(c.cost_savings_pct === null || c.cost_savings_pct === undefined ? null : -c.cost_savings_pct)}
                    </td>
                    <td className="td">
                      {fmtMs(c.latency_ms)} <span className="text-[11px] text-zinc-500">{pct(c.latency_delta_pct)}</span>
                    </td>
                    {s.tool_step && (
                      <td className="td" title={c.tool_n ? `decision ${qual(c.decision_agreement)} · names+order ${qual(c.tool_name_agreement)} · args ${qual(c.tool_args_match)} over ${c.tool_n} replays` : undefined}>
                        <span className={clsx("font-medium", agreeTone(c.tool_agreement))}>{qual(c.tool_agreement)}</span>
                        {c.decision_agreement !== null && c.decision_agreement !== undefined && (
                          <div className="text-[10px] text-zinc-500">decision {qual(c.decision_agreement)}</div>
                        )}
                      </td>
                    )}
                    <td className="td text-[11px] text-zinc-400">{Object.entries(c.labels || {}).map(([k, v]) => `${v} ${k}`).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <ToolMismatches candidates={s.candidates} />
      <Examples candidates={s.candidates} />
    </Card>
  );
}

function Examples({ candidates }: { candidates: any[] }) {
  const withEx = candidates.filter((c) => c.examples?.length);
  const [cand, setCand] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (!withEx.length) return null;
  const cur = withEx.find((c) => c.candidate === cand) || withEx[0];
  return (
    <div className="mt-4 border-t border-zinc-800 pt-3">
      <button className="flex items-center gap-1 text-xs font-medium text-zinc-300" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} Side-by-side examples
      </button>
      {open && (
        <div className="mt-3">
          {withEx.length > 1 && (
            <div className="mb-3 flex flex-wrap gap-1">
              {withEx.map((c) => (
                <button key={c.candidate} onClick={() => setCand(c.candidate)} className={clsx("rounded-md px-2 py-0.5 font-mono text-[11px]", c === cur ? "bg-zinc-700 text-white" : "text-zinc-400 hover:bg-zinc-800")}>
                  {c.candidate}
                </button>
              ))}
            </div>
          )}
          <div className="space-y-3">
            {cur.examples.map((e: any) => (
              <div key={e.observation_id} className="rounded-lg border border-zinc-800 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="text-zinc-400">
                    <span className="label mr-1">Prompt</span> {e.input}
                  </span>
                  <span className="flex items-center gap-2">
                    <Badge className={(e.score ?? 0) >= 0.95 ? "text-emerald-300" : (e.score ?? 0) >= 0.7 ? "text-amber-300" : "text-red-300"}>
                      {e.label} · {e.score === null ? "–" : e.score.toFixed(2)}
                    </Badge>
                    <Link to={`/traces/${e.trace_id}`} className="text-indigo-300 hover:underline">
                      trace
                    </Link>
                  </span>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2">
                    <div className="label mb-1">Original · {fmtMs(e.original_latency_ms)}</div>
                    {e.original_tool_calls?.length > 0 && <Calls calls={e.original_tool_calls} />}
                    <pre className="whitespace-pre-wrap break-words font-sans text-xs text-zinc-300">{e.original}</pre>
                  </div>
                  <div className="rounded-md border border-emerald-900/50 bg-emerald-500/[0.03] p-2">
                    <div className="label mb-1">
                      {cur.candidate} · {fmtMs(e.candidate_latency_ms)} · {fmtCost(e.candidate_cost)}
                      {e.cached ? " · cached" : ""}
                    </div>
                    {e.candidate_tool_calls?.length > 0 && <Calls calls={e.candidate_tool_calls} tone={e.tool_agreement ? "text-emerald-200/90" : "text-red-200/90"} />}
                    <pre className="whitespace-pre-wrap break-words font-sans text-xs text-zinc-300">{e.candidate}</pre>
                  </div>
                </div>
                {e.reason && <div className="mt-2 text-xs italic text-zinc-500">Judge: {e.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
