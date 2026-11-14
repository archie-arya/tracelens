/**
 * TraceLens JS/TS SDK - zero dependencies, works in Node 18+, Bun, Deno and edge runtimes.
 *
 *   const tl = new TraceLens({ apiKey: process.env.TRACELENS_API_KEY, host: "http://localhost:8000" });
 *   await tl.trace("chat", async (t) => { ...; return answer; });
 *   const openai = wrapOpenAI(new OpenAI(), tl);   // auto-trace chat completions
 */
type Json = any;
export type ObservationType = "span" | "generation" | "event" | "tool" | "retrieval" | "embedding" | "agent" | "chain";
export interface TraceLensOptions {
    apiKey?: string;
    host?: string;
    enabled?: boolean;
    flushAt?: number;
    flushIntervalMs?: number;
    sampleRate?: number;
    mask?: (v: Json) => Json;
    release?: string;
    environment?: string;
}
export declare class Trace {
    client: TraceLens;
    name: string;
    sampled: boolean;
    id: any;
    startTime: string;
    endTime?: string;
    body: Record<string, Json>;
    constructor(client: TraceLens, name: string, opts?: Record<string, Json>, sampled?: boolean);
    update(fields: {
        userId?: string;
        sessionId?: string;
        tags?: string[];
        metadata?: Json;
        input?: Json;
        output?: Json;
        name?: string;
    }): this;
    score(name: string, value: number | boolean | string, comment?: string): void;
    end(output?: Json): void;
}
export declare class Span {
    trace: Trace;
    name: string;
    type: ObservationType;
    parentId?: string | undefined;
    id: any;
    startTime: string;
    completionStartTime?: string;
    data: Record<string, Json>;
    constructor(trace: Trace, name: string, type?: ObservationType, parentId?: string | undefined, init?: Record<string, Json>);
    update(fields: {
        input?: Json;
        output?: Json;
        model?: string;
        modelParameters?: Json;
        metadata?: Json;
        usage?: {
            input?: number;
            output?: number;
            total?: number;
        };
        cost?: number;
        level?: string;
        statusMessage?: string;
    }): this;
    markFirstToken(): void;
    error(e: unknown): void;
    end(output?: Json): void;
}
export declare class TraceLens {
    opts: Required<Pick<TraceLensOptions, "host" | "flushAt" | "flushIntervalMs" | "sampleRate">> & TraceLensOptions;
    private queue;
    private timer;
    private failures;
    private pausedUntil;
    enabled: boolean;
    constructor(opts?: TraceLensOptions);
    enqueue(type: string, body: Json): void;
    flush(): Promise<void>;
    /** Run `fn` inside a new trace. Nested `span()` calls attach automatically (Node AsyncLocalStorage). */
    trace<T>(name: string, fn: (t: Trace) => Promise<T> | T, opts?: {
        userId?: string;
        sessionId?: string;
        tags?: string[];
        metadata?: Json;
        input?: Json;
    }): Promise<T>;
    /** Run `fn` as a child span (creates a trace if none is active). */
    span<T>(name: string, fn: (s: Span) => Promise<T> | T, opts?: {
        type?: ObservationType;
        input?: Json;
        model?: string;
        metadata?: Json;
    }): Promise<T>;
    /** Wrap any async function so each call is recorded as a span. */
    observe<A extends any[], R>(name: string, fn: (...args: A) => Promise<R> | R, type?: ObservationType): (...args: A) => Promise<R>;
    /** Manually start a generation (call `.end()` yourself). */
    startGeneration(name: string, init?: {
        model?: string;
        input?: Json;
        modelParameters?: Json;
    }): Span;
    score(name: string, value: number | boolean | string, opts?: {
        traceId?: string;
        observationId?: string;
        comment?: string;
    }): void;
}
/** Auto-trace an OpenAI(-compatible) client: `wrapOpenAI(new OpenAI({ baseURL }), tl)` */
export declare function wrapOpenAI<T extends object>(client: T, tl: TraceLens): T;
export default TraceLens;
