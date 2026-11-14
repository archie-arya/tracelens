# TraceLens docs

| Doc | Read it when you want to |
|---|---|
| [Main README](../README.md) | run TraceLens locally, instrument your app, see features and configuration |
| [REST API](api.md) | send data from any language, or script TraceLens: ingestion, traces, scores, evals, datasets, alerts, optimizer, projects |
| [Cost Optimizer](cost-optimizer.md) | find LLM steps that could run on cheaper models, and how replays, scoring and recommendations work |
| [Extending](extending.md) | add evaluators, storage backends, exporters, SDK integrations, pricing, redaction, judge models, and optimizer providers, scorers, samplers and policies |
| [Public demo](../demo/README.md) | understand or refresh the GitHub Pages demo |
| [Deploying](../deploy/README.md) | run one shared instance for a team (Docker, Fly.io, Render) |

## Repository layout

```
server/         FastAPI server: ingestion, storage, evals, alerts, Cost Optimizer (tracelens_server/optimizer)
sdk/python/     zero-dependency Python SDK, auto-instrumentation, `tracelens` CLI
sdk/js/         TypeScript/JavaScript SDK
dashboard/      React dashboard (also builds the static demo)
examples/       runnable examples, numbered from simplest to most advanced
demo/           demo traffic generator, snapshot exporter and the published snapshot
deploy/         optional Fly.io and Render configs for a shared instance
docs/           these docs
scripts/        run_local.sh: one-command local start
```
