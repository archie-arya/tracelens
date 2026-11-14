import json

import pytest

httpx = pytest.importorskip("httpx")

import tracelens


def _chat_response(req):
    body = json.loads(req.content)
    if body.get("stream"):
        chunks = [
            {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}, "finish_reason": None}]},
            {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
            {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
        ]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode(), headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    })


def test_openai_compatible_groq(mem):
    import openai
    client = openai.OpenAI(api_key="k", base_url="https://api.groq.com/openai/v1", http_client=httpx.Client(transport=httpx.MockTransport(_chat_response)))

    @tracelens.observe()
    def app():
        r = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": "hi"}], temperature=0.2)
        return r.choices[0].message.content

    assert app() == "Hello!"
    tracelens.flush()
    gen = next(o for o in mem.of("observation") if o["type"] == "generation")
    assert gen["name"] == "groq.chat" and gen["metadata"]["provider"] == "groq"
    assert gen["prompt_tokens"] == 10 and gen["completion_tokens"] == 3
    assert gen["model_parameters"] == {"temperature": 0.2}
    assert gen["output"] == "Hello!" and gen["cost"] > 0
    root = next(o for o in mem.of("observation") if o["name"].endswith("app"))
    assert gen["parent_id"] == root["id"]


def test_openai_streaming(mem):
    import openai
    client = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(_chat_response)))
    stream = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True, stream_options={"include_usage": True})
    text = "".join((c.choices[0].delta.content or "") for c in stream if c.choices)
    assert text == "Hello"
    tracelens.flush()
    gen = mem.of("observation")[0]
    assert gen["output"] == "Hello" and gen["total_tokens"] == 7 and gen["completion_start_time"]
    assert mem.of("trace")[0]["output"] == "Hello"


async def test_openai_async(mem):
    import openai
    client = openai.AsyncOpenAI(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(_chat_response)))
    r = await client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert r.choices[0].message.content == "Hello!"
    tracelens.flush()
    assert mem.of("observation")[0]["model"] == "gpt-4o"


def test_openai_error(mem):
    import openai
    client = openai.OpenAI(api_key="k", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": {"message": "rate"}}))))
    with pytest.raises(openai.RateLimitError):
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    tracelens.flush()
    assert mem.of("observation")[0]["level"] == "ERROR"


def test_anthropic(mem):
    import anthropic
    try:
        import httpx2 as hx
    except ImportError:
        hx = httpx

    def h(req):
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-3-5-haiku-20241022",
                                         "content": [{"type": "text", "text": "Bonjour"}], "stop_reason": "end_turn", "stop_sequence": None,
                                         "usage": {"input_tokens": 12, "output_tokens": 4}})
    client = anthropic.Anthropic(api_key="k", http_client=hx.Client(transport=hx.MockTransport(lambda r: hx.Response(200, json=h(r).json()))))
    r = client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=50, system="be french", messages=[{"role": "user", "content": "hi"}])
    assert r.content[0].text == "Bonjour"
    tracelens.flush()
    g = mem.of("observation")[0]
    assert g["output"] == "Bonjour" and g["prompt_tokens"] == 12 and g["input"][0]["role"] == "system" and g["cost"] > 0


def test_langchain_handler(mem):
    pytest.importorskip("langchain_core")
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.prompts import ChatPromptTemplate
    from tracelens.integrations.langchain import TraceLensCallbackHandler

    chain = ChatPromptTemplate.from_messages([("user", "Say {x}")]) | FakeListChatModel(responses=["ok!"])
    out = chain.invoke({"x": "ok"}, config={"callbacks": [TraceLensCallbackHandler(user_id="u9")]})
    assert out.content == "ok!"
    tracelens.flush()
    obs = mem.of("observation")
    traces = mem.of("trace")
    assert len(traces) == 1 and traces[0]["user_id"] == "u9"
    root = next(o for o in obs if o["parent_id"] is None)
    gen = next(o for o in obs if o["type"] == "generation")
    assert gen["output"] == "ok!"
    assert len({o["trace_id"] for o in obs}) == 1 and root["type"] in ("chain", "agent")
