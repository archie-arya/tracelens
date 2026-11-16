# Changelog

## 0.2.0 (2026-11-16)

Agent / tool-call evaluation in the Cost Optimizer.

### SDK (Python)
- LLM spans now record the tool definitions offered to the model (`metadata.tool_definitions`, with JSON schemas), the tool calls it returned (`output.tool_calls` as `{id, name, arguments}`, with arguments parsed), and the full message history including earlier tool calls and tool results. Covers OpenAI and OpenAI-compatible clients, Anthropic, Google Gen AI (Gemini) and LiteLLM, streaming included (OpenAI `delta.tool_calls`, Anthropic `input_json_delta`, Gemini function-call chunks).
- SDK message objects passed back into `messages` (e.g. a `ChatCompletionMessage` or Gemini `Content`) are stored as plain dicts.
- CLI: `tracelens optimize --allow-tool-steps --scorer-config '{...}'`; `tracelens steps` shows calls that become replayable with tools; run output shows tool agreement and mismatched calls.
- Still zero dependencies.

### Server
- `allow_tool_steps` run option (off by default). Tool-calling LLM steps are replayed one recorded call at a time, with the recorded messages, recorded tool results and the same tool schemas. Tools are never executed and nothing is sent back after the candidate's answer. Steps without recorded tool definitions, or that use provider built-in tools, stay skipped, with the reason reported.
- New scorer `tool_call_match`: compares the call-vs-answer decision, tool names, order and arguments (`args_mode`: lenient, strict or partial; `ignore_args` for free-text arguments), reports the sub-scores and can blend in a text score (`judge_weight`). `auto` uses it for tool steps.
- Results: per-candidate `tool_agreement`, `decision_agreement`, `tool_name_agreement`, `tool_args_match` and `tool_mismatches`, plus per-trace `trajectories` (the original tool-call sequence vs. each candidate's decision at every replayed step).
- `quality_threshold` policy: `min_tool_agreement` (default 0.95) applies on tool steps.
- Providers: OpenAI-compatible hosts, Gemini and LiteLLM send tools and tool history and return tool calls (`Provider.supports_tools`). The replay cache key includes the tool schemas.
- `GET /api/v1/optimizer/steps` adds `replayable_with_tools` and `tool_calls`.

### Dashboard
- Optimizer: *Replay tool-calling steps* option in the run form, a *Tool agreement* column, a diff of mismatched tool calls, and a *Tool-call trajectories* table per trace.

### Demo
- `demo/seed.py --only agent` drives the tool-calling agent. The public demo includes an agent optimizer run.

## 0.1.0 (2026-11-14)

First release: tracing SDK (zero dependencies) with auto-instrumentation, FastAPI server (SQLite/Postgres, OTLP and Langfuse-compatible ingestion), dashboard, LLM-as-judge and heuristic evaluations, datasets and experiments, alerts, and the Cost Optimizer.
