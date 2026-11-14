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
