# tracelens-js

A zero-dependency TypeScript SDK for [TraceLens](../../README.md). It works on Node 18+, Bun, Deno and edge runtimes.

```ts
import { TraceLens, wrapOpenAI } from "tracelens-js";   // or copy src/index.ts into your project
import OpenAI from "openai";

const tl = new TraceLens({ apiKey: process.env.TRACELENS_API_KEY, host: "http://localhost:8000" });
const openai = wrapOpenAI(new OpenAI(), tl);             // chat completions are traced, including streaming

const answer = await tl.trace("support-chat", async (t) => {
  t.update({ userId: "u_42", tags: ["prod"] });
  const docs = await tl.span("retrieve", () => search(q), { type: "retrieval" });
  const r = await openai.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: q }] });
  return r.choices[0].message.content;
});
tl.score("user_feedback", true);
await tl.flush();
```

Nesting uses `AsyncLocalStorage` on Node, Bun and Deno. Export is batched, retried and behind a circuit breaker, so it never throws into your app.

Install from the GitHub release: `npm i https://github.com/archie-arya/tracelens/releases/download/v0.1.0/tracelens-js-0.1.0.tgz`, or vendor the single file `src/index.ts`.
