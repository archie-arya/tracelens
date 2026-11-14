"""Anthropic and LiteLLM are auto-instrumented too (no code changes beyond init).

    pip install anthropic litellm
"""
import tracelens

tracelens.init()

import anthropic  # noqa: E402

claude = anthropic.Anthropic()
claude.messages.create(model="claude-3-5-haiku-latest", max_tokens=200, messages=[{"role": "user", "content": "Hello!"}])

import litellm  # noqa: E402

litellm.completion(model="groq/llama-3.1-8b-instant", messages=[{"role": "user", "content": "Hello!"}])
tracelens.flush()
