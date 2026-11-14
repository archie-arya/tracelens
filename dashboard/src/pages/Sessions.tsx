import { Link } from "react-router-dom";
import { useApi } from "../api";
import { Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { fmtAgo, fmtCost, fmtMs, fmtNum } from "../format";
import { useApp } from "../state";

export default function Sessions() {
  const { project } = useApp();
  const { data, error } = useApi<any>("/api/v1/sessions?limit=200", [project?.id]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div>
      <PageHeader title="Sessions" subtitle="Multi-turn conversations grouped by session_id" />
      <div className="card overflow-x-auto">
        {data.data.length === 0 ? (
          <Empty title="No sessions yet">Pass session_id to tracelens.trace(...) or @observe(session_id=...) to group conversation turns.</Empty>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                {["Session", "User", "Turns", "Duration", "Tokens", "Cost", "Errors", "Last active"].map((h) => (
                  <th key={h} className="th">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.data.map((s: any) => (
                <tr key={s.session_id} className="border-b border-zinc-800/60 last:border-0 hover:bg-zinc-800/40">
                  <td className="td font-mono text-xs">
                    <Link className="text-indigo-300 hover:underline" to={`/traces?session_id=${encodeURIComponent(s.session_id)}&order_by=`}>{s.session_id}</Link>
                  </td>
                  <td className="td text-zinc-400">{s.user_id || "–"}</td>
                  <td className="td">{s.traces}</td>
                  <td className="td text-zinc-400">{s.end_time && s.start_time ? fmtMs((s.end_time - s.start_time) * 1000) : "–"}</td>
                  <td className="td text-zinc-400">{fmtNum(s.total_tokens)}</td>
                  <td className="td text-zinc-400">{fmtCost(s.total_cost)}</td>
                  <td className={s.errors ? "td text-rose-400" : "td text-zinc-500"}>{s.errors || 0}</td>
                  <td className="td text-zinc-400">{fmtAgo(s.end_time || s.start_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
