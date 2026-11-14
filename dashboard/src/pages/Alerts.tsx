import { BellRing, Plus, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, useApi } from "../api";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Select } from "../components/ui";
import { fmtAgo } from "../format";
import { useApp } from "../state";

const SYM: Record<string, string> = { gt: ">", gte: "≥", lt: "<", lte: "≤" };

export default function Alerts() {
  const { readOnly, project } = useApp();
  const { data, error, reload } = useApi<any>("/api/v1/alerts/rules", [project?.id]);
  const { data: events, reload: reloadEv } = useApi<any>("/api/v1/alerts/events", [project?.id]);
  const [form, setForm] = useState<any>({ name: "", metric: "error_rate", comparator: "gt", threshold: 0.1, window_minutes: 60, cooldown_minutes: 60, webhook_url: "", filter: {} });
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const create = () => {
    setErr(null);
    api("/api/v1/alerts/rules", { method: "POST", body: { ...form, webhook_url: form.webhook_url || null } }).then(reload).catch((e) => setErr(e.message));
  };
  const check = (id: string, test: boolean) =>
    api(`/api/v1/alerts/rules/${id}/check?test=${test}`, { method: "POST" })
      .then((r: any) => {
        setMsg(`Current value: ${r.value === null ? "n/a" : Number(r.value).toFixed(4)}${r.event ? ` · webhook ${r.event.delivered ? "delivered" : "not delivered"} (${r.event.response})` : " · not triggered"}`);
        reload();
        reloadEv();
      })
      .catch((e) => setErr(e.message));
  return (
    <div>
      <PageHeader title="Alerts" subtitle="Get a Slack/Discord/any webhook when cost spikes, latency degrades, errors rise or eval scores drop. Checked every minute." />
      {msg && <div className="mb-3 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-sm text-indigo-200">{msg}</div>}
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-4">
          <Card title="Rules">
            {data.data.length === 0 ? (
              <Empty title="No alert rules" />
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-800">
                    {["Name", "Condition", "Window", "Last value", "Last fired", ""].map((h) => (
                      <th key={h} className="th">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.data.map((r: any) => (
                    <tr key={r.id} className="border-b border-zinc-800/60 last:border-0">
                      <td className="td font-medium">{r.name}</td>
                      <td className="td font-mono text-xs">
                        {r.metric}
                        {r.filter?.score_name ? `(${r.filter.score_name})` : ""} {SYM[r.comparator]} {r.threshold}
                      </td>
                      <td className="td text-zinc-400">{r.window_minutes} min</td>
                      <td className="td">{r.last_value === null || r.last_value === undefined ? "–" : Number(r.last_value).toFixed(4)}</td>
                      <td className="td text-zinc-400">{r.last_triggered_at ? fmtAgo(r.last_triggered_at) : "never"}</td>
                      <td className="td whitespace-nowrap text-right">
                        <button disabled={readOnly} title="Evaluate now" className="mr-2 text-zinc-500 hover:text-indigo-300 disabled:opacity-30" onClick={() => check(r.id, false)}>
                          <BellRing className="h-4 w-4" />
                        </button>
                        <button disabled={readOnly} title="Send test webhook" className="mr-2 text-zinc-500 hover:text-indigo-300 disabled:opacity-30" onClick={() => check(r.id, true)}>
                          <Send className="h-4 w-4" />
                        </button>
                        <button disabled={readOnly} className="text-zinc-500 hover:text-rose-400 disabled:opacity-30" onClick={() => api(`/api/v1/alerts/rules/${r.id}`, { method: "DELETE" }).then(reload)}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card title="Recent alert events">
            {!events || events.data.length === 0 ? (
              <Empty title="Nothing fired yet" />
            ) : (
              <div className="space-y-2">
                {events.data.map((e: any) => (
                  <div key={e.id} className="flex items-start gap-3 rounded-lg border border-zinc-800 p-2.5 text-sm">
                    <BellRing className="mt-0.5 h-4 w-4 text-amber-400" />
                    <div className="flex-1">
                      <div>{e.message}</div>
                      <div className="mt-0.5 text-xs text-zinc-500">
                        {fmtAgo(e.created_at)} · {e.delivered ? <span className="text-emerald-400">delivered</span> : <span className="text-zinc-400">{e.response}</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <Card title="New alert">
          <div className="space-y-3">
            <input className="input w-full" placeholder="Name (e.g. Cost spike)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <div className="grid grid-cols-[2fr_1fr_1fr] gap-2">
              <Select value={form.metric} onChange={(v) => setForm({ ...form, metric: v })} options={data.metrics} />
              <Select value={form.comparator} onChange={(v) => setForm({ ...form, comparator: v })} options={data.comparators.map((c: string) => ({ value: c, label: SYM[c] }))} />
              <input type="number" step="any" className="input" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} />
            </div>
            {form.metric === "score_avg" && (
              <input className="input w-full" placeholder="score name (e.g. relevance)" value={form.filter.score_name || ""} onChange={(e) => setForm({ ...form, filter: { score_name: e.target.value } })} />
            )}
            <div className="grid grid-cols-2 gap-2">
              <label className="block"><span className="label">Window (min)</span><input type="number" className="input mt-1 w-full" value={form.window_minutes} onChange={(e) => setForm({ ...form, window_minutes: Number(e.target.value) })} /></label>
              <label className="block"><span className="label">Cooldown (min)</span><input type="number" className="input mt-1 w-full" value={form.cooldown_minutes} onChange={(e) => setForm({ ...form, cooldown_minutes: Number(e.target.value) })} /></label>
            </div>
            <input className="input w-full" placeholder="Webhook URL (Slack, Discord, any)" value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} />
            {err && <ErrorBox error={err} />}
            <button className="btn btn-primary w-full justify-center" disabled={readOnly} onClick={create}>
              <Plus className="h-4 w-4" /> Create alert
            </button>
            <div className="flex flex-wrap gap-1">
              {["cost", "error_rate", "latency_p95", "score_avg"].map((m) => <Badge key={m}>{m}</Badge>)}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
