/**
 * TraceLens JS/TS SDK - zero dependencies, works in Node 18+, Bun, Deno and edge runtimes.
 *
 *   const tl = new TraceLens({ apiKey: process.env.TRACELENS_API_KEY, host: "http://localhost:8000" });
 *   await tl.trace("chat", async (t) => { ...; return answer; });
 *   const openai = wrapOpenAI(new OpenAI(), tl);   // auto-trace chat completions
 */
const uid = () => {
    const c = globalThis.crypto;
    if (c?.randomUUID)
        return c.randomUUID().replace(/-/g, "");
    return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
};
const iso = (d = new Date()) => d.toISOString();
// Async context: AsyncLocalStorage on Node/Bun/Deno, explicit parent passing elsewhere.
let als = null;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = globalThis.process?.versions?.node ? await import("node:async_hooks") : null;
    als = mod ? new mod.AsyncLocalStorage() : null;
}
catch {
    als = null;
}
const current = () => als?.getStore();
export class Trace {
    client;
    name;
    sampled;
    id = uid();
    startTime = iso();
    endTime;
    body;
    constructor(client, name, opts = {}, sampled = true) {
        this.client = client;
        this.name = name;
        this.sampled = sampled;
        this.body = { tags: [], metadata: {}, ...opts };
    }
    update(fields) {
        if (fields.name)
            this.name = fields.name;
        if (fields.userId)
            this.body.user_id = fields.userId;
        if (fields.sessionId)
            this.body.session_id = fields.sessionId;
        if (fields.tags)
            this.body.tags = [...new Set([...(this.body.tags || []), ...fields.tags])];
        if (fields.metadata)
            this.body.metadata = { ...this.body.metadata, ...fields.metadata };
        if (fields.input !== undefined)
            this.body.input = fields.input;
        if (fields.output !== undefined)
            this.body.output = fields.output;
        return this;
    }
    score(name, value, comment) {
        this.client.score(name, value, { traceId: this.id, comment });
    }
    end(output) {
        if (output !== undefined)
            this.body.output = output;
        this.endTime = iso();
        if (!this.sampled)
            return;
        const m = this.client.opts.mask;
        this.client.enqueue("trace", {
            id: this.id, name: this.name, user_id: this.body.user_id, session_id: this.body.session_id, tags: this.body.tags,
            metadata: this.body.metadata, input: m ? m(this.body.input) : this.body.input, output: m ? m(this.body.output) : this.body.output,
            release: this.client.opts.release, environment: this.client.opts.environment, start_time: this.startTime, end_time: this.endTime, sdk: "js",
        });
    }
}
export class Span {
    trace;
    name;
    type;
    parentId;
    id = uid();
    startTime = iso();
    completionStartTime;
    data = {};
    constructor(trace, name, type = "span", parentId, init = {}) {
        this.trace = trace;
        this.name = name;
        this.type = type;
        this.parentId = parentId;
        Object.assign(this.data, init);
    }
    update(fields) {
        const d = this.data;
        if (fields.input !== undefined)
            d.input = fields.input;
        if (fields.output !== undefined)
            d.output = fields.output;
        if (fields.model)
            d.model = fields.model;
        if (fields.modelParameters)
            d.model_parameters = fields.modelParameters;
        if (fields.metadata)
            d.metadata = { ...(d.metadata || {}), ...fields.metadata };
        if (fields.usage)
            d.usage = fields.usage;
        if (fields.cost !== undefined)
            d.cost = fields.cost;
        if (fields.level)
            d.level = fields.level;
        if (fields.statusMessage)
            d.status_message = fields.statusMessage;
        return this;
    }
    markFirstToken() {
        this.completionStartTime ??= iso();
    }
    error(e) {
        const err = e;
        this.update({ level: "ERROR", statusMessage: `${err?.name || "Error"}: ${err?.message || String(e)}`, metadata: { exception: { stack: err?.stack } } });
    }
    end(output) {
        if (output !== undefined)
            this.data.output = output;
        if (!this.trace.sampled)
            return;
        const m = this.trace.client.opts.mask;
        const d = this.data;
        this.trace.client.enqueue("observation", {
            id: this.id, trace_id: this.trace.id, parent_id: this.parentId, type: this.type, name: this.name,
            ...d, input: m ? m(d.input) : d.input, output: m ? m(d.output) : d.output,
            start_time: this.startTime, end_time: iso(), completion_start_time: this.completionStartTime,
        });
    }
}
export class TraceLens {
    opts;
    queue = [];
    timer = null;
    failures = 0;
    pausedUntil = 0;
    enabled;
    constructor(opts = {}) {
        const env = globalThis.process?.env || {};
        this.opts = { host: env.TRACELENS_HOST || "http://localhost:8000", flushAt: 50, flushIntervalMs: 1000, sampleRate: 1, apiKey: env.TRACELENS_API_KEY, ...opts };
        this.opts.host = this.opts.host.replace(/\/$/, "");
        this.enabled = opts.enabled ?? !!this.opts.apiKey;
        const proc = globalThis.process;
        proc?.on?.("beforeExit", () => this.flush());
    }
    enqueue(type, body) {
        if (!this.enabled)
            return;
        if (this.queue.length > 10000)
            return; // drop rather than grow unbounded
        this.queue.push({ type, body });
        if (this.queue.length >= this.opts.flushAt)
            void this.flush();
        else if (!this.timer) {
            this.timer = setTimeout(() => void this.flush(), this.opts.flushIntervalMs);
            this.timer?.unref?.();
        }
    }
    async flush() {
        if (this.timer)
            clearTimeout(this.timer);
        this.timer = null;
        if (!this.queue.length || Date.now() < this.pausedUntil)
            return;
        const batch = this.queue.splice(0, this.queue.length);
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const r = await fetch(`${this.opts.host}/api/v1/ingest`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.opts.apiKey}` },
                    body: JSON.stringify({ batch }),
                });
                if (r.ok || (r.status >= 400 && r.status < 500 && r.status !== 429)) {
                    this.failures = 0;
                    return;
                }
            }
            catch {
                /* retry */
            }
            await new Promise((res) => setTimeout(res, 300 * 2 ** attempt));
        }
        if (++this.failures >= 3)
            this.pausedUntil = Date.now() + 30_000; // circuit breaker: never hurt the app
    }
    /** Run `fn` inside a new trace. Nested `span()` calls attach automatically (Node AsyncLocalStorage). */
    async trace(name, fn, opts = {}) {
        const t = new Trace(this, name, {}, Math.random() < this.opts.sampleRate).update(opts);
        const run = async () => {
            try {
                const out = await fn(t);
                if (t.body.output === undefined)
                    t.body.output = out;
                return out;
            }
            catch (e) {
                t.update({ metadata: { error: String(e) } });
                throw e;
            }
            finally {
                t.end();
            }
        };
        return als ? als.run({ trace: t }, run) : run();
    }
    /** Run `fn` as a child span (creates a trace if none is active). */
    async span(name, fn, opts = {}) {
        const ctx = current();
        if (!ctx)
            return this.trace(name, () => this.span(name, fn, opts), { input: opts.input });
        const s = new Span(ctx.trace, name, opts.type || "span", ctx.span?.id, { input: opts.input, model: opts.model, metadata: opts.metadata });
        const run = async () => {
            try {
                const out = await fn(s);
                if (s.data.output === undefined)
                    s.data.output = out;
                return out;
            }
            catch (e) {
                s.error(e);
                throw e;
            }
            finally {
                s.end();
            }
        };
        return als ? als.run({ trace: ctx.trace, span: s }, run) : run();
    }
    /** Wrap any async function so each call is recorded as a span. */
    observe(name, fn, type = "span") {
        return (...args) => this.span(name, () => fn(...args), { type, input: args.length === 1 ? args[0] : args });
    }
    /** Manually start a generation (call `.end()` yourself). */
    startGeneration(name, init = {}) {
        const ctx = current();
        const t = ctx?.trace ?? new Trace(this, name);
        const s = new Span(t, name, "generation", ctx?.span?.id, { model: init.model, input: init.input, model_parameters: init.modelParameters });
        if (!ctx) {
            const end = s.end.bind(s);
            s.end = (o) => {
                end(o);
                t.end(o ?? s.data.output);
            };
        }
        return s;
    }
    score(name, value, opts = {}) {
        const traceId = opts.traceId ?? current()?.trace.id;
        if (!traceId)
            return;
        const body = { id: uid(), trace_id: traceId, observation_id: opts.observationId, name, comment: opts.comment, source: "sdk" };
        if (typeof value === "string")
            body.string_value = value;
        else
            body.value = typeof value === "boolean" ? (value ? 1 : 0) : value;
        this.enqueue("score", body);
    }
}
/** Auto-trace an OpenAI(-compatible) client: `wrapOpenAI(new OpenAI({ baseURL }), tl)` */
export function wrapOpenAI(client, tl) {
    const c = client;
    const completions = c.chat?.completions;
    if (!completions || completions.__tracelens)
        return client;
    const orig = completions.create.bind(completions);
    const provider = String(c.baseURL || "").includes("groq") ? "groq" : String(c.baseURL || "").includes("googleapis") ? "gemini" : "openai";
    completions.create = async (params, ...rest) => {
        const g = tl.startGeneration(`${provider}.chat`, { model: params.model, input: params.messages, modelParameters: { temperature: params.temperature, max_tokens: params.max_tokens } });
        g.update({ metadata: { provider } });
        try {
            const res = await orig(params, ...rest);
            if (params.stream) {
                const parts = [];
                let usage;
                const it = res[Symbol.asyncIterator].bind(res);
                res[Symbol.asyncIterator] = async function* () {
                    for await (const chunk of { [Symbol.asyncIterator]: it }) {
                        g.markFirstToken();
                        parts.push(chunk.choices?.[0]?.delta?.content || "");
                        if (chunk.usage)
                            usage = chunk.usage;
                        yield chunk;
                    }
                    g.update({ output: parts.join(""), usage: usage && { input: usage.prompt_tokens, output: usage.completion_tokens } });
                    g.end();
                };
                return res;
            }
            g.update({ model: res.model, output: res.choices?.[0]?.message?.content ?? res.choices?.[0]?.message, usage: res.usage && { input: res.usage.prompt_tokens, output: res.usage.completion_tokens, total: res.usage.total_tokens } });
            g.end();
            return res;
        }
        catch (e) {
            g.error(e);
            g.end();
            throw e;
        }
    };
    completions.__tracelens = true;
    return client;
}
export default TraceLens;
