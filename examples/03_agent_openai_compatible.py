"""Tool-calling agent using the standard `openai` client against ANY OpenAI-compatible API.

Defaults to Gemini's OpenAI-compatible endpoint (free). Set OPENAI_BASE_URL/OPENAI_API_KEY/AGENT_MODEL to use
Groq, OpenRouter, Together, Ollama (http://localhost:11434/v1), vLLM or OpenAI itself - tracing is identical.

    pip install openai
    python examples/03_agent_openai_compatible.py "What's 18% GST on a Pro plan for a year, and is Mumbai supported?"
"""
import json
import os
import sys

import openai

import tracelens
from _kb import retrieve

tracelens.init()
client = openai.OpenAI(
    base_url=os.environ.get("OPENAI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    api_key=os.environ.get("OPENAI_API_KEY") or os.environ["GEMINI_API_KEY"],
    timeout=60, max_retries=1,
)
MODEL = os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite")

TOOLS = [
    {"type": "function", "function": {"name": "search_docs", "description": "Search Acme Cloud documentation", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculator", "description": "Evaluate an arithmetic expression, e.g. '29*12*0.8'", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "get_account", "description": "Look up a customer's account plan and region", "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}}},
]
ACCOUNTS = {"cus_001": {"plan": "Pro", "region": "Mumbai", "seats": 5}, "cus_002": {"plan": "Starter", "region": "Frankfurt", "seats": 1}}


@tracelens.observe(type="tool")
def search_docs(query: str):
    return retrieve(query, k=2)


@tracelens.observe(type="tool")
def calculator(expression: str):
    if not set(expression) <= set("0123456789+-*/(). "):
        raise ValueError(f"unsupported expression: {expression}")
    return round(eval(expression, {"__builtins__": {}}), 4)  # noqa: S307 - sanitized above


@tracelens.observe(type="tool")
def get_account(customer_id: str):
    if customer_id not in ACCOUNTS:
        raise KeyError(f"customer {customer_id} not found")
    return ACCOUNTS[customer_id]


FUNCS = {"search_docs": search_docs, "calculator": calculator, "get_account": get_account}


@tracelens.observe(type="agent", name="billing-agent")
def run_agent(task: str, user_id: str = "anonymous", session_id: str | None = None, max_steps: int = 5) -> str:
    tracelens.update_current_trace(user_id=user_id, session_id=session_id, tags=["agent", "tools"])
    messages = [{"role": "system", "content": "You are a billing assistant for Acme Cloud. Use tools to look things up and compute numbers. Be concise."},
                {"role": "user", "content": task}]
    for step in range(max_steps):
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, temperature=0)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                result = FUNCS[tc.function.name](**json.loads(tc.function.arguments or "{}"))
            except Exception as e:  # the error is recorded on the tool span; the agent sees it and recovers
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return "Stopped: step limit reached."


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Customer cus_001 wants to switch to annual billing. What will they pay per year, and how long are their backups kept?"
    print(run_agent(task, user_id="cli-user"))
    tracelens.flush()
