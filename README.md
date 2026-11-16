<div align="center">

<img src="dashboard/public/favicon.svg" width="72" alt="TraceLens logo" />

# TraceLens

**Local-first, open-source LLM observability. Run it on your own machine, point your app at `localhost`, and see every prompt, token, dollar and failure, with LLM-as-judge evals on top.**

Your traces stay on your machine · 2-line SDK · OpenTelemetry native · SQLite by default, Postgres optional · free LLM judges (Gemini, Groq, or fully local with Ollama)

[**Run it locally →**](#-run-it-locally-2-minutes) · [See it in action (read-only demo)](https://archie-arya.github.io/tracelens/) · [Integrations](#-integrations) · [Extend it](docs/extending.md) · [API](docs/api.md)

[![CI](https://github.com/archie-arya/tracelens/actions/workflows/ci.yml/badge.svg)](https://github.com/archie-arya/tracelens/actions/workflows/ci.yml)
![License: MIT](https://img.shields.io/badge/license-MIT-indigo)
![Python SDK: zero deps](https://img.shields.io/badge/python%20sdk-zero%20deps-22d3ee)

</div>

---

TraceLens is a lightweight, self-hosted alternative to LangSmith and Langfuse. There is no account and no cloud: you run one small server next to your app, and every OpenAI, Groq, Gemini, Anthropic, LiteLLM or LangChain call shows up as a **nested trace**, with inputs and outputs, token usage, cost, latency, time-to-first-token and errors. **Evaluators** then score each answer for relevance, faithfulness (hallucination), toxicity or your own rubric, and a **webhook alert** fires when quality drops or costs spike. The **Cost Optimizer** replays your real prompts on cheaper models and tells you which steps can switch, and what that does to cost, quality and latency.

Everything is stored in a SQLite file on your disk (`./data/tracelens.db`). Nothing is sent anywhere else, except the judge prompts if you turn on LLM evals with a cloud model. Use Ollama to keep even those local.

## 🚀 Run it locally (2 minutes)

**1. Start the server on your machine.** Pick one:

```bash
git clone https://github.com/archie-arya/tracelens && cd tracelens

# a) One script, no Docker (Python 3.9+, plus Node 18+ for the dashboard)
./scripts/run_local.sh

# b) The same thing, by hand
(cd dashboard && npm ci && VITE_OUT_DIR=../server/tracelens_server/static npx vite build)   # build the dashboard once
python3 -m venv .venv && .venv/bin/pip install ./server
TRACELENS_INIT_API_KEY=tl-sk-local-dev .venv/bin/tracelens-server --data-dir ./data

# c) Docker: Postgres plus the server, data in a local volume
cp .env.example .env && docker compose up -d
```

Open **<http://localhost:8000>**. The dashboard and the API are served by the same process, bound to `127.0.0.1` by default. Your API key is `tl-sk-local-dev` (`tl-sk-local-dev-change-me` with docker compose), or create more projects and keys under **Settings**.

**2. Point your app at it.**

```bash
pip install "git+https://github.com/archie-arya/tracelens#subdirectory=sdk/python"
```

```python
import tracelens
tracelens.init(api_key="tl-sk-local-dev", host="http://localhost:8000")   # that's it: LLM calls are now traced
```

Or leave the code at `tracelens.init()` and set `TRACELENS_API_KEY` / `TRACELENS_HOST` in the environment. A fuller example:

```python
import tracelens
tracelens.init()                       # auto-instruments every supported LLM SDK that's installed

from openai import OpenAI
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key="...")   # or OpenAI, Ollama, Gemini...

@tracelens.observe()                   # optional: group multiple steps into one trace
def answer(question: str, user_id: str) -> str:
    tracelens.update_current_trace(user_id=user_id, tags=["prod"])
    docs = search(question)            # decorate with @tracelens.observe(type="retrieval")
    r = client.chat.completions.create(model="llama-3.1-8b-instant",
                                       messages=[{"role": "user", "content": f"{docs}\n\n{question}"}])
    return r.choices[0].message.content
```

No key and no server yet? `python examples/01_quickstart.py` runs without any LLM key. If the server is down, the SDK quietly turns into a no-op and never breaks your app.

**3. Add evals (optional).** Give the server a judge model: a free Gemini key (`GEMINI_API_KEY`, from <https://aistudio.google.com/apikey>), a Groq key, or a fully local one with `TRACELENS_JUDGE_MODELS=ollama:llama3.1`. Then add a rule under **Evaluations → Online eval rules**, or use the API:

```bash
curl -X POST localhost:8000/api/v1/eval-rules -H "Authorization: Bearer tl-sk-local-dev" \
  -H "Content-Type: application/json" -d '{"evaluator":"llm_faithfulness","sample_rate":0.5}'
```

Every new trace now gets a faithfulness score with the judge's reasoning.

## 👀 See it in action

**[archie-arya.github.io/tracelens](https://archie-arya.github.io/tracelens/)** is a read-only snapshot of a real TraceLens instance. It shows a Gemini RAG bot, a tool-calling agent, a ticket classifier, sessions, experiments and live LLM-judge scores. It's static, so nothing you do there is saved. Run it locally to trace your own app.

## ✨ Features

| | |
|---|---|
| **Tracing** | Traces → spans → generations, with tool, retrieval, agent, chain, embedding and event types. Nested automatically through `contextvars` (thread- and asyncio-safe). Also supports sessions, users, tags, metadata, releases and environments. |
| **Auto-instrumentation** | OpenAI SDK and **every OpenAI-compatible API** (Groq, Together, OpenRouter, Fireworks, DeepSeek, Ollama, vLLM, Azure, Gemini's OpenAI endpoint). Also Google Gen AI (`google-genai`), Anthropic and LiteLLM, plus a LangChain/LangGraph callback handler. Sync, async and streaming. |
| **OpenTelemetry** | `POST /v1/traces` accepts OTLP/HTTP in protobuf or JSON, so **any language** works. It maps the GenAI semantic conventions (`gen_ai.*`), OpenInference (`llm.*`) and OpenLLMetry attributes. |
| **Cost & usage** | Built-in, pluggable pricing table covering OpenAI, Anthropic, Gemini, Groq and open models. You can override it at runtime or with a JSON file. Costs are computed in the SDK, or on the server for OTel spans. |
| **Metrics** | Volume, cost, tokens, p50/p95/p99 latency, error rate and TTFT over time, broken down by model, workflow, user and tag. |
| **Evaluations** | LLM-as-judge (relevance, faithfulness/hallucination, toxicity, correctness, custom rubric) and heuristics (JSON validity, regex, length, keywords, exact match, similarity, latency budget). You can run them **online** with sampling rules, in batches, from a playground or on a single trace. Write your own with one small class. |
| **Datasets & experiments** | Golden datasets plus `run_experiment()` to compare prompt or model versions side by side with scores, latency and cost. |
| **Cost Optimizer** | Replays sampled production prompts, per step, on cheaper candidate models (Gemini, Groq, Ollama, any OpenAI-compatible API, LiteLLM), scores them against the original outputs, and recommends switches with cost, quality and latency deltas, a confidence level and side-by-side examples. Agent steps can be included: they are scored on the tool calls the candidate makes, and tools are never executed. Spend is capped and replays are cached. |
| **Alerts** | Rules on cost, error rate, latency, volume or average score over a sliding window. Alerts go to a Slack- or Discord-compatible webhook, with cooldowns. |
| **Feedback** | `tracelens.score("user_feedback", True)` from your app, or via the REST API. |
| **Production-safe SDK** | Zero dependencies. Export is async, batched and non-blocking, with retry and backoff, a bounded queue and a circuit breaker. Includes sampling, a PII redaction hook and a **no-op mode** when no key is set or the server is down. |
| **Local-first** | Runs on your laptop with one command and keeps data in a local SQLite file. Also ships as one container. SQLite by default and Postgres for scale, behind a swappable storage interface. Multi-project API keys, a public read-only demo mode and a Langfuse-compatible ingestion endpoint. |

## 🏗 Architecture

```mermaid
flowchart LR
  subgraph Your apps
    A1[Python app<br/>tracelens SDK] 
    A2[JS / TS app<br/>tracelens-js]
    A3[Any language<br/>OpenTelemetry OTLP]
    A4[LangChain / LangGraph<br/>callback handler]
  end
  A1 & A2 & A4 -- "batched JSON<br/>POST /api/v1/ingest" --> API
  A3 -- "OTLP/HTTP protobuf·json<br/>POST /v1/traces" --> OTLP[OTLP → GenAI<br/>semantic mapper]
  OTLP --> API
  subgraph TraceLens server  [FastAPI]
    API[Ingestion & auth<br/>project API keys] --> ST[(Storage interface<br/>SQLite · Postgres · custom)]
    API -- new traces --> EV[Eval engine<br/>online rules · jobs · experiments]
    EV --> J[Judge LLM chain<br/>Gemini → Groq → Ollama → OpenAI-compatible]
    EV --> ST
    AL[Alert loop<br/>every 60 s] --> ST
    AL -- webhook --> W[Slack / Discord / any URL]
    Q[Query & metrics API] --> ST
  end
  UI[React dashboard<br/>overview · traces · waterfall · evals · datasets · alerts] --> Q
```

* **SDK side:** calls are recorded in-process. On span end, an event goes onto a bounded in-memory queue, and a daemon thread batches events to the server. Your request path never waits on the network.
* **Server side:** events are **upserts**, so they're idempotent and out-of-order safe. Spans can arrive before their trace, and retries never duplicate. Trace aggregates (cost, tokens, latency, models, status) are recomputed per batch. Completed traces trigger matching eval rules in the background.
* **Storage:** every query goes through [`Storage`](server/tracelens_server/storage/base.py). The default `SQLStorage` runs on SQLite (WAL) or Postgres. Plug in ClickHouse, DuckDB or anything else with `TRACELENS_STORAGE=module:Class`.

## 🔌 Integrations

| Library / protocol | How |
|---|---|
| OpenAI + any OpenAI-compatible API (Groq, Together, OpenRouter, Ollama, vLLM, LM Studio, DeepSeek, Fireworks, Azure, Gemini OpenAI endpoint) | automatic after `tracelens.init()` |
| Google Gen AI SDK (`from google import genai`) | automatic |
| Anthropic | automatic |
| LiteLLM (100+ providers) | automatic |
| LangChain / LangGraph | `config={"callbacks": [TraceLensCallbackHandler()]}` |
| Any framework / custom code | `@tracelens.observe()`, `with tracelens.span(...)`, `with tracelens.generation(...)` |
| OpenTelemetry (any language, OpenLLMetry, OpenInference) | OTLP/HTTP → `{host}/v1/traces`, header `Authorization: Bearer <key>` |
| JavaScript / TypeScript | [`sdk/js`](sdk/js): `tl.trace()`, `tl.span()`, `wrapOpenAI()`. Install: `npm i https://github.com/archie-arya/tracelens/releases/download/v0.1.0/tracelens-js-0.1.0.tgz` |
| Langfuse SDKs | point them at TraceLens: `/api/public/ingestion` is accepted |
| Plain HTTP | `POST /api/v1/ingest`. See [docs/api.md](docs/api.md) |

Runnable examples are in [`examples/`](examples): quickstart (no key needed), a **RAG bot on Gemini**, a **tool-calling agent** over the OpenAI client, LangChain, raw OpenTelemetry, **datasets & experiments**, Anthropic/LiteLLM, and the script that seeds the public demo.

### SDK reference (Python)

```python
tracelens.init(api_key=None, host=None, *,          # env: TRACELENS_API_KEY / TRACELENS_HOST
               auto_instrument=True,                # or ["openai", "anthropic", "google_genai", "litellm"]
               sample_rate=1.0,                     # trace-level sampling
               redact_pii=False, mask=None,         # built-in PII scrubbing and/or your own fn(value) -> value
               exporters=None,                      # extra exporters: ConsoleExporter(), FileExporter("t.jsonl"), custom
               release=None, environment=None, default_tags=None, default_metadata=None,
               capture_io=True, enabled=None, flush_interval=1.0, batch_size=100, max_queue=10_000)

@tracelens.observe(name=None, type="span"|"generation"|"tool"|"retrieval"|"agent"|"chain", capture_input=True, capture_output=True)
with tracelens.trace("name", user_id=..., session_id=..., tags=[...], metadata={...}) as t: ...
with tracelens.span("name", type="tool", input=...) as s: s.update(output=..., metadata=...)
with tracelens.generation("llm", model="my-model", input=msgs) as g: g.update(output=text, usage={"input": 10, "output": 5})
tracelens.update_current_trace(...); tracelens.update_current_observation(...)
tracelens.score("user_feedback", True | 0.8 | "label", trace_id=None, comment=None)
tracelens.get_current_trace_id(); tracelens.flush(); tracelens.shutdown()
tracelens.pricing.register("my-finetune", input=0.5, output=1.5)      # USD per 1M tokens
```

## 🧪 Evaluations

| Evaluator | Kind | Score | Needs |
|---|---|---|---|
| `llm_relevance` | LLM judge | relevance 0–1 | input, output |
| `llm_faithfulness` | LLM judge (hallucination) | faithfulness 0–1 | retrieved context (`type="retrieval"` spans or `metadata.context`) |
| `llm_toxicity` | LLM judge | toxicity 0–1 (lower is better) | output |
| `llm_correctness` | LLM judge | correctness 0–1 | expected output (datasets) |
| `llm_rubric` | LLM judge | your score name | plain-English criteria |
| `json_valid`, `regex`, `length`, `contains`, `exact_match`, `similarity`, `latency_budget` | heuristic | 0–1 | – |

Judges run server-side on a **fallback chain** (`TRACELENS_JUDGE_MODELS`, default `gemini:gemini-flash-lite-latest,gemini:gemini-3.1-flash-lite,...`). Providers include `gemini`, `groq`, `ollama` and `openai` (any OpenAI-compatible base URL). When a model is rate-limited or removed, the next one in the chain is used.

```python
from tracelens.experiments import create_dataset, run_experiment
create_dataset("support-golden", [{"input": "Refund window?", "expected_output": "30 days"}])
run_experiment("support-golden", my_app, run_name="v2-rag", evaluators=["similarity", "llm_correctness"])
```

## 💸 Cost Optimizer

"Could this step run on a cheaper model?" Answered with your own traffic instead of a benchmark:

```bash
pip install -e sdk/python            # gives you the `tracelens` CLI
export TRACELENS_API_KEY=tl-sk-...   # TRACELENS_HOST defaults to http://localhost:8000
tracelens steps                      # LLM steps by spend, and how many calls can be replayed
tracelens optimize --step "support-rag / gemini.generate_content" \
                   --candidates gemini:gemma-4-26b-a4b-it,gemini:gemini-3.1-flash-lite --sample-size 20 --max-spend 0.50
```

```
→ SWITCH: gemini:gemma-4-26b-a4b-it is 100% cheaper with -2.0% relative quality and +1786% latency over 10 replays (threshold -5%).  [confidence: medium, n=10]
```

The same is available from the dashboard (*Cost Optimizer*: pick a step, add candidates, run), from Python (`tracelens.optimize(steps=[...], candidates=[...])`), and from `POST /api/v1/optimizer/runs`. Free candidates work out of the box: the Gemini free tier with `GEMINI_API_KEY`, or local Ollama models, which always cost $0. Providers, scorers, samplers and policies are all plugins.

Results are **estimates**: list prices, and quality relative to the original output, on a sample. Only `generation` calls are replayed (tool-calling ones only with `--allow-tool-steps`), and inputs can be redacted first (`--redact`). Read **[docs/cost-optimizer.md](docs/cost-optimizer.md)** for the design and caveats.

### Agent / tool-call evaluation

For agents, the final answer is only part of the picture: a cheaper model can reach the same answer while calling the wrong tool, skipping a lookup, or passing different arguments. Since v0.2 the SDK records the tool definitions offered to the model, the tool calls it returned, and the tool results fed back on every LLM span (OpenAI-compatible clients, Anthropic, Gemini and LiteLLM), and the optimizer can replay those steps:

```bash
tracelens optimize --step "billing-agent / gemini.chat" --allow-tool-steps \
                   --candidates gemini:gemini-flash-lite-latest,groq:llama-3.1-8b-instant --sample-size 20
```

* Each recorded LLM call is replayed **once**, with the recorded messages, the recorded tool results and the same tool schemas. The candidate's tool calls are compared, never executed, and nothing is sent back to it afterwards.
* The `tool_call_match` scorer (chosen by `auto` for these steps) checks the call-vs-answer decision, tool names, order and arguments (`args_mode`: `lenient` by default, `strict` for exact JSON, or `partial`; `ignore_args` skips free-text arguments such as search queries). It reports each sub-score, and final-answer turns are judged on their text. `judge_weight` blends a text score into tool-call turns.
* Each candidate gets a **tool agreement** rate: the share of replays with the same calls, names, order and arguments. `quality_threshold` will only recommend a switch at `min_tool_agreement` (0.95) or above. The run page lists mismatched calls next to the originals, and a per-trace trajectory view compares the original run's tool sequence with each candidate's decision at every step.

It's off by default, so existing runs behave exactly as before. Traces recorded with SDK 0.1 don't have tool schemas, so their tool steps are still skipped. Details: [docs/cost-optimizer.md](docs/cost-optimizer.md#tool-calling-steps-agents).

## 🧩 Extensibility

Everything is a plugin point. See **[docs/extending.md](docs/extending.md)** for details.

* **Custom evaluators**: subclass `Evaluator`, decorate with `@register_evaluator`, and load with `TRACELENS_PLUGINS=my_module` or a `tracelens.evaluators` entry point.
* **Storage backends**: implement `Storage` and set `TRACELENS_STORAGE=pkg.module:MyStorage`.
* **SDK exporters**: subclass `Exporter` to send to Kafka, S3, stdout or another vendor, side by side with TraceLens.
* **Integrations**: `tracelens.register_integration("mylib", "my_pkg.tracelens_mylib")`, a module exposing `instrument()`.
* **Pricing**: `tracelens.pricing.register(...)` / `set_calculator(fn)` in the SDK, or `TRACELENS_PRICING_FILE` on the server.
* **Judge models**: any provider/model chain through environment variables.
* **Cost Optimizer**: `@register_provider`, `@register_scorer`, `@register_sampler` and `@register_policy`, loaded the same way (`TRACELENS_PLUGINS` or a `tracelens.plugins` entry point). Every evaluator is also usable as an optimizer scorer (`eval:<name>`).

All docs: **[docs/](docs/README.md)**.

## ⚙️ Configuration

All settings are environment variables. The defaults are tuned for a single developer running locally.

| Variable | Default | |
|---|---|---|
| `TRACELENS_DATABASE_URL` | `sqlite:///./data/tracelens.db` | or `postgresql://user:pass@host/db` (`--data-dir` sets the SQLite folder) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | set `HOST=0.0.0.0` to accept traffic from other machines |
| `TRACELENS_INIT_API_KEY` | random, printed in logs | fixed key for the `default` project |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENAI_API_KEY` + `OPENAI_BASE_URL` / `OLLAMA_BASE_URL` | – | judge providers and Cost Optimizer candidates (also `OPENROUTER_`, `TOGETHER_`, `DEEPSEEK_`, `FIREWORKS_`, `MISTRAL_API_KEY`) |
| `TRACELENS_JUDGE_MODELS` | Gemini flash-lite chain | `provider:model,...` fallback order |
| `TRACELENS_ALLOW_SIGNUP` | `true` | anyone can create a project from the dashboard |
| `TRACELENS_PUBLIC_DEMO` / `TRACELENS_DEMO_PROJECT` | `false` / `demo` | anonymous read-only access to one project |
| `TRACELENS_ADMIN_TOKEN` | – | enables `GET /api/v1/projects` |
| `TRACELENS_PLUGINS`, `TRACELENS_STORAGE`, `TRACELENS_PRICING_FILE` | – | extensions |
| `TRACELENS_LLM_EVALS_PER_HOUR` | `300` | per-project judge rate limit |
| `TRACELENS_OPTIMIZER_REDACT` / `TRACELENS_OPTIMIZER_RPM_<PROVIDER>` | `false` / per provider | always redact replayed prompts, and per-provider request rate (e.g. `TRACELENS_OPTIMIZER_RPM_GEMINI=12`) |


### Optional: a shared team instance

Running locally is the default. If your team wants one shared server, run the same Docker image (or `docker compose`) on any machine or VM you control, set `HOST=0.0.0.0`, Postgres and a real `TRACELENS_INIT_API_KEY`, and point every app's `TRACELENS_HOST` at it. [`deploy/`](deploy) has `fly.toml` and `render.yaml` starting points for managed hosts. Put it behind HTTPS before sending production data.

## ❓ FAQ

**What is "the TraceLens server"?**
A program you run yourself: on your laptop, or on a machine your team controls. It receives traces from your app, stores them in SQLite (or Postgres), serves the dashboard, and runs the LLM judges and the Cost Optimizer. There is no central TraceLens cloud and no account. Each developer or team runs their own server.

**TraceLens key vs. my OpenAI or Gemini key: which is which?**
They are separate. Your **LLM provider key** (OpenAI, Gemini, Groq...) stays in your app, as it is today. TraceLens never needs it to record traces. The **TraceLens project key** (`tl-sk-...`) only tells your server which project a trace belongs to. The server needs a model key only for LLM judges and the Cost Optimizer, and only on the server: for example a free `GEMINI_API_KEY`. With Ollama you need no key at all.

**How do I get a TraceLens key?**
Start your server. With `./scripts/run_local.sh` (or the manual command above), the key `tl-sk-local-dev` works from the first start. With docker compose it is `tl-sk-local-dev-change-me`. If you start `tracelens-server` without `TRACELENS_INIT_API_KEY`, a random key is printed in the log. To create more projects and keys, use **Settings** in your own server's dashboard.

**Can I get a key from the public demo?**
No. [The demo](https://archie-arya.github.io/tracelens/) is a static, read-only snapshot hosted on GitHub Pages. There is no server behind it, so it can't receive traces or issue keys. Run your own server (two minutes, above) to trace your app.

## 🛠 Development

```bash
cd server && uv venv && uv pip install -e ".[dev]" && pytest          # API, ingestion, OTLP, evals, alerts
cd sdk/python && uv venv && uv pip install -e ".[dev]" openai anthropic langchain-core && pytest
cd sdk/js && npm i && npm run build && npm test
cd dashboard && npm i && npm run dev                                   # proxies /api to localhost:8000
```

## Roadmap

Prompt management and versioning · ClickHouse storage backend · human annotation queues · cost budgets per user · Go and Java SDK helpers.

## License

MIT © Archie Arya
