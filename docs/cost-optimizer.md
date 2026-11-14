# Cost Optimizer

The Cost Optimizer answers one question with your own production data: **which LLM steps could run on a cheaper model without getting worse?**

It takes real generations already stored in TraceLens, picks a sample per step, replays the same prompts against cheaper candidate models, scores each candidate output against the original, and turns the numbers into recommendations such as:

> **switch** `support-rag / gemini.generate_content` from `gemini-flash-lite-latest` to `gemini:gemma-4-26b-a4b-it`: 100% cheaper (Gemma is free on the Gemini API), −2.0% relative quality, +1786% latency (it's a thinking model), n=10, confidence medium

That's a real result from the [public demo](https://archie-arya.github.io/tracelens/#/optimizer). The same run with `max_latency_increase_pct: 50` says **keep**.

Every number is an **estimate**. Costs come from list prices in the pricing table, and quality is relative to the original outputs as judged by the scorer, on a sample. The UI, API, CLI and SDK all say so.

## How a run works

```
steps ─► generations in the lookback window ─► replay-safety filter ─► sampler ─► provider (rate limit, cache, spend cap)
      ─► scorer (candidate vs original) ─► aggregation (cost, quality + 95% CI, latency) ─► policy ─► recommendation
```

1. **Steps.** A step is a group of generations with the same name, optionally narrowed by trace name and parent span:
   `"rewrite_query"`, `"support-rag / gemini.generate_content"`, `"support-rag / rewrite_query / gemini.generate_content"`.
   You can also pass a dict, `{"name": ..., "trace_name": ..., "parent_name": ..., "model": ..., "tag": ...}`.
   `GET /api/v1/optimizer/steps` lists every LLM step by spend and shows how many of its calls can be replayed.
2. **Safety filter.** These are never replayed:
   * anything that isn't a `generation` observation (tool, retrieval, agent and custom spans, so side effects never run);
   * generations whose output is a tool call or whose conversation contains tool calls or tool results;
   * errored calls, calls with no visible output, and inputs that aren't a chat prompt.

   Skip counts are reported per step.
3. **Redaction.** With `redact: true` (or `TRACELENS_OPTIMIZER_REDACT=true`), only redacted inputs are sent to candidates: emails, phones, cards, keys, IPs and SSNs are masked first. Without it, the prompt is replayed as stored. If the SDK masked data at capture time, it stays masked.
4. **Sampler.** Picks `sample_size` calls per step and drops duplicate prompts.
5. **Providers.** Each provider has a rolling per-minute rate limiter (`default_rpm`, `rpm` in the run config, or `TRACELENS_OPTIMIZER_RPM_<PROVIDER>`). Requests are retried on 429 and 5xx, honouring `Retry-After`. `concurrency` limits parallel replays.
6. **Replay cache.** Completions are cached by `sha256(candidate, messages, params)`, so rerunning with another scorer or policy costs nothing.
7. **Spend cap.** Before each call the runner estimates its cost. Once `max_spend_usd` (default $1) would be exceeded, the remaining replays are skipped and marked as such. Local providers (Ollama) count as $0.
8. **Scorer.** Returns relative quality from 0 to 1, where 1 means as good as the original or better.
9. **Policy.** Turns the candidate stats into `switch`, `keep` or `insufficient_data`, with a rationale and a confidence level:
   * **high:** n ≥ 30 and the lower bound of the 95% CI clears the threshold;
   * **medium:** n ≥ 10;
   * **low:** anything else.

Runs are stored in the database and execute in the server's background loop. Progress, cancel and delete are supported. A run that was interrupted by a server restart shows as `interrupted`.

## Built-in plugins

| Kind | Name | What it does |
|---|---|---|
| provider | `gemini` | Gemini and Gemma via the Gemini API, with a free tier (`GEMINI_API_KEY`). A bare `gemini-*` or `gemma-*` name (no `provider:` prefix) uses this provider, and any other bare name uses `openai` |
| provider | `openai` | OpenAI or any OpenAI-compatible URL (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) |
| provider | `groq`, `openrouter`, `together`, `deepseek`, `fireworks`, `mistral` | OpenAI-compatible hosts (`<NAME>_API_KEY`) |
| provider | `ollama` | Local models (`OLLAMA_BASE_URL`), always $0 |
| provider | `litellm` | Anything LiteLLM supports (`pip install litellm`), e.g. `litellm:anthropic/claude-3-5-haiku-latest` |
| scorer | `auto` (default) | `json_match` when the original is JSON, otherwise `pairwise_judge` when a judge model is configured, otherwise `similarity` |
| scorer | `pairwise_judge` | An LLM judge sees both answers in random order and scores each. Relative quality is `1 − max(0, original − candidate)`. Set `judge_models` and `criteria` in the scorer config |
| scorer | `exact_match`, `json_match`, `similarity` | Normalized equality, the share of JSON fields that match, token F1 |
| scorer | `eval:<evaluator>` | Reuses any evaluator, built-in or plugin (e.g. `eval:faithfulness`). Reference-style evaluators score the candidate with the original as the expected output, and absolute ones score both outputs and compare |
| sampler | `recent` (default), `random`, `stratified` | Newest first, seeded random (`seed`), or spread evenly over prompt length and time |
| policy | `quality_threshold` (default) | The cheapest candidate within `max_quality_drop` (5%) that saves at least `min_savings_pct` (10%), with at least `min_samples` (5) replays |
| policy | `budget` | The best quality among candidates that reach `target_savings_pct`, as long as quality ≥ `min_quality` |
| policy | `weighted` | Maximizes `quality_weight × quality + cost_weight × savings (+ latency_weight × speedup)` and only switches if the result beats the original |

Unknown model prices show as "price unknown" and are never recommended. Register prices with `TRACELENS_PRICING_FILE` (see [extending](extending.md#5-pricing)).

Adding your own provider, scorer, sampler or policy is about 15 lines. See [extending.md](extending.md#9-cost-optimizer-plugins).

## Using it

**Dashboard:** go to *Cost Optimizer*. The page lists steps by spend; pick one, add candidates, then start a run. A run page shows the recommendations, a cost-vs-quality chart per step, the candidate table and side-by-side examples.

**CLI** (installed with the Python SDK):

```bash
export TRACELENS_HOST=http://localhost:8000 TRACELENS_API_KEY=tl-sk-...
tracelens steps                                     # LLM steps by spend, with replayable counts
tracelens optimize --step "support-rag / rewrite_query / gemini.generate_content" \
                   --candidates gemini:gemini-3.1-flash-lite,ollama:llama3.2 \
                   --sample-size 20 --max-spend 0.50 --redact
tracelens runs                                      # previous runs
```

Useful flags: `--scorer`, `--policy`, `--policy-config '{"max_quality_drop": 0.1}'`, `--sampler`, `--concurrency`, `--no-wait`, `--json`.
`--project` is an optional check that the API key belongs to the project you expect.

**Python SDK:**

```python
import tracelens
tracelens.init()
run = tracelens.optimize(steps=["rewrite_query"], candidates=["gemini:gemini-3.1-flash-lite"], sample_size=20, max_spend_usd=0.5)
for rec in tracelens.optimizer.recommendations(run):
    print(rec["action"], rec["step"], rec.get("to_model"), rec.get("cost_savings_pct"), rec.get("quality_delta_pct"), rec.get("confidence"))
```

**REST:** see [api.md](api.md#cost-optimizer).

## Caveats

* Quality is relative to the original output, not to ground truth. If the original was wrong, an equally wrong candidate scores 1.0. For ground truth, run an [experiment](../README.md) on a dataset instead.
* Judge calls cost money if your judge is a paid model. Only candidate replays count toward `max_spend_usd`. The default judge chain uses free tiers.
* Steps that call tools are skipped, by design. Replaying them would mean re-running side effects or simulating tool results.
* Latency is measured from the TraceLens server and includes network distance to the provider.
