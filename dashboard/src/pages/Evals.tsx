import { Play, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { api, qs, useApi } from "../api";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, ScoreChip, Select, Spinner, Tabs } from "../components/ui";
import { fmtAgo } from "../format";
import { useApp } from "../state";
import { axis, tickTime, tooltipStyle } from "./Overview";

const COLORS = ["#818cf8", "#22d3ee", "#f472b6", "#fbbf24", "#34d399", "#f87171", "#a78bfa"];

export default function Evals() {
  const [tab, setTab] = useState("scores");
  const { readOnly, project } = useApp();
  const { data: evs } = useApi<any>("/api/v1/evaluators");
  return (
    <div>
      <PageHeader
        title="Evaluations"
        subtitle={
          <>
            LLM-as-judge and heuristic evaluators. Judge models: <span className="font-mono text-zinc-400">{(evs?.judge_models || []).slice(0, 2).join(", ")}</span>
            {evs && !evs.judge_available && <span className="text-amber-400"> (no judge key configured on server)</span>}
          </>
        }
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "scores", label: "Scores" },
          { id: "rules", label: "Online eval rules" },
          { id: "run", label: "Batch run" },
          { id: "playground", label: "Playground" },
          { id: "catalog", label: "Evaluator catalog" },
        ]}
      />
      <div className="mt-4">
        {tab === "scores" && <ScoresTab key={project?.id} />}
        {tab === "rules" && <RulesTab evs={evs?.data || []} readOnly={readOnly} />}
        {tab === "run" && <RunTab evs={evs?.data || []} readOnly={readOnly} />}
        {tab === "playground" && <Playground evs={evs?.data || []} readOnly={readOnly} />}
        {tab === "catalog" && <Catalog evs={evs?.data || []} />}
      </div>
    </div>
  );
}

function ScoresTab() {
  const { range } = useApp();
  const { data: m } = useApi<any>(`/api/v1/metrics${qs({ range })}`);
  const [name, setName] = useState("");
  const { data: scores, error } = useApi<any>(`/api/v1/scores${qs({ name, limit: 100 })}`);
  if (error) return <ErrorBox error={error} />;
  if (!m || !scores) return <Loading />;
  const names: string[] = m.scores.map((s: any) => s.name);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        {m.scores.map((s: any) => (
          <button key={s.name} onClick={() => setName(name === s.name ? "" : s.name)} className={`card p-3 text-left transition hover:border-indigo-500/60 ${name === s.name ? "border-indigo-500" : ""}`}>
            <div className="label">{s.name}</div>
            <div className="mt-1 text-2xl font-semibold">{s.avg?.toFixed(2)}</div>
            <div className="text-xs text-zinc-500">{s.count} scores</div>
          </button>
        ))}
      </div>
      {names.length > 0 && (
        <Card title="Average score over time">
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={m.timeseries}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="t" tickFormatter={tickTime(m.bucket_seconds)} {...axis} />
                <YAxis domain={[0, 1]} {...axis} />
                <Tooltip {...tooltipStyle} labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {names.map((n, i) => (
                  <Line isAnimationActive={false} key={n} dataKey={`score:${n}`} name={n} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={2} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
      <Card title={`Recent scores${name ? ` · ${name}` : ""}`}>
        {scores.data.length === 0 ? (
          <Empty title="No scores yet">Create an online eval rule or run a batch evaluation.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-zinc-800">
                  {["When", "Score", "Label", "Reason", "Source", "Trace"].map((h) => (
                    <th key={h} className="th">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {scores.data.map((s: any) => (
                  <tr key={s.id} className="border-b border-zinc-800/60 last:border-0">
                    <td className="td whitespace-nowrap text-zinc-400">{fmtAgo(s.created_at)}</td>
                    <td className="td"><ScoreChip name={s.name} value={s.value} /></td>
                    <td className="td text-zinc-400">{s.string_value || "–"}</td>
                    <td className="td max-w-[520px] text-zinc-300">{s.comment || "–"}</td>
                    <td className="td whitespace-nowrap text-xs text-zinc-500">{s.source}{s.evaluator ? ` · ${s.evaluator}` : ""}</td>
                    <td className="td"><Link className="font-mono text-xs text-indigo-300 hover:underline" to={`/traces/${s.trace_id}`}>{s.trace_id.slice(0, 10)}…</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ConfigFields({ ev, config, setConfig }: { ev: any; config: any; setConfig: (c: any) => void }) {
  if (!ev) return null;
  return (
    <>
      {(ev.config || []).map((f: any) => (
        <label key={f.name} className="block">
          <span className="label">{f.name}{f.required ? " *" : ""}</span>
          {f.type === "text" ? (
            <textarea className="input mt-1 h-20 w-full" placeholder={f.description} value={config[f.name] ?? ""} onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })} />
          ) : f.type === "boolean" ? (
            <input type="checkbox" className="ml-2" checked={config[f.name] ?? f.default ?? false} onChange={(e) => setConfig({ ...config, [f.name]: e.target.checked })} />
          ) : (
            <input
              className="input mt-1 w-full"
              placeholder={f.description || String(f.default ?? "")}
              value={Array.isArray(config[f.name]) ? config[f.name].join(", ") : config[f.name] ?? ""}
              onChange={(e) => setConfig({ ...config, [f.name]: f.type === "list" ? e.target.value.split(",").map((s) => s.trim()).filter(Boolean) : f.type === "number" ? Number(e.target.value) : e.target.value })}
            />
          )}
        </label>
      ))}
    </>
  );
}

function RulesTab({ evs, readOnly }: { evs: any[]; readOnly: boolean }) {
  const { data, reload, error } = useApi<any>("/api/v1/eval-rules");
  const [form, setForm] = useState<any>({ evaluator: "llm_relevance", sample_rate: 1, config: {}, filter: {} });
  const [err, setErr] = useState<string | null>(null);
  const ev = evs.find((e) => e.name === form.evaluator);
  const create = async () => {
    setErr(null);
    try {
      await api("/api/v1/eval-rules", { method: "POST", body: form });
      reload();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card title="Active rules · run automatically on every new matching trace">
        {error && <ErrorBox error={error} />}
        {!data ? (
          <Loading />
        ) : data.data.length === 0 ? (
          <Empty title="No eval rules" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                {["Evaluator", "Filter", "Sampling", "Config", ""].map((h) => (
                  <th key={h} className="th">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.data.map((r: any) => (
                <tr key={r.id} className="border-b border-zinc-800/60 last:border-0">
                  <td className="td font-medium">{r.evaluator}</td>
                  <td className="td text-xs text-zinc-400">{Object.keys(r.filter || {}).length ? JSON.stringify(r.filter) : "all traces"}</td>
                  <td className="td">{Math.round(r.sample_rate * 100)}%</td>
                  <td className="td max-w-[260px] truncate text-xs text-zinc-500">{JSON.stringify(r.config)}</td>
                  <td className="td text-right">
                    <button disabled={readOnly} className="text-zinc-500 hover:text-rose-400 disabled:opacity-30" onClick={() => api(`/api/v1/eval-rules/${r.id}`, { method: "DELETE" }).then(reload)}>
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="New rule">
        <div className="space-y-3">
          <label className="block">
            <span className="label">Evaluator</span>
            <Select className="mt-1 w-full" value={form.evaluator} onChange={(v) => setForm({ ...form, evaluator: v, config: {} })} options={evs.map((e) => e.name)} />
          </label>
          <ConfigFields ev={ev} config={form.config} setConfig={(c) => setForm({ ...form, config: c })} />
          <label className="block">
            <span className="label">Only traces named (optional)</span>
            <input className="input mt-1 w-full" value={form.filter.name || ""} onChange={(e) => setForm({ ...form, filter: { ...form.filter, name: e.target.value || undefined } })} />
          </label>
          <label className="block">
            <span className="label">Sample rate: {Math.round(form.sample_rate * 100)}%</span>
            <input type="range" min={0} max={1} step={0.05} className="mt-1 w-full" value={form.sample_rate} onChange={(e) => setForm({ ...form, sample_rate: Number(e.target.value) })} />
          </label>
          {err && <ErrorBox error={err} />}
          <button className="btn btn-primary w-full justify-center" disabled={readOnly} onClick={create}>
            <Plus className="h-4 w-4" /> Create rule
          </button>
          {readOnly && <p className="text-xs text-zinc-500">Read-only demo. Create a project in Settings to add rules.</p>}
        </div>
      </Card>
    </div>
  );
}

function RunTab({ evs, readOnly }: { evs: any[]; readOnly: boolean }) {
  const [evName, setEv] = useState("llm_relevance");
  const [config, setConfig] = useState<any>({});
  const [limit, setLimit] = useState(10);
  const [job, setJob] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!job || job.status === "completed") return;
    const h = setTimeout(() => api(`/api/v1/evals/jobs/${job.id}`).then(setJob).catch(() => {}), 1000);
    return () => clearTimeout(h);
  }, [job]);
  const ev = evs.find((e) => e.name === evName);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <Card title="Evaluate recent traces">
        <div className="space-y-3">
          <Select className="w-full" value={evName} onChange={(v) => (setEv(v), setConfig({}))} options={evs.map((e) => e.name)} />
          <ConfigFields ev={ev} config={config} setConfig={setConfig} />
          <label className="block">
            <span className="label">Most recent N traces</span>
            <input type="number" className="input mt-1 w-full" value={limit} min={1} max={200} onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          {err && <ErrorBox error={err} />}
          <button
            className="btn btn-primary w-full justify-center"
            disabled={readOnly}
            onClick={() => {
              setErr(null);
              api("/api/v1/evals/run", { method: "POST", body: { evaluator: evName, config, limit } }).then(setJob).catch((e) => setErr(e.message));
            }}
          >
            <Play className="h-4 w-4" /> Run
          </button>
        </div>
      </Card>
      <Card title="Job">
        {!job ? (
          <Empty title="No job yet" />
        ) : (
          <div>
            <div className="mb-3 flex items-center gap-3 text-sm">
              {job.status !== "completed" && <Spinner />}
              <Badge>{job.status}</Badge>
              <span>{job.done}/{job.total} traces</span>
              {job.avg_score !== undefined && job.avg_score !== null && <span>avg <b>{job.avg_score}</b></span>}
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded bg-zinc-800">
              <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(job.done / Math.max(1, job.total)) * 100}%` }} />
            </div>
            <div className="mt-3 space-y-1.5">
              {job.scores.map((s: any) => (
                <div key={s.trace_id} className="flex items-start gap-2 text-sm">
                  <ScoreChip name={ev?.score_name || evName} value={s.value} />
                  <Link className="font-mono text-xs text-indigo-300" to={`/traces/${s.trace_id}`}>{s.trace_id.slice(0, 8)}</Link>
                  <span className="text-zinc-400">{s.reason}</span>
                </div>
              ))}
              {job.errors.map((e: string) => <div key={e} className="text-xs text-rose-400">{e}</div>)}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

function Playground({ evs, readOnly }: { evs: any[]; readOnly: boolean }) {
  const [evName, setEv] = useState("llm_faithfulness");
  const [config, setConfig] = useState<any>({});
  const [input, setInput] = useState("What is the capital of Australia?");
  const [output, setOutput] = useState("The capital of Australia is Sydney, its largest city.");
  const [context, setContext] = useState("Canberra is the capital city of Australia. Sydney is the most populous city.");
  const [expected, setExpected] = useState("Canberra");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ev = evs.find((e) => e.name === evName);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Try an evaluator on any input/output">
        <div className="space-y-3">
          <Select className="w-full" value={evName} onChange={(v) => (setEv(v), setConfig({}))} options={evs.map((e) => e.name)} />
          <ConfigFields ev={ev} config={config} setConfig={setConfig} />
          <label className="block"><span className="label">Input</span><textarea className="input mt-1 h-16 w-full" value={input} onChange={(e) => setInput(e.target.value)} /></label>
          <label className="block"><span className="label">Output</span><textarea className="input mt-1 h-20 w-full" value={output} onChange={(e) => setOutput(e.target.value)} /></label>
          <label className="block"><span className="label">Context (retrieved docs)</span><textarea className="input mt-1 h-16 w-full" value={context} onChange={(e) => setContext(e.target.value)} /></label>
          <label className="block"><span className="label">Expected output</span><input className="input mt-1 w-full" value={expected} onChange={(e) => setExpected(e.target.value)} /></label>
          {err && <ErrorBox error={err} />}
          <button
            className="btn btn-primary w-full justify-center"
            disabled={readOnly || busy}
            onClick={() => {
              setBusy(true);
              setErr(null);
              api("/api/v1/evals/test", { method: "POST", body: { evaluator: evName, config, input, output, context: context ? [context] : [], expected_output: expected || null } })
                .then(setRes)
                .catch((e) => setErr(e.message))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? <Spinner className="text-white" /> : <Play className="h-4 w-4" />} Evaluate
          </button>
          {readOnly && <p className="text-xs text-zinc-500">Needs an API key (Settings) so the demo's free judge quota isn't shared publicly.</p>}
        </div>
      </Card>
      <Card title="Result">
        {!res ? (
          <Empty title="Run an evaluator to see its verdict" />
        ) : (
          <div className="space-y-3">
            <div className="text-4xl font-semibold">{res.score === null ? "–" : res.score.toFixed(2)}</div>
            {res.label && <Badge>{res.label}</Badge>}
            <p className="text-sm text-zinc-300">{res.reason}</p>
            <div className="text-xs text-zinc-500">{res.metadata?.judge_model ? `${res.metadata.judge_model} · ` : ""}{res.ms} ms</div>
          </div>
        )}
      </Card>
    </div>
  );
}

function Catalog({ evs }: { evs: any[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {evs.map((e) => (
        <div key={e.name} className="card p-4">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{e.name}</span>
            <Badge className={e.kind === "llm" ? "text-violet-300" : "text-emerald-300"}>{e.kind === "llm" ? "LLM judge" : e.kind}</Badge>
          </div>
          <p className="mt-2 text-sm text-zinc-400">{e.description}</p>
          <div className="mt-2 text-xs text-zinc-500">
            score: <span className="text-zinc-300">{e.score_name}</span> · {e.higher_is_better ? "higher is better" : "lower is better"}
            {e.requires?.length ? ` · needs ${e.requires.join(", ")}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}
