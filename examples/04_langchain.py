"""LangChain integration via the callback handler (works with any LangChain chat model, LCEL chains and LangGraph).

    pip install langchain-core langchain-openai
    python examples/04_langchain.py
"""
import os

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

import tracelens
from tracelens.integrations.langchain import TraceLensCallbackHandler

tracelens.init(auto_instrument=False)  # LangChain is traced through the callback handler instead

try:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gemini-flash-lite-latest", api_key=os.environ["GEMINI_API_KEY"],
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/", temperature=0.3)
except ImportError:  # no langchain-openai installed: use a fake model so the example still runs
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    llm = FakeListChatModel(responses=["Observability lets you see every prompt, cost and failure in production."])

prompt = ChatPromptTemplate.from_messages([("system", "You explain engineering concepts in one sentence."), ("user", "Explain {topic}")])
chain = prompt | llm | StrOutputParser()

if __name__ == "__main__":
    handler = TraceLensCallbackHandler(trace_name="explain-chain", user_id="lc-user", tags=["langchain"])
    for topic in ["LLM observability", "prompt caching"]:
        print(chain.invoke({"topic": topic}, config={"callbacks": [handler]}))
    tracelens.flush()
