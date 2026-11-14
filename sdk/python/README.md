# tracelens (Python SDK)

Zero-dependency SDK for [TraceLens](https://github.com/archie-arya/tracelens).

```bash
pip install "git+https://github.com/archie-arya/tracelens#subdirectory=sdk/python"
```

```python
import tracelens
tracelens.init(api_key="tl-sk-...", host="http://localhost:8000")  # auto-instruments OpenAI, Gemini, Anthropic, LiteLLM
```

See the main README for the full guide.
