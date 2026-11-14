"""Datasets & experiments: compare two prompt versions on a golden dataset with an LLM judge + fuzzy match.

    python examples/06_experiment.py
"""
import os

from google import genai
from google.genai import types

import tracelens
from tracelens.experiments import create_dataset, run_experiment
from _kb import retrieve

tracelens.init()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta"))

create_dataset("support-golden", [
    {"input": "How long do I have to get a refund on an annual plan?", "expected_output": "30 days"},
    {"input": "How much is the Pro plan per month?", "expected_output": "$29 per month"},
    {"input": "Which regions have GPU instances?", "expected_output": "Mumbai and Virginia"},
    {"input": "How long are backups kept on Enterprise?", "expected_output": "90 days"},
    {"input": "What uptime SLA does the Pro plan have?", "expected_output": "99.9%"},
], description="Golden Q&A for the support bot")


def make_task(system: str, use_rag: bool):
    def task(question: str) -> str:
        ctx = "\n".join(d["content"] for d in retrieve(question)) if use_rag else ""
        r = client.models.generate_content(model="gemini-flash-lite-latest", contents=f"{ctx}\n\nQ: {question}",
                                           config=types.GenerateContentConfig(system_instruction=system, temperature=0, max_output_tokens=120))
        return (r.text or "").strip()
    return task


evaluators = ["similarity", {"evaluator": "llm_correctness"}]
for name, system, rag in [("v1-no-context", "Answer the question briefly.", False),
                          ("v2-rag", "Answer briefly using only the provided context.", True)]:
    summary = run_experiment("support-golden", make_task(system, rag), run_name=name, evaluators=evaluators, metadata={"prompt": name})
    print(name, summary.get("avg_scores"))
