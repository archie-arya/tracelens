# REST API (v1)

Authentication: `Authorization: Bearer tl-sk-...`. A Langfuse-style `Basic base64(public:secret)` header is also accepted. Every resource is scoped to the key's project. `GET` requests without a key read the public demo project, when it's enabled. Times are epoch seconds unless noted. Interactive OpenAPI docs live at `/docs`.

## Ingestion

`POST /api/v1/ingest`: batched, idempotent upserts. Events may arrive in any order.

```json
{"batch": [
  {"type": "trace", "body": {"id": "t1", "name": "chat", "user_id": "u1", "session_id": "s1", "tags": ["prod"],
                              "input": "...", "output": "...", "metadata": {}, "start_time": "2026-11-14T10:00:00Z", "end_time": 1763114401.2}},
  {"type": "observation", "body": {"id": "o1", "trace_id": "t1", "parent_id": null, "type": "generation", "name": "llm",
                                    "model": "gpt-4o-mini", "input": [{"role": "user", "content": "hi"}], "output": "hello",
                                    "usage": {"input": 12, "output": 3}, "model_parameters": {"temperature": 0.2},
                                    "level": "DEFAULT", "start_time": "...", "end_time": "...", "completion_start_time": "..."}},
  {"type": "score", "body": {"trace_id": "t1", "name": "user_feedback", "value": 1, "comment": "great"}}
]}
```

* Observation `type` can be `span | generation | event | tool | retrieval | embedding | agent | chain | guardrail`.
* Times can be ISO-8601 strings or epoch numbers in s, ms, µs or ns.
* `usage` keys `input/output/total`, `prompt_tokens/completion_tokens` and `input_tokens/output_tokens` are all accepted. If `cost` is missing it's computed from the pricing table.
* Langfuse event types (`trace-create`, `generation-create`, `span-update`, ...) are accepted, also at `POST /api/public/ingestion`.
* The response is `{"accepted": n, "traces": .., "observations": .., "scores": .., "errors": [{"index": i, "error": "..."}]}`.

`POST /v1/traces` (alias `/api/v1/otel/v1/traces`): OTLP/HTTP `ExportTraceServiceRequest`, protobuf or JSON, optionally gzip.

## Query

| Method & path | Description |
|---|---|
| `GET /api/v1/traces` | list. Filters: `q, name, user_id, session_id, tag, status (ok/error), model, release, environment, start, end, min_latency_ms, min_cost`. Paging: `limit, offset`. Sorting: `order_by (start_time/latency/cost/tokens), order (asc/desc)` |
| `GET /api/v1/traces/{id}` | trace with `observations[]` and `scores[]` |
| `DELETE /api/v1/traces/{id}` | delete a trace and its children |
| `GET /api/v1/traces/export` | NDJSON export (`start, end, limit`) |
| `GET /api/v1/observations` | filters: `trace_id, type, model, name, level, start, end` |
| `GET /api/v1/sessions` | sessions with turn counts, cost, tokens and errors |
| `GET /api/v1/metrics` | `range=1h/24h/7d/30d/90d` or `start&end`, `bucket_seconds`. Returns totals, timeseries (traces, errors, cost, tokens, p50/p95, average score per name) and breakdowns by model, name, user, tag and score |
| `GET /api/v1/filters` | distinct names, models, tags, users and score names |

## Scores & evaluations

| | |
|---|---|
| `POST /api/v1/scores` | `{trace_id, name, value?, string_value?, observation_id?, comment?}` |
| `GET /api/v1/scores` | filters: `trace_id, name, source, start, end` |
| `GET /api/v1/evaluators` | catalog with config schema and judge availability |
| `POST /api/v1/evals/run` | `{evaluator, config, trace_ids? , filter?, limit}` → job |
| `GET /api/v1/evals/jobs/{id}` | progress and results |
| `POST /api/v1/evals/test` | run an evaluator on ad-hoc `{input, output, expected_output, context}` |
| `POST /api/v1/traces/{id}/evaluate` | `{evaluator, config}` → score |
| `GET/POST /api/v1/eval-rules`, `DELETE /api/v1/eval-rules/{id}` | online rules `{evaluator, config, filter: {name?, tags?, exclude_tags?, environment?}, sample_rate}` |

## Datasets & experiments

| | |
|---|---|
| `GET/POST /api/v1/datasets` | `{name, description}`. Creating is idempotent by name |
| `GET /api/v1/datasets/{id}` · `GET /api/v1/datasets/by-name/{name}` | dataset with items |
| `POST /api/v1/datasets/{id}/items` | `{items: [{input, expected_output, metadata}]}` |
| `POST /api/v1/datasets/{id}/runs` | `{run_name, links: [{item_id, trace_id}], evaluators: [{evaluator, config}], metadata}`. Scores the run synchronously and returns its summary |
| `GET /api/v1/datasets/{id}/runs` | per-run averages (scores, latency, cost, errors) and the item × run matrix |

## Alerts

| | |
|---|---|
| `GET/POST /api/v1/alerts/rules` | `{name, metric: cost/error_rate/latency_p95/latency_p50/trace_count/tokens/score_avg, comparator: gt/gte/lt/lte, threshold, window_minutes, cooldown_minutes, webhook_url, filter: {score_name?, trace_name?, tag?}}` |
| `DELETE /api/v1/alerts/rules/{id}` | |
| `POST /api/v1/alerts/rules/{id}/check?test=false` | evaluate now. With `test=true`, a test webhook is always sent |
| `GET /api/v1/alerts/events` | history |

## Projects & keys

| | |
|---|---|
| `POST /api/v1/projects` | `{name}` → `{project, api_key}`. Open when `TRACELENS_ALLOW_SIGNUP=true`, otherwise needs `X-Admin-Token` |
| `GET /api/v1/project` | current project. `read_only` is true for anonymous demo access |
| `GET/POST /api/v1/keys`, `DELETE /api/v1/keys/{id}` | manage the project's keys. Keys are stored as SHA-256 hashes |
