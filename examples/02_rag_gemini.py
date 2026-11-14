"""RAG support bot on Google Gemini (free tier) using the official google-genai SDK - auto-instrumented.

    pip install google-genai
    export GEMINI_API_KEY=...  TRACELENS_API_KEY=tl-sk-...  TRACELENS_HOST=http://localhost:8000
    python examples/02_rag_gemini.py "What is your refund policy?"
"""
import os
import sys

from google import genai
from google.genai import types

import tracelens
from _kb import retrieve

tracelens.init(release="rag-v2", environment=os.environ.get("APP_ENV", "production"))
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta"))
MODEL = os.environ.get("RAG_MODEL", "gemini-flash-lite-latest")


@tracelens.observe(type="retrieval", name="retrieve_docs")
def search(question: str):
    return retrieve(question, k=2)


@tracelens.observe(name="rewrite_query")
def rewrite(question: str) -> str:
    r = client.models.generate_content(model=MODEL, contents=f"Rewrite this customer question as a short search query, output only the query: {question}",
                                       config=types.GenerateContentConfig(temperature=0, max_output_tokens=40))
    return (r.text or question).strip()


@tracelens.observe(name="support-rag")
def answer(question: str, user_id: str = "anonymous", session_id: str | None = None) -> str:
    tracelens.update_current_trace(user_id=user_id, session_id=session_id, tags=["rag", "support"])
    query = rewrite(question)
    docs = search(query)
    context = "\n\n".join(f"[{d['title']}] {d['content']}" for d in docs)
    r = client.models.generate_content(
        model=MODEL,
        contents=f"Context:\n{context}\n\nCustomer question: {question}",
        config=types.GenerateContentConfig(
            system_instruction="You are Acme Cloud's support assistant. Answer ONLY from the context in 1-3 sentences. If the context doesn't contain the answer, say you don't know.",
            temperature=0.2, max_output_tokens=300),
    )
    return r.text


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Do you refund monthly plans?"
    print(answer(q, user_id="cli-user"))
    tracelens.flush()
