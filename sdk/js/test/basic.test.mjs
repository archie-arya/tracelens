import { test } from "node:test";
import assert from "node:assert";
import http from "node:http";
import { TraceLens, wrapOpenAI } from "../dist/index.js";

test("nested spans and openai wrapper are exported", async () => {
  const received = [];
  const srv = http.createServer((req, res) => {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => { received.push(...JSON.parse(b).batch); res.end("{}"); });
  }).listen(0);
  const port = srv.address().port;
  const tl = new TraceLens({ apiKey: "k", host: `http://127.0.0.1:${port}` });
  const fakeOpenAI = { baseURL: "https://api.groq.com/openai/v1", chat: { completions: { create: async (p) => ({ model: p.model, choices: [{ message: { content: "hi!" } }], usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 } }) } } };
  const ai = wrapOpenAI(fakeOpenAI, tl);
  const out = await tl.trace("chat", async (t) => {
    t.update({ userId: "u1" });
    await tl.span("retrieve", async () => ["doc"], { type: "retrieval" });
    const r = await ai.chat.completions.create({ model: "llama-3.1-8b-instant", messages: [{ role: "user", content: "hey" }] });
    return r.choices[0].message.content;
  });
  assert.equal(out, "hi!");
  await tl.flush();
  srv.close();
  const trace = received.find((e) => e.type === "trace").body;
  const obs = received.filter((e) => e.type === "observation").map((e) => e.body);
  assert.equal(trace.user_id, "u1");
  assert.equal(trace.output, "hi!");
  assert.equal(obs.length, 2);
  assert.ok(obs.every((o) => o.trace_id === trace.id));
  const gen = obs.find((o) => o.type === "generation");
  assert.equal(gen.metadata.provider, "groq");
  assert.equal(gen.usage.input, 3);
});
