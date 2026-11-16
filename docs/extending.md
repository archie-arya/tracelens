# Extending TraceLens

TraceLens is built from small, documented interfaces. Everything below works without forking.

## 1. Custom evaluators (server)

```python
# my_evals.py
from tracelens_server.evals import Evaluator, EvalInput, EvalResult, ConfigField, register_evaluator

@register_evaluator
class MentionsCompetitor(Evaluator):
    name = "mentions_competitor"
    description = "1 if the answer mentions a competitor (lower is better)"
    higher_is_better = False
    config_fields = [ConfigField("names", "list", ["globex", "initech"], "Competitor names")]

    async def evaluate(self, inp: EvalInput, config: dict) -> EvalResult:
        text = str(inp.output).lower()
        hits = [n for n in config.get("names", []) if n in text]
        return EvalResult(score=1.0 if hits else 0.0, label="mentions" if hits else "clean", reason=f"found {hits}")
```

Load it with `TRACELENS_PLUGINS=my_evals`, or package it with an entry point:

```toml
[project.entry-points."tracelens.evaluators"]
mentions_competitor = "my_evals:MentionsCompetitor"
```

It then shows up in `GET /api/v1/evaluators`, the dashboard (with a config form generated from `config_fields`), online rules, batch runs and experiments.

`EvalInput` gives you `input`, `output`, `expected_output` (datasets), `context` (outputs of `retrieval` spans plus `metadata.context`), `metadata` and the full `trace`, including all observations. To build an LLM judge, subclass `tracelens_server.evals.llm_judge.LLMJudge` and set `rubric`. It reuses the configured judge model chain and its fallbacks.

## 2. Storage backends (server)

Implement the abstract methods of [`Storage`](../server/tracelens_server/storage/base.py) and point the server at it:

```bash
TRACELENS_STORAGE=my_pkg.clickhouse:ClickHouseStorage TRACELENS_DATABASE_URL=clickhouse://... tracelens-server
```

The constructor receives the database URL. `metrics()` has a portable default built on `metric_rows()`. Override it to push aggregation down into your database. The built-in `SQLStorage` supports SQLite and Postgres, plus any SQLAlchemy dialect that has JSON support.

## 3. Exporters (Python SDK)

```python
import tracelens
from tracelens import Exporter

class KafkaExporter(Exporter):
    def __init__(self, producer, topic): self.p, self.topic = producer, topic
    def export(self, events):            # called from a background thread with a batch
        for e in events:
            self.p.send(self.topic, e)    # raise to signal failure (counts toward the circuit breaker)

tracelens.init(exporters=[KafkaExporter(producer, "llm-traces")])   # TraceLens HTTP export is added too if api_key is set
```

Built-ins include `HttpExporter`, `ConsoleExporter` (debugging, or `TRACELENS_DEBUG=1`) and `FileExporter("traces.jsonl")` for offline capture.

## 4. New library integrations (Python SDK)

Write a module with `instrument() -> bool` and `uninstrument()`, then register it:

```python
tracelens.register_integration("mistralai", "my_pkg.tracelens_mistral")
tracelens.init()   # auto-instruments it with the others
```

Inside, use `tracelens.get_client().start_observation(name, type="generation", model=..., input=...)` and call `.end(output=..., usage={"input": n, "output": m})`. Parenting under the active trace or span happens automatically.

## 5. Pricing

* SDK: `tracelens.pricing.register("my-model", input=0.2, output=0.6)` (USD per 1M tokens, longest prefix match), or `tracelens.pricing.set_calculator(lambda model, inp, out: ...)`.
* Server (for OTel/REST data without cost): `TRACELENS_PRICING_FILE=/etc/tracelens/prices.json` with `{"my-model": [0.2, 0.6]}`.
* An explicit `cost` sent with an observation always wins.

## 6. PII redaction / masking

```python
from tracelens.redact import make_redactor
import re
mask = make_redactor(extra=[("EMPLOYEE_ID", re.compile(r"EMP-\d{6}"))])
tracelens.init(mask=mask)            # or redact_pii=True for the defaults (emails, phones, cards, keys, IPs, SSNs)
```

Masking runs in the SDK before data leaves your process.

## 7. Judge models

`TRACELENS_JUDGE_MODELS="groq:llama-3.3-70b-versatile,gemini:gemini-flash-lite-latest,ollama:llama3.1"` sets the order. On 429 or 5xx the judge moves to the next model, and models returning 404 are skipped for an hour. An evaluator can also override the chain per rule with `config.model_chain`.

## 8. Alerts

Webhooks get a JSON payload containing `text` and `content`, which Slack and Discord render directly, plus structured `rule`, `value` and `project_id` fields. To route alerts elsewhere, point the webhook at your own service: PagerDuty, email or an incident bot.

## 9. Cost Optimizer plugins

The [Cost Optimizer](cost-optimizer.md) is built from four registries. A plugin is a small class with a `name`, a `description` and optional `config_fields`, registered with a decorator. Load your module with `TRACELENS_PLUGINS=my_pkg.optimizer_plugins` (comma-separated), or ship it as a package with an entry point:

```toml
[project.entry-points."tracelens.plugins"]
my_plugins = "my_pkg.optimizer_plugins"     # imported at server start; the decorators do the registering
```

Each plugin then appears in `GET /api/v1/optimizer/plugins`, in the dashboard's run form, and in the CLI and SDK by name.

### Provider: where candidate models run

```python
from tracelens_server.optimizer import Provider, Completion, register_provider
from tracelens_server.optimizer.providers import OpenAICompatible

@register_provider                      # any OpenAI-compatible API: two attributes
class Cerebras(OpenAICompatible):
    name = "cerebras"
    description = "Cerebras (CEREBRAS_API_KEY)"
    base_url = "https://api.cerebras.ai/v1"
    key_env = "CEREBRAS_API_KEY"
    default_rpm = 30

@register_provider                      # anything else: implement complete()
class MyGateway(Provider):
    name = "gateway"
    local = False                       # True = always $0 (e.g. on-prem models)
    default_rpm = 120                   # override per run with "rpm" or TRACELENS_OPTIMIZER_RPM_GATEWAY

    def available(self) -> bool:
        return True

    async def complete(self, model, messages, params) -> Completion:
        # messages: OpenAI-style [{"role", "content"}]; params: temperature, top_p, max_tokens, stop, seed, json
        text, usage = await my_client.chat(model, messages, **params)
        return Completion(text=text, input_tokens=usage.inp, output_tokens=usage.out)   # cost=None -> pricing table
```

Use it as `gateway:my-model`. Rate limiting, retries on 429 and 5xx, timeouts, caching and the spend cap are handled for you. Raise an `httpx.HTTPStatusError` (or any exception) on failure.

To take part in tool-calling replays (`allow_tool_steps`), set `supports_tools = True`. `params["tools"]` then holds `[{"name", "description", "parameters"}]` (and possibly `params["tool_choice"]`), and `messages` can contain assistant `tool_calls` and `{"role": "tool", "tool_call_id", "name", "content"}` results. Return the model's calls as `Completion(tool_calls=[{"id", "name", "arguments"}])`, and never execute them. `OpenAICompatible` subclasses get this for free. Providers without it are skipped on tool steps, with a reason.

### Scorer: how a candidate output is compared with the original

```python
from tracelens_server.optimizer import Scorer, Comparison, register_scorer

@register_scorer
class SameLanguage(Scorer):
    name = "same_language"
    description = "1 if the candidate answers in the same language as the original"
    kind = "heuristic"                  # or "llm"

    def applicable(self, sample) -> bool:   # optional; "auto" falls back when False
        return bool(sample.output_text)

    async def compare(self, sample, candidate, config) -> Comparison:
        same = detect(sample.output_text) == detect(candidate.text)
        return Comparison(score=1.0 if same else 0.0, label="match" if same else "mismatch")
```

`score` is relative quality from 0 to 1, where 1 means as good as the original or better. `sample` contains the replayed `messages`, `params`, original `output_text`, tokens, cost, latency and any retrieval `context`. For tool-calling steps it also has `tool_step=True`, `tools` (definitions) and `tool_calls` (the original calls), and `candidate.tool_calls` holds the candidate's calls. `tracelens_server.optimizer.tools.compare_tool_calls(original, candidate, mode)` gives you the built-in sub-scores to build on. Every [evaluator](#1-custom-evaluators-server) is already a scorer as `eval:<name>`, so you often don't need a new scorer at all.

### Sampler: which generations get replayed

```python
from tracelens_server.optimizer import Sampler, register_sampler
from tracelens_server.optimizer.samplers import dedupe

@register_sampler
class MostExpensive(Sampler):
    name = "most_expensive"
    description = "The N costliest calls: where savings matter most"

    def select(self, samples, n, config):
        return sorted(dedupe(samples), key=lambda s: -(s.cost or 0))[:n]
```

### Policy: when to recommend a switch

```python
from tracelens_server.optimizer import Policy, register_policy
from tracelens_server.optimizer.policies import recommendation
from tracelens_server.evals import ConfigField

@register_policy
class Strict(Policy):
    name = "strict"
    description = "Switch only when the whole 95% CI stays above 0.98"
    config_fields = [ConfigField("floor", "number", 0.98, "CI lower bound")]

    def recommend(self, step, baseline, candidates, config):
        ok = [c for c in candidates if c["price_known"] and c["quality_ci"] and c["quality_ci"][0] >= config.get("floor", 0.98)
              and (c["cost_savings_pct"] or 0) > 0]
        if not ok:
            return recommendation(step, baseline, None, "keep", "No candidate is safely as good.")
        best = max(ok, key=lambda c: c["cost_savings_pct"])
        return recommendation(step, baseline, best, "switch", f"{best['candidate']} saves {best['cost_savings_pct']:.0f}%.")
```

Each candidate dict has `candidate`, `n`, `quality_mean`, `quality_ci`, `labels`, `price_known`, `cost_per_call`, `cost_savings_pct`, `latency_ms`, `latency_delta_pct`, `monthly_savings_usd`, `errors` and `examples`, plus `tool_agreement`, `decision_agreement` and `tool_n` (`None` / 0 on steps without tools). `baseline` has `model`, `cost_per_call`, `latency_ms` and `monthly_calls_est`. Always build the result with `recommendation(...)`, so the dashboard, CLI and SDK can render it and it stays marked as an estimate.
