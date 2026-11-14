"""Smallest possible example - no LLM key needed. Shows traces, nested spans, a manual generation and a score.

    export TRACELENS_API_KEY=tl-sk-...  TRACELENS_HOST=http://localhost:8000
    python examples/01_quickstart.py
"""
import time

import tracelens

tracelens.init()  # reads TRACELENS_API_KEY / TRACELENS_HOST


@tracelens.observe(type="retrieval")
def search(query: str) -> list:
    time.sleep(0.05)
    return ["TraceLens is an open-source LLM observability platform.", "It is MIT licensed."]


@tracelens.observe()
def answer(question: str) -> str:
    docs = search(question)
    with tracelens.generation("fake-llm", model="gpt-4o-mini", input=[{"role": "user", "content": question}]) as g:
        time.sleep(0.1)
        text = f"Based on {len(docs)} documents: {docs[0]}"
        g.update(output=text, usage={"input": 120, "output": 25})  # cost is computed from the pricing table
    tracelens.update_current_trace(user_id="demo-user", tags=["quickstart"])
    tracelens.score("user_feedback", 1, comment="thumbs up")
    return text


if __name__ == "__main__":
    print(answer("What is TraceLens?"))
    tracelens.flush()
    print("Sent! Open the dashboard -> Traces.")
