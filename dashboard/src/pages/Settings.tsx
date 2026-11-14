import { KeyRound, LogOut, Plus, Server, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, getKey, getServer, ingestHost, isStatic, setKey, setServer, STATIC_BUILD, useApi } from "../api";
import { Card, CopyButton, ErrorBox, PageHeader, Tabs } from "../components/ui";
import { fmtAgo } from "../format";
import { useApp } from "../state";

const REPO = "https://github.com/archie-arya/tracelens";

function snippets(key: string, host: string): Record<string, { label: string; code: string }> {
  return {
    python: {
      label: "Python (2 lines)",
      code: `pip install "git+${REPO}#subdirectory=sdk/python"

import tracelens
tracelens.init(api_key="${key}", host="${host}")
# Every OpenAI / Groq / Gemini / Anthropic / LiteLLM call is now traced.

@tracelens.observe()            # optional: group steps into one trace
def answer(question: str) -> str:
    docs = retrieve(question)     # @observe(type="retrieval") for nested spans
    return llm(question, docs)`,
    },
    openai: {
      label: "OpenAI-compatible",
      code: `import tracelens, openai
tracelens.init(api_key="${key}", host="${host}")

# Works for OpenAI, Groq, Together, OpenRouter, Ollama, vLLM, Gemini's OpenAI endpoint...
client = openai.OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_KEY)
client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": "hi"}])`,
    },
    gemini: {
      label: "Gemini",
      code: `import tracelens
from google import genai
tracelens.init(api_key="${key}", host="${host}")

client = genai.Client()
with tracelens.trace("summarize", user_id="u_42", tags=["prod"]):
    client.models.generate_content(model="gemini-flash-lite-latest", contents="Summarize ...")`,
    },
    langchain: {
      label: "LangChain",
      code: `import tracelens
from tracelens.integrations.langchain import TraceLensCallbackHandler
tracelens.init(api_key="${key}", host="${host}")

chain.invoke({"question": "..."}, config={"callbacks": [TraceLensCallbackHandler(user_id="u_42")]})`,
    },
    otel: {
      label: "OpenTelemetry (any language)",
      code: `# Any OTLP/HTTP exporter works - GenAI semantic conventions (gen_ai.*) & OpenInference are understood.
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${host}/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer ${key}"

# Python shortcut:
from tracelens.otel import configure_otel
configure_otel(api_key="${key}", host="${host}")`,
    },
    js: {
      label: "TypeScript / JS",
      code: `import { TraceLens, wrapOpenAI } from "./tracelens";   // sdk/js (zero deps)
const tl = new TraceLens({ apiKey: "${key}", host: "${host}" });
const openai = wrapOpenAI(new OpenAI(), tl);

await tl.trace("chat", async (t) => {
  t.update({ userId: "u_42" });
  return openai.chat.completions.create({ model: "gpt-4o-mini", messages });
});`,
    },
    curl: {
      label: "REST / cURL",
      code: `curl -X POST ${host}/api/v1/ingest \\
  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\
  -d '{"batch":[{"type":"trace","body":{"id":"t1","name":"hello","input":"hi","output":"hello!"}},
       {"type":"generation","body":{"id":"g1","trace_id":"t1","name":"llm","model":"gpt-4o-mini",
        "usage":{"input":12,"output":3},"input":"hi","output":"hello!"}}]}'`,
    },
  };
}

export function Onboarding({ compact }: { compact?: boolean }) {
  const [tab, setTab] = useState("python");
  const key = getKey() || "tl-sk-...";
  const s = snippets(key, ingestHost());
  return (
    <Card title={compact ? "No traces yet — send your first one" : "Integrate in 2 minutes"} className="mb-4">
      <Tabs value={tab} onChange={setTab} tabs={Object.entries(s).map(([id, v]) => ({ id, label: v.label }))} />
      <div className="relative mt-3">
        <CopyButton text={s[tab].code} className="absolute right-2 top-2" />
        <pre className="overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-zinc-300">{s[tab].code}</pre>
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const { project, refreshProject, readOnly } = useApp();
  const [server, setServerInput] = useState(getServer());
  const [name, setName] = useState("");
  const [existing, setExisting] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const hasKey = !!getKey();
  const { data: keys, reload } = useApi<any>(hasKey && !isStatic() ? "/api/v1/keys" : null, [project?.id]);

  const createProject = async () => {
    setErr(null);
    try {
      const r: any = await api("/api/v1/projects", { method: "POST", body: { name: name || "My project" }, key: null });
      setKey(r.api_key);
      setNewKey(r.api_key);
      refreshProject();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const useExisting = async () => {
    setErr(null);
    try {
      await api("/api/v1/project", { key: existing.trim() });
      setKey(existing.trim());
      setExisting("");
      refreshProject();
    } catch (e: any) {
      setErr(`Key rejected: ${e.message}`);
    }
  };

  return (
    <div>
      <PageHeader title="Settings & API keys" subtitle="Create a free project, connect your apps, manage keys." />
      {err && <div className="mb-3"><ErrorBox error={err} /></div>}
      {newKey && (
        <div className="mb-4 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4">
          <div className="text-sm font-semibold text-emerald-200">Your API key (shown once — saved in this browser)</div>
          <div className="mt-2 flex items-center gap-2 font-mono text-sm">
            {newKey} <CopyButton text={newKey} />
          </div>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-2"><Server className="h-4 w-4" /> Server</span>}>
          <p className="mb-3 text-sm text-zinc-400">
            {isStatic()
              ? "You're viewing a static snapshot of the demo project. Point the dashboard at any TraceLens server (self-hosted with docker compose, or a hosted instance) to create projects and send live traces."
              : `Connected to ${getServer() || window.location.origin}.`}
          </p>
          <div className="flex gap-2">
            <input className="input flex-1" placeholder={STATIC_BUILD ? "https://your-tracelens.example.com" : "(same origin)"} value={server} onChange={(e) => setServerInput(e.target.value)} />
            <button className="btn btn-ghost" onClick={() => (setServer(server || null), setKey(null), window.location.reload())}>Connect</button>
          </div>
          {getServer() && (
            <button className="mt-2 text-xs text-zinc-500 hover:text-zinc-300" onClick={() => (setServer(null), setKey(null), window.location.reload())}>
              Reset to default
            </button>
          )}
        </Card>
        <Card title={<span className="flex items-center gap-2"><KeyRound className="h-4 w-4" /> Project</span>}>
          {hasKey && !readOnly ? (
            <div className="space-y-3">
              <div className="text-sm">
                Signed in to <b>{project?.name}</b> <span className="font-mono text-xs text-zinc-500">({project?.slug})</span>
              </div>
              <button className="btn btn-ghost" onClick={() => (setKey(null), setNewKey(null))}>
                <LogOut className="h-3.5 w-3.5" /> Use public demo instead
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <div className="label mb-1">Create a new project (free, no signup)</div>
                <div className="flex gap-2">
                  <input className="input flex-1" placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} />
                  <button className="btn btn-primary" disabled={isStatic()} onClick={createProject}>
                    <Plus className="h-3.5 w-3.5" /> Create
                  </button>
                </div>
              </div>
              <div>
                <div className="label mb-1">…or use an existing API key</div>
                <div className="flex gap-2">
                  <input className="input flex-1 font-mono" placeholder="tl-sk-..." value={existing} onChange={(e) => setExisting(e.target.value)} />
                  <button className="btn btn-ghost" disabled={isStatic() || !existing} onClick={useExisting}>Use key</button>
                </div>
              </div>
              {isStatic() && <p className="text-xs text-amber-300/80">Connect a server first.</p>}
            </div>
          )}
        </Card>
      </div>

      {keys && (
        <Card title="API keys" className="mt-4" actions={<button className="btn btn-ghost" onClick={() => api("/api/v1/keys", { method: "POST", body: { name: `key-${Date.now() % 10000}` } }).then((r: any) => (setNewKey(r.api_key), reload()))}><Plus className="h-3.5 w-3.5" /> New key</button>}>
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800">
                {["Name", "Key", "Created", "Status", ""].map((h) => <th key={h} className="th">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {keys.data.map((k: any) => (
                <tr key={k.id} className="border-b border-zinc-800/60 last:border-0">
                  <td className="td">{k.name}</td>
                  <td className="td font-mono text-xs text-zinc-400">{k.display}</td>
                  <td className="td text-zinc-400">{fmtAgo(k.created_at)}</td>
                  <td className="td">{k.revoked ? <span className="text-rose-400">revoked</span> : <span className="text-emerald-400">active</span>}</td>
                  <td className="td text-right">
                    {!k.revoked && (
                      <button className="text-zinc-500 hover:text-rose-400" onClick={() => api(`/api/v1/keys/${k.id}`, { method: "DELETE" }).then(reload)}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <div className="mt-4">
        <Onboarding />
      </div>
    </div>
  );
}
