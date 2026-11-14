import { clsx } from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { CopyButton } from "./ui";

function isMessages(v: any): v is { role: string; content: any }[] {
  return Array.isArray(v) && v.length > 0 && v.every((m) => m && typeof m === "object" && "role" in m && ("content" in m || "tool_calls" in m || "parts" in m));
}

const ROLE: Record<string, string> = {
  system: "border-zinc-600 bg-zinc-800/40",
  user: "border-sky-600/50 bg-sky-500/5",
  human: "border-sky-600/50 bg-sky-500/5",
  assistant: "border-violet-600/50 bg-violet-500/5",
  ai: "border-violet-600/50 bg-violet-500/5",
  model: "border-violet-600/50 bg-violet-500/5",
  tool: "border-amber-600/50 bg-amber-500/5",
};

function textOf(c: any): string {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((p) => (typeof p === "string" ? p : p?.text ?? JSON.stringify(p))).join("\n");
  return JSON.stringify(c, null, 2);
}

export function DataView({ value, title }: { value: any; title?: string }) {
  const [raw, setRaw] = useState(false);
  if (value === null || value === undefined) return <div className="text-sm italic text-zinc-600">{title ? `No ${title.toLowerCase()}` : "empty"}</div>;
  const copyText = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="group relative">
      <div className="mb-1.5 flex items-center justify-between">
        {title && <span className="label">{title}</span>}
        <div className="flex items-center gap-1 opacity-60 group-hover:opacity-100">
          {typeof value !== "string" && (
            <button className="rounded px-1.5 text-[11px] text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200" onClick={() => setRaw(!raw)}>
              {raw ? "pretty" : "json"}
            </button>
          )}
          <CopyButton text={copyText} />
        </div>
      </div>
      {raw ? (
        <pre className="max-h-[480px] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-300">{copyText}</pre>
      ) : typeof value === "string" ? (
        <div className="max-h-[480px] overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-sm leading-relaxed text-zinc-200">{value}</div>
      ) : isMessages(value) ? (
        <div className="space-y-2">
          {value.map((m, i) => (
            <div key={i} className={clsx("rounded-lg border-l-2 px-3 py-2", ROLE[m.role] || ROLE.system)}>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{m.role}</div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">{textOf(m.content ?? (m as any).parts ?? "")}</div>
              {(m as any).tool_calls && <pre className="mt-1 font-mono text-xs text-amber-300">{JSON.stringify((m as any).tool_calls, null, 2)}</pre>}
            </div>
          ))}
        </div>
      ) : (
        <div className="max-h-[480px] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs">
          <JsonNode value={value} depth={0} />
        </div>
      )}
    </div>
  );
}

function JsonNode({ value, depth, name }: { value: any; depth: number; name?: string }) {
  const [open, setOpen] = useState(depth < 2);
  const label = name !== undefined ? <span className="text-sky-300">{name}: </span> : null;
  if (value === null) return <div>{label}<span className="text-zinc-500">null</span></div>;
  if (typeof value !== "object") {
    const cls = typeof value === "string" ? "text-emerald-300" : typeof value === "number" ? "text-amber-300" : "text-pink-300";
    return (
      <div className="whitespace-pre-wrap break-words">
        {label}
        <span className={cls}>{typeof value === "string" ? `"${value}"` : String(value)}</span>
      </div>
    );
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value);
  const br = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div>
      <button className="inline-flex items-center text-zinc-400 hover:text-zinc-200" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {label}
        <span>{br[0]}</span>
        {!open && <span className="text-zinc-600"> {entries.length} items {br[1]}</span>}
      </button>
      {open && (
        <div className="ml-3 border-l border-zinc-800 pl-2">
          {entries.map(([k, v]) => (
            <JsonNode key={k} name={k} value={v} depth={depth + 1} />
          ))}
          <div className="text-zinc-400">{br[1]}</div>
        </div>
      )}
    </div>
  );
}
