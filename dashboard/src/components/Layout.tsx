import { clsx } from "clsx";
import { Bell, Database, FlaskConical, KeyRound, LayoutDashboard, ListTree, MessagesSquare, Settings } from "lucide-react";
import { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useApp } from "../state";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/traces", label: "Traces", icon: ListTree },
  { to: "/sessions", label: "Sessions", icon: MessagesSquare },
  { to: "/evals", label: "Evaluations", icon: FlaskConical },
  { to: "/datasets", label: "Datasets", icon: Database },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/settings", label: "Settings & Keys", icon: Settings },
];

function GithubIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2">
      <img src={`${import.meta.env.BASE_URL}favicon.svg`} className="h-7 w-7" alt="" />
      <span className="text-[15px] font-semibold tracking-tight">
        Trace<span className="bg-gradient-to-r from-indigo-400 to-cyan-300 bg-clip-text text-transparent">Lens</span>
      </span>
    </Link>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { project, readOnly, staticMode, range, setRange } = useApp();
  return (
    <div className="flex h-full">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 md:flex">
        <div className="px-4 py-4">
          <Logo />
        </div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition", isActive ? "bg-zinc-800/80 text-white" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200")}>
              <n.icon className="h-4 w-4" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-2 border-t border-zinc-800 p-3 text-xs text-zinc-500">
          <a className="flex items-center gap-2 hover:text-zinc-300" href="https://github.com/archie-arya/tracelens" target="_blank" rel="noreferrer">
            <GithubIcon /> GitHub · MIT
          </a>
          <div>Open-source LLM observability</div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800 bg-zinc-950/80 px-5 py-2.5 backdrop-blur">
          <div className="flex items-center gap-3 md:hidden">
            <Logo />
          </div>
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="text-zinc-500">Project</span>
            <span className="truncate font-medium">{project?.name || "—"}</span>
            {readOnly && <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-300">{staticMode ? "Static demo · read-only" : "Public demo · read-only"}</span>}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-zinc-800 p-0.5">
              {["1h", "24h", "7d", "30d"].map((r) => (
                <button key={r} onClick={() => setRange(r)} className={clsx("rounded-md px-2.5 py-1 text-xs font-medium", range === r ? "bg-zinc-800 text-white" : "text-zinc-500 hover:text-zinc-300")}>
                  {r}
                </button>
              ))}
            </div>
            {readOnly && (
              <Link to="/settings" className="btn btn-primary">
                <KeyRound className="h-3.5 w-3.5" /> Get API key
              </Link>
            )}
          </div>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b border-zinc-800 px-3 py-1.5 md:hidden">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => clsx("whitespace-nowrap rounded-md px-2.5 py-1 text-xs", isActive ? "bg-zinc-800 text-white" : "text-zinc-400")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <main className="min-h-0 flex-1 overflow-auto px-5 py-5">
          <div className="mx-auto max-w-[1400px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
