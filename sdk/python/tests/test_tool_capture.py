"""Tool definitions, tool calls and tool results are recorded on LLM spans for every integration."""
import json

import pytest

httpx = pytest.importorskip("httpx")

import tracelens
from tracelens.integrations import _tools

OA_TOOLS = [{"type": "function", "function": {"name": "get_account", "description": "Look up a customer",
                                              "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}}}]
DEF = [{"name": "get_account", "description": "Look up a customer",
        "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}}]


def _oa_handler(req):
    body = json.loads(req.content)
    if body["messages"][-1]["role"] == "tool":
        msg = {"role": "assistant", "content": "They are on Pro."}
    else:
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id": "cus_001"}'}}]}
    if body.get("stream"):
        chunks = [{"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                      {"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer'}}]}, "finish_reason": None}]},
                  {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {"tool_calls": [
                      {"index": 0, "function": {"arguments": '_id": "cus_001"}'}}]}, "finish_reason": "tool_calls"}]}]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode(), headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
                                     "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}})


def test_openai_records_tools_calls_and_results(mem):
    import openai
    client = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(_oa_handler)))
    messages = [{"role": "user", "content": "What plan is cus_001 on?"}]
    r = client.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=OA_TOOLS, tool_choice="auto")
    msg = r.choices[0].message
    messages.append(msg)  # the SDK object itself, as many agents do
    messages.append({"role": "tool", "tool_call_id": msg.tool_calls[0].id, "content": '{"plan": "Pro"}'})
    client.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=OA_TOOLS)
    tracelens.flush()
    g1, g2 = [o for o in mem.of("observation") if o["type"] == "generation"]
    assert g1["metadata"]["tools"] == ["get_account"] and g1["metadata"]["tool_definitions"] == DEF
    assert g1["model_parameters"]["tool_choice"] == "auto"
    assert g1["output"] == {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "name": "get_account", "arguments": {"customer_id": "cus_001"}}]}
    assert g2["input"][1]["tool_calls"][0]["function"]["name"] == "get_account"  # the assistant turn, as a plain dict
    assert g2["input"][2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"plan": "Pro"}'}
    assert g2["output"] == "They are on Pro."


def test_openai_stream_tool_calls(mem):
    import openai
    client = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(_oa_handler)))
    for _ in client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "q"}], tools=OA_TOOLS, stream=True):
        pass
    tracelens.flush()
    g = mem.of("observation")[0]
    assert g["output"]["tool_calls"] == [{"id": "call_1", "name": "get_account", "arguments": {"customer_id": "cus_001"}}]


def test_anthropic_tool_use(mem):
    import anthropic

    def h(req):
        body = json.loads(req.content)
        assert body["tools"][0]["name"] == "get_account"
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-3-5-haiku-20241022",
                                         "content": [{"type": "text", "text": "Checking."},
                                                     {"type": "tool_use", "id": "tu_1", "name": "get_account", "input": {"customer_id": "cus_001"}}],
                                         "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 4}})
    try:
        import httpx2 as hx
    except ImportError:
        hx = httpx
    client = anthropic.Anthropic(api_key="k", http_client=hx.Client(transport=hx.MockTransport(lambda r: hx.Response(200, json=h(r).json()))))
    tools = [{"name": "get_account", "description": "Look up a customer", "input_schema": DEF[0]["parameters"]}]
    history = [{"role": "user", "content": "plan?"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_0", "name": "get_account", "input": {"customer_id": "x"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_0", "content": "not found"}]}]
    client.messages.create(model="claude-3-5-haiku-20241022", max_tokens=50, tools=tools, messages=history)
    tracelens.flush()
    g = mem.of("observation")[0]
    assert g["metadata"]["tool_definitions"] == DEF
    assert g["output"] == {"role": "assistant", "content": "Checking.", "tool_calls": [{"id": "tu_1", "name": "get_account", "arguments": {"customer_id": "cus_001"}}]}
    assert g["input"][2]["content"][0]["type"] == "tool_result"


def test_anthropic_stream_accumulates_tool_use(mem):
    from tracelens.integrations.anthropic import _Acc

    class Ev:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    obs = tracelens.get_client().start_observation("anthropic.messages", type="generation")
    acc = _Acc(obs)
    acc.feed(Ev(type="content_block_start", index=1, content_block=Ev(type="tool_use", id="tu_1", name="get_account")))
    acc.feed(Ev(type="content_block_delta", index=1, delta=Ev(type="input_json_delta", partial_json='{"customer_id": ')))
    acc.feed(Ev(type="content_block_delta", index=1, delta=Ev(type="input_json_delta", partial_json='"cus_001"}')))
    acc.done()
    tracelens.flush()
    assert mem.of("observation")[0]["output"]["tool_calls"] == [{"id": "tu_1", "name": "get_account", "arguments": {"customer_id": "cus_001"}}]


def test_gemini_function_calls_and_declarations(mem):
    types = pytest.importorskip("google.genai.types")
    from tracelens.integrations import google_genai as gg

    cfg = types.GenerateContentConfig(tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
        name="get_account", description="Look up a customer", parameters=types.Schema(type="OBJECT", properties={"customer_id": types.Schema(type="STRING")}))])])
    contents = [types.Content(role="user", parts=[types.Part(text="plan?")]),
                types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="get_account", args={"customer_id": "x"}))]),
                types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="get_account", response={"error": "not found"}))])]
    resp = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="get_account", args={"customer_id": "cus_001"}))]))])
    obs = gg._start("gemini-2.5-flash", {"contents": contents, "config": cfg}, ())
    gg._end(obs, resp)
    tracelens.flush()
    g = mem.of("observation")[0]
    assert g["metadata"]["tools"] == ["get_account"] and g["metadata"]["tool_definitions"][0]["parameters"]["properties"]["customer_id"]["type"] == "STRING"
    assert g["output"]["tool_calls"] == [{"id": None, "name": "get_account", "arguments": {"customer_id": "cus_001"}}]
    assert g["input"][1]["parts"][0]["function_call"]["name"] == "get_account"
    assert g["input"][2]["parts"][0]["function_response"]["response"] == {"error": "not found"}


def test_litellm_records_tools(mem):
    pytest.importorskip("openai")
    from openai.types.chat import ChatCompletion
    from tracelens.integrations import litellm as ll

    resp = ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 1, "model": "groq/llama", "choices": [
        {"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id":"cus_001"}'}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
    wrapped = ll._sync(lambda *a, **k: resp)
    wrapped(model="groq/llama", messages=[{"role": "user", "content": "q"}], tools=OA_TOOLS)
    tracelens.flush()
    g = mem.of("observation")[0]
    assert g["metadata"]["tool_definitions"] == DEF and g["metadata"]["via"] == "litellm"
    assert g["output"]["tool_calls"][0]["arguments"] == {"customer_id": "cus_001"}


def test_helpers_never_raise():
    assert _tools.openai_tool_definitions({"tools": [None, 3, {"type": "web_search"}]}) == [{"name": "web_search", "builtin": True}]
    assert _tools.parse_args("{bad") == "{bad" and _tools.parse_args("") == {}
    assert _tools.with_tool_calls("hi", []) == "hi"

    def lookup(customer_id: str):
        """Look up a customer."""
    assert _tools.gemini_tool_definitions({"tools": [lookup]})[0]["name"] == "lookup"
