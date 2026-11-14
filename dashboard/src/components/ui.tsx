import { clsx } from "clsx";
import { Bot, Box, Brain, Check, Copy, Database, Link2, Loader2, Search, Sparkles, Wrench, Zap } from "lucide-react";
import { ReactNode, useState } from "react";
import { LOWER_IS_BETTER, scoreColor } from "../format";

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={clsx("card", className)}>
      {(title || actions) && (
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2.5">
          <div className="text-sm font-semibold text-zinc-200">{title}</div>
          <div className="flex items-center gap-2">{actions}</div>
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Stat({ label, value, sub, icon, tone }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode; tone?: string }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        <span className="text-zinc-600">{icon}</span>
      </div>
      <div className={clsx("mt-2 text-2xl font-semibold tracking-tight", tone)}>{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx("inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 text-[11px] font-medium text-zinc-300", className)}>{children}</span>;
}

export function ScoreChip({ name, value, label }: { name: string; value?: number | null; label?: string | null }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", scoreColor(value, !LOWER_IS_BETTER.has(name)))} title={label || undefined}>
      {name} <b>{value === null || value === undefined ? label || "–" : value.toFixed(2)}</b>
    </span>
  );
}

export function StatusDot({ status }: { status?: string }) {
  return <span className={clsx("inline-block h-2 w-2 rounded-full", status === "error" ? "bg-rose-500 shadow-[0_0_8px] shadow-rose-500/60" : "bg-emerald-500")} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("h-4 w-4 animate-spin text-zinc-500", className)} />;
}

export function Loading() {
  return (
    <div className="flex items-center gap-2 p-8 text-sm text-zinc-500">
      <Spinner /> Loading…
    </div>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <Search className="h-6 w-6 text-zinc-600" />
      <div className="text-sm font-medium text-zinc-300">{title}</div>
      {children && <div className="max-w-md text-sm text-zinc-500">{children}</div>}
    </div>
  );
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      className={clsx("rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200", className)}
      title="Copy"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setOk(true);
        setTimeout(() => setOk(false), 1200);
      }}
    >
      {ok ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

const TYPE_STYLE: Record<string, { icon: any; cls: string; bar: string }> = {
  generation: { icon: Sparkles, cls: "text-violet-300 bg-violet-500/15 border-violet-500/30", bar: "bg-violet-500" },
  retrieval: { icon: Database, cls: "text-cyan-300 bg-cyan-500/15 border-cyan-500/30", bar: "bg-cyan-500" },
  tool: { icon: Wrench, cls: "text-amber-300 bg-amber-500/15 border-amber-500/30", bar: "bg-amber-500" },
  agent: { icon: Bot, cls: "text-pink-300 bg-pink-500/15 border-pink-500/30", bar: "bg-pink-500" },
  chain: { icon: Link2, cls: "text-sky-300 bg-sky-500/15 border-sky-500/30", bar: "bg-sky-500" },
  embedding: { icon: Brain, cls: "text-teal-300 bg-teal-500/15 border-teal-500/30", bar: "bg-teal-500" },
  event: { icon: Zap, cls: "text-yellow-300 bg-yellow-500/15 border-yellow-500/30", bar: "bg-yellow-500" },
  span: { icon: Box, cls: "text-zinc-300 bg-zinc-500/15 border-zinc-500/30", bar: "bg-zinc-400" },
};
export function typeStyle(t?: string) {
  return TYPE_STYLE[t || "span"] || TYPE_STYLE.span;
}
export function TypeBadge({ type }: { type?: string }) {
  const s = typeStyle(type);
  const Icon = s.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide", s.cls)}>
      <Icon className="h-3 w-3" />
      {type || "span"}
    </span>
  );
}

export function Select({ value, onChange, options, placeholder, className }: { value: string; onChange: (v: string) => void; options: (string | { value: string; label: string })[]; placeholder?: string; className?: string }) {
  return (
    <select className={clsx("input pr-8", className)} value={value} onChange={(e) => onChange(e.target.value)}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (typeof o === "string" ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>))}
    </select>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex gap-1 border-b border-zinc-800">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)} className={clsx("-mb-px border-b-2 px-3 py-2 text-sm", value === t.id ? "border-indigo-400 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-zinc-500">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}
