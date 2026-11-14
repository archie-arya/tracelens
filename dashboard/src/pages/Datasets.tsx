import { ArrowLeft, Database } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../api";
import { Card, Empty, ErrorBox, Loading, PageHeader, ScoreChip } from "../components/ui";
import { fmtAgo, fmtCost, fmtMs, preview } from "../format";
import { useApp } from "../state";

export default function Datasets() {
  const { project } = useApp();
  const { data, error } = useApi<any>("/api/v1/datasets", [project?.id]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div>
      <PageHeader title="Datasets & experiments" subtitle="Golden test sets. Run your app over them with tracelens.experiments.run_experiment() and compare runs." />
      {data.data.length === 0 ? (
        <div className="card">
          <Empty title="No datasets yet">
            <code className="text-xs">create_dataset("qa", [{`{"input": ..., "expected_output": ...}`}])</code>
          </Empty>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.data.map((d: any) => (
            <Link key={d.id} to={`/datasets/${d.id}`} className="card p-4 transition hover:border-indigo-500/60">
              <div className="flex items-center gap-2 font-semibold">
                <Database className="h-4 w-4 text-cyan-300" /> {d.name}
              </div>
              <p className="mt-1 text-sm text-zinc-400">{d.description || "—"}</p>
              <div className="mt-3 text-xs text-zinc-500">
                {d.item_count} items · {d.run_count} runs · created {fmtAgo(d.created_at)}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function DatasetDetail() {
  const { id } = useParams();
  const { data: ds, error } = useApi<any>(`/api/v1/datasets/${id}`);
  const { data: runs } = useApi<any>(`/api/v1/datasets/${id}/runs`);
  if (error) return <ErrorBox error={error} />;
  if (!ds || !runs) return <Loading />;
  const scoreNames = Array.from(new Set(runs.runs.flatMap((r: any) => Object.keys(r.avg_scores)))) as string[];
  const best: Record<string, number> = {};
  scoreNames.forEach((n) => (best[n] = Math.max(...runs.runs.map((r: any) => r.avg_scores[n] ?? -1))));
  return (
    <div>
      <Link to="/datasets" className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-300">
        <ArrowLeft className="h-4 w-4" /> Datasets
      </Link>
      <PageHeader title={ds.name} subtitle={ds.description} />
      <Card title="Experiment runs" className="mb-4">
        {runs.runs.length === 0 ? (
          <Empty title="No runs yet" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-zinc-800">
                  {["Run", "Items", ...scoreNames, "Avg latency", "Cost", "Errors", "When"].map((h) => (
                    <th key={h} className="th">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.runs.map((r: any) => (
                  <tr key={r.run_name} className="border-b border-zinc-800/60 last:border-0">
                    <td className="td">
                      <div className="font-medium">{r.run_name}</div>
                      {r.metadata && Object.keys(r.metadata).length > 0 && <div className="text-xs text-zinc-500">{JSON.stringify(r.metadata)}</div>}
                    </td>
                    <td className="td">{r.items}</td>
                    {scoreNames.map((n) => (
                      <td key={n} className="td">
                        {r.avg_scores[n] !== undefined ? (
                          <span className={r.avg_scores[n] === best[n] && runs.runs.length > 1 ? "font-semibold text-emerald-300" : ""}>{r.avg_scores[n].toFixed(3)}</span>
                        ) : (
                          "–"
                        )}
                      </td>
                    ))}
                    <td className="td">{fmtMs(r.avg_latency_ms)}</td>
                    <td className="td">{fmtCost(r.total_cost)}</td>
                    <td className={r.errors ? "td text-rose-400" : "td text-zinc-500"}>{r.errors}</td>
                    <td className="td text-zinc-400">{fmtAgo(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title={`Items × runs (${ds.items.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                <th className="th">Input</th>
                <th className="th">Expected</th>
                {runs.runs.map((r: any) => (
                  <th key={r.run_name} className="th">{r.run_name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ds.items.map((it: any) => (
                <tr key={it.id} className="border-b border-zinc-800/60 align-top last:border-0">
                  <td className="td max-w-[260px] text-zinc-300">{preview(it.input, 140)}</td>
                  <td className="td max-w-[200px] text-zinc-400">{preview(it.expected_output, 100)}</td>
                  {runs.runs.map((r: any) => {
                    const c = runs.matrix[it.id]?.[r.run_name];
                    return (
                      <td key={r.run_name} className="td max-w-[280px]">
                        {c ? (
                          <Link to={`/traces/${c.trace_id}`} className="block hover:text-indigo-200">
                            <div className="text-zinc-300">{preview(c.output, 120)}</div>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {Object.entries(c.scores).map(([n, v]: any) => <ScoreChip key={n} name={n} value={v} />)}
                            </div>
                          </Link>
                        ) : (
                          "–"
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
