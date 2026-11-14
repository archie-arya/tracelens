import { Activity, AlertTriangle, Coins, Gauge, Hash, Star, Users } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { qs, useApi } from "../api";
import { Card, Empty, ErrorBox, Loading, PageHeader, ScoreChip, Stat } from "../components/ui";
import { fmtCost, fmtMs, fmtNum, fmtPct } from "../format";
import { useApp } from "../state";
import { Onboarding } from "./Settings";

export const tooltipStyle = { contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }, labelStyle: { color: "#a1a1aa" } };
export const axis = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;

export function tickTime(bucket: number) {
  return (t: number) => {
    const d = new Date(t * 1000);
    return bucket >= 86400 ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };
}

export default function Overview() {
  const { range, project } = useApp();
  const { data: m, error, loading } = useApi<any>(`/api/v1/metrics${qs({ range })}`, [project?.id]);
  if (error) return <ErrorBox error={error} />;
  if (!m) return <Loading />;
  const t = m.totals;
  const fmtT = tickTime(m.bucket_seconds);
  const series = m.timeseries.map((s: any) => ({ ...s, cost: s.cost }));
  const avgScore = m.scores.find((s: any) => s.name === "relevance") || m.scores[0];
  return (
    <div>
      <PageHeader title="Overview" subtitle={`Last ${range} · ${project?.name || ""}`} actions={loading ? <span className="text-xs text-zinc-500">refreshing…</span> : null} />
      {t.traces === 0 && <Onboarding compact />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Traces" value={fmtNum(t.traces)} sub={`${fmtNum(t.generations)} LLM calls`} icon={<Activity className="h-4 w-4" />} />
        <Stat label="Total cost" value={fmtCost(t.cost)} sub={`${fmtCost(t.avg_cost_per_trace)} / trace`} icon={<Coins className="h-4 w-4" />} />
        <Stat label="Tokens" value={fmtNum(t.tokens)} icon={<Hash className="h-4 w-4" />} />
        <Stat label="p50 latency" value={fmtMs(t.p50_latency_ms)} icon={<Gauge className="h-4 w-4" />} />
        <Stat label="p95 latency" value={fmtMs(t.p95_latency_ms)} sub={`p99 ${fmtMs(t.p99_latency_ms)}`} icon={<Gauge className="h-4 w-4" />} />
        <Stat label="Error rate" value={fmtPct(t.error_rate)} sub={`${t.errors} failed traces`} tone={t.error_rate > 0.05 ? "text-rose-400" : undefined} icon={<AlertTriangle className="h-4 w-4" />} />
        <Stat label={avgScore ? `Avg ${avgScore.name}` : "Avg score"} value={avgScore ? avgScore.avg.toFixed(2) : "–"} sub={`${t.users} users`} icon={avgScore ? <Star className="h-4 w-4" /> : <Users className="h-4 w-4" />} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Traces & errors">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={series}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="t" tickFormatter={fmtT} {...axis} />
                <YAxis {...axis} allowDecimals={false} />
                <Tooltip {...tooltipStyle} labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} />
                <Bar isAnimationActive={false} dataKey="traces" stackId="a" fill="#6366f1" radius={[0, 0, 0, 0]} name="traces" />
                <Bar isAnimationActive={false} dataKey="errors" fill="#f43f5e" name="errors" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Latency (ms)">
          <div className="h-56">
            <ResponsiveContainer>
              <LineChart data={series}>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="t" tickFormatter={fmtT} {...axis} />
                <YAxis {...axis} />
                <Tooltip {...tooltipStyle} labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} formatter={(v: any) => fmtMs(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line isAnimationActive={false} dataKey="p50_latency_ms" name="p50" stroke="#22d3ee" dot={{ r: 2.5, fill: "#22d3ee" }} strokeWidth={2} connectNulls />
                <Line isAnimationActive={false} dataKey="p95_latency_ms" name="p95" stroke="#a78bfa" dot={{ r: 2.5, fill: "#a78bfa" }} strokeWidth={2} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Cost (USD)">
          <div className="h-56">
            <ResponsiveContainer>
              <AreaChart data={series}>
                <defs>
                  <linearGradient id="gc" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="1" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="t" tickFormatter={fmtT} {...axis} />
                <YAxis {...axis} tickFormatter={(v) => fmtCost(v)} width={70} />
                <Tooltip {...tooltipStyle} labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} formatter={(v: any) => fmtCost(v)} />
                <Area isAnimationActive={false} dataKey="cost" stroke="#10b981" fill="url(#gc)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Tokens">
          <div className="h-56">
            <ResponsiveContainer>
              <AreaChart data={series}>
                <defs>
                  <linearGradient id="gt" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#f59e0b" stopOpacity={0.4} />
                    <stop offset="1" stopColor="#f59e0b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#27272a" vertical={false} />
                <XAxis dataKey="t" tickFormatter={fmtT} {...axis} />
                <YAxis {...axis} tickFormatter={(v) => fmtNum(v)} />
                <Tooltip {...tooltipStyle} labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} />
                <Area isAnimationActive={false} dataKey="tokens" stroke="#f59e0b" fill="url(#gt)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card title="Models" className="xl:col-span-2">
          {m.by_model.length === 0 ? (
            <Empty title="No LLM calls yet" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    {["Model", "Calls", "Input tok", "Output tok", "Cost", "Avg latency", "p95", "TTFT", "Errors"].map((h) => (
                      <th key={h} className="th">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {m.by_model.map((r: any) => (
                    <tr key={r.model} className="border-b border-zinc-800/60 last:border-0">
                      <td className="td font-mono text-xs">
                        <Link className="hover:text-indigo-300" to={`/traces?model=${encodeURIComponent(r.model)}`}>{r.model}</Link>
                      </td>
                      <td className="td">{fmtNum(r.calls)}</td>
                      <td className="td">{fmtNum(r.prompt_tokens)}</td>
                      <td className="td">{fmtNum(r.completion_tokens)}</td>
                      <td className="td">{fmtCost(r.cost)}</td>
                      <td className="td">{fmtMs(r.avg_latency_ms)}</td>
                      <td className="td">{fmtMs(r.p95_latency_ms)}</td>
                      <td className="td">{fmtMs(r.avg_ttft_ms)}</td>
                      <td className={r.errors ? "td text-rose-400" : "td text-zinc-500"}>{r.errors}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Scores">
          {m.scores.length === 0 ? (
            <Empty title="No scores yet">Add an eval rule or send user feedback with tracelens.score().</Empty>
          ) : (
            <div className="space-y-3">
              {m.scores.map((s: any) => (
                <div key={s.name} className="flex items-center justify-between">
                  <ScoreChip name={s.name} value={s.avg} />
                  <span className="text-xs text-zinc-500">{s.count} scored · min {s.min} · max {s.max}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Top workflows">
          <table className="w-full">
            <tbody>
              {m.by_name.slice(0, 8).map((r: any) => (
                <tr key={r.name} className="border-b border-zinc-800/60 last:border-0">
                  <td className="td truncate">
                    <Link className="hover:text-indigo-300" to={`/traces?name=${encodeURIComponent(r.name)}`}>{r.name}</Link>
                  </td>
                  <td className="td text-right text-zinc-400">{fmtNum(r.count)}</td>
                  <td className="td text-right text-zinc-400">{fmtMs(r.p95_latency_ms)}</td>
                  <td className="td text-right text-zinc-400">{fmtCost(r.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Top users by cost">
          <table className="w-full">
            <tbody>
              {m.by_user.slice(0, 8).map((r: any) => (
                <tr key={r.user_id} className="border-b border-zinc-800/60 last:border-0">
                  <td className="td">
                    <Link className="hover:text-indigo-300" to={`/traces?user_id=${encodeURIComponent(r.user_id)}`}>{r.user_id}</Link>
                  </td>
                  <td className="td text-right text-zinc-400">{r.count} traces</td>
                  <td className="td text-right text-zinc-400">{fmtCost(r.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Tags">
          <div className="flex flex-wrap gap-2">
            {m.by_tag.map((r: any) => (
              <Link key={r.tag} to={`/traces?tag=${encodeURIComponent(r.tag)}`} className="rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-1 text-xs hover:border-indigo-500">
                {r.tag} <span className="text-zinc-500">{r.count}</span>
              </Link>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
