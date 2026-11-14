<div align="center">

<img src="dashboard/public/favicon.svg" width="72" alt="TraceLens logo" />

# TraceLens

**Open-source, self-hostable LLM observability. Trace every prompt, token, dollar and failure, and score quality automatically with LLM-as-judge evals.**

Plug-and-play SDKs · OpenTelemetry native · runs on SQLite or Postgres · free LLM judges (Gemini, Groq, Ollama)

[**Live demo →**](https://archie-arya.github.io/tracelens/) · [Quickstart](#-2-minute-quickstart) · [Integrations](#-integrations) · [Self-host](#-self-hosting) · [Extend it](docs/extending.md) · [API](docs/api.md)

![License: MIT](https://img.shields.io/badge/license-MIT-indigo)
![Python SDK: zero deps](https://img.shields.io/badge/python%20sdk-zero%20deps-22d3ee)
![Tests: pytest + node:test](https://img.shields.io/badge/tests-pytest%20%2B%20node%3Atest-green)

</div>

---

TraceLens is a lightweight alternative to LangSmith and Langfuse that you can run anywhere with a single container. Add two lines to any Python app and every OpenAI, Groq, Gemini, Anthropic, LiteLLM or LangChain call shows up as a **nested trace**, with inputs and outputs, token usage, cost, latency, time-to-first-token and errors. Then let **evaluators** score each answer for relevance, faithfulness (hallucination), toxicity or your own rubric, and get a **webhook alert** when quality drops or costs spike.

```python
import tracelens
tracelens.init(api_key="tl-sk-...", host="http://localhost:8000")   # that's it: LLM calls are now traced
```

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
| **Alerts** | Rules on cost, error rate, latency, volume or average score over a sliding window. Alerts go to a Slack- or Discord-compatible webhook, with cooldowns. |
| **Feedback** | `tracelens.score("user_feedback", True)` from your app, or via the REST API. |
| **Production-safe SDK** | Zero dependencies. Export is async, batched and non-blocking, with retry and backoff, a bounded queue and a circuit breaker. Includes sampling, a PII redaction hook and a **no-op mode** when no key is set or the server is down. |
| **Self-hostable** | One container. SQLite by default and Postgres for scale, behind a swappable storage interface. Multi-project API keys, a public read-only demo mode and a Langfuse-compatible ingestion endpoint. |

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

## 🚀 2-minute quickstart

**1. Run the server** (pick one):

```bash
# a) Docker: SQLite, single container
docker build -t tracelens . && docker run -p 8000:8000 -v tracelens:/data -e TRACELENS_INIT_API_KEY=tl-sk-local-dev -e GEMINI_API_KEY=$GEMINI_API_KEY tracelens

# b) docker compose: Postgres
cp .env.example .env && docker compose up -d

# c) Plain Python
pip install ./server && TRACELENS_INIT_API_KEY=tl-sk-local-dev tracelens-server
```

Open <http://localhost:8000>. The dashboard is served by the same process. Use the key you set, or create a project under **Settings** to get one.

**2. Install the SDK and trace your app:**

```bash
pip install "git+https://github.com/archie-arya/tracelens#subdirectory=sdk/python"
export TRACELENS_API_KEY=tl-sk-local-dev TRACELENS_HOST=http://localhost:8000
```

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

**3. Add evals.** Under **Evaluations → Online eval rules**, add `llm_relevance`, or use the API:

```bash
curl -X POST localhost:8000/api/v1/eval-rules -H "Authorization: Bearer $TRACELENS_API_KEY" \
  -H "Content-Type: application/json" -d '{"evaluator":"llm_faithfulness","sample_rate":0.5}'
```

Every new trace now gets a faithfulness score with the judge's reasoning, using a free Gemini key by default.

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

## 🧩 Extensibility

Everything is a plugin point. See **[docs/extending.md](docs/extending.md)** for details.

* **Custom evaluators**: subclass `Evaluator`, decorate with `@register_evaluator`, and load with `TRACELENS_PLUGINS=my_module` or a `tracelens.evaluators` entry point.
* **Storage backends**: implement `Storage` and set `TRACELENS_STORAGE=pkg.module:MyStorage`.
* **SDK exporters**: subclass `Exporter` to send to Kafka, S3, stdout or another vendor, side by side with TraceLens.
* **Integrations**: `tracelens.register_integration("mylib", "my_pkg.tracelens_mylib")`, a module exposing `instrument()`.
* **Pricing**: `tracelens.pricing.register(...)` / `set_calculator(fn)` in the SDK, or `TRACELENS_PRICING_FILE` on the server.
* **Judge models**: any provider/model chain through environment variables.

## 🏠 Self-hosting

| Variable | Default | |
|---|---|---|
| `TRACELENS_DATABASE_URL` | `sqlite:///./data/tracelens.db` | or `postgresql://user:pass@host/db` |
| `TRACELENS_INIT_API_KEY` | random, printed in logs | fixed key for the `default` project |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENAI_API_KEY` + `OPENAI_BASE_URL` / `OLLAMA_BASE_URL` | – | judge providers |
| `TRACELENS_JUDGE_MODELS` | Gemini flash-lite chain | `provider:model,...` fallback order |
| `TRACELENS_ALLOW_SIGNUP` | `true` | anyone can create a project from the dashboard |
| `TRACELENS_PUBLIC_DEMO` / `TRACELENS_DEMO_PROJECT` | `true` / `demo` | anonymous read-only access to one project |
| `TRACELENS_ADMIN_TOKEN` | – | enables `GET /api/v1/projects` |
| `TRACELENS_PLUGINS`, `TRACELENS_STORAGE`, `TRACELENS_PRICING_FILE` | – | extensions |
| `TRACELENS_LLM_EVALS_PER_HOUR` | `300` | per-project judge rate limit |

Deploy configs are included for **Docker / docker compose**, **Fly.io** (`fly.toml`) and **Render** (`render.yaml`, free plan). The dashboard can also be hosted statically (GitHub Pages) and pointed at any server from **Settings → Server**.

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
