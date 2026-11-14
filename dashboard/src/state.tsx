import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, getKey, isStatic } from "./api";

export type Project = { id: string; name: string; slug: string; read_only: boolean; is_demo: boolean; allow_signup: boolean; judge_available: boolean };
type Ctx = { range: string; setRange: (r: string) => void; project: Project | null; projectError: string | null; refreshProject: () => void; readOnly: boolean; staticMode: boolean };

const AppCtx = createContext<Ctx>(null as any);

export function AppProvider({ children }: { children: ReactNode }) {
  const [range, setRangeState] = useState(localStorage.getItem("tl.range") || "24h");
  const [project, setProject] = useState<Project | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const refreshProject = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    const h = () => refreshProject();
    window.addEventListener("tl-auth", h);
    return () => window.removeEventListener("tl-auth", h);
  }, [refreshProject]);
  useEffect(() => {
    api<Project>("/api/v1/project")
      .then((p) => (setProject(p), setProjectError(null)))
      .catch((e) => (setProject(null), setProjectError(e.message)));
  }, [tick]);
  const setRange = (r: string) => {
    localStorage.setItem("tl.range", r);
    setRangeState(r);
  };
  const staticMode = isStatic();
  return (
    <AppCtx.Provider value={{ range, setRange, project, projectError, refreshProject, readOnly: staticMode || !!project?.read_only || !getKey(), staticMode }}>
      {children}
    </AppCtx.Provider>
  );
}

export const useApp = () => useContext(AppCtx);
