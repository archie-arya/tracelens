# Public demo

The read-only demo at <https://archie-arya.github.io/tracelens/> is the normal dashboard, built in static mode
(`VITE_STATIC_DEMO=1`) and fed from JSON files in [`snapshot/`](snapshot), exported from a real TraceLens server.

| File | What it does |
|---|---|
| `seed.py` | Generates realistic traffic on the free Gemini tier (RAG bot, tool-calling agent, ticket classifier, sessions, a fallback, an experiment, LangChain and OpenTelemetry examples) |
| `snapshot.py` | Exports the demo project (traces, metrics, scores, datasets, alerts, Cost Optimizer runs) to `snapshot/` |
| `snapshot/` | The exported data. `.github/workflows/pages.yml` builds and publishes the dashboard with it on every push |

Refresh only the agent data (tool-calling traces for the optimizer's tool-call evaluation), then run an agent optimizer run:

```bash
python demo/seed.py --only agent --repeat 2 --minutes 12
tracelens optimize --step "billing-agent / gemini.chat" --allow-tool-steps --candidates gemini:gemma-4-26b-a4b-it,gemini:gemini-flash-lite-latest --sample-size 12
```

Refresh it:

```bash
TRACELENS_API_KEY=<demo project key> TRACELENS_HOST=http://localhost:8000 GEMINI_API_KEY=... python demo/seed.py --minutes 30
python demo/snapshot.py http://localhost:8000 demo/snapshot     # the server needs TRACELENS_PUBLIC_DEMO=true
git add demo/snapshot && git commit -m "Refresh demo snapshot" && git push
```
