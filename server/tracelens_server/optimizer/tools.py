"""Tool-call helpers for the Cost Optimizer: read tool definitions, tool calls and tool results out of stored
generations (OpenAI, Anthropic, Gemini and LangChain shapes), normalize them, and compare two sets of tool calls.

Canonical shapes used everywhere in the optimizer:

* tool definition: ``{"name", "description", "parameters"}`` (``parameters`` is a JSON schema)
* tool call:       ``{"id", "name", "arguments"}`` (``arguments`` is a dict when it parses as JSON)
* messages:        OpenAI style; assistant messages may carry ``tool_calls``; tool results are
                   ``{"role": "tool", "tool_call_id", "name", "content"}``

Nothing in this module (or anywhere in the optimizer) executes a tool. Replays send the recorded tool results back
to the candidate model as plain data.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------- parsing

def parse_args(a: Any) -> Any:
    if isinstance(a, str):
        s = a.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return a
    return {} if a is None else a


def _call(name: Any, args: Any, id_: Any = None) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    return {"id": id_, "name": str(name), "arguments": parse_args(args)}


def _one_call(c: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(c, dict):
        return None
    fn = c.get("function") if isinstance(c.get("function"), dict) else None
    if fn is not None:  # OpenAI wire format {"id", "type": "function", "function": {"name", "arguments"}}
        return _call(fn.get("name"), fn.get("arguments"), c.get("id"))
    if c.get("type") == "tool_use":  # Anthropic content block
        return _call(c.get("name"), c.get("input"), c.get("id"))
    args = c.get("arguments", c.get("args", c.get("input")))
    return _call(c.get("name"), args, c.get("id"))


def extract_tool_calls(output: Any) -> List[Dict[str, Any]]:
    """Tool calls in a stored generation output, in order. ``[]`` when the model answered without calling a tool."""
    if output is None or isinstance(output, (str, int, float, bool)):
        return []
    if isinstance(output, list):  # Anthropic content blocks / Gemini parts / list of messages
        out: List[Dict[str, Any]] = []
        for b in output:
            if isinstance(b, dict) and (b.get("type") == "tool_use"):
                c = _one_call(b)
                if c:
                    out.append(c)
            elif isinstance(b, dict) and (b.get("functionCall") or b.get("function_call")):
                fc = b.get("functionCall") or b.get("function_call")
                c = _call(fc.get("name"), fc.get("args", fc.get("arguments")), fc.get("id"))
                if c:
                    out.append(c)
            elif isinstance(b, dict):
                out += extract_tool_calls(b)
        return out
    if not isinstance(output, dict):
        return []
    if isinstance(output.get("tool_calls"), list):
        return [c for c in (_one_call(x) for x in output["tool_calls"]) if c]
    fc = output.get("function_call") or output.get("functionCall")
    if isinstance(fc, dict):
        c = _call(fc.get("name"), fc.get("arguments", fc.get("args")), fc.get("id"))
        return [c] if c else []
    if output.get("type") in ("tool_use", "function_call"):
        c = _one_call(output)
        return [c] if c else []
    if "choices" in output:
        try:
            return extract_tool_calls(output["choices"][0]["message"])
        except Exception:
            return []
    for key in ("content", "parts", "message"):
        if isinstance(output.get(key), (list, dict)):
            r = extract_tool_calls(output[key])
            if r:
                return r
    if isinstance(output.get("additional_kwargs"), dict):  # LangChain AIMessage
        return extract_tool_calls(output["additional_kwargs"])
    return []


def tool_definitions(obs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tool definitions recorded for a generation: ``metadata.tool_definitions`` (SDK >= 0.2), ``model_parameters.tools``
    or an ``input`` dict with a ``tools`` key (OTLP / Langfuse / custom ingestion)."""
    md = obs.get("metadata") or {}
    cands = [md.get("tool_definitions"), (obs.get("model_parameters") or {}).get("tools")]
    inp = obs.get("input")
    if isinstance(inp, dict):
        cands.append(inp.get("tools"))
    for raw in cands:
        defs = normalize_tool_definitions(raw)
        if defs:
            return defs
    return []


def normalize_tool_definitions(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        if t.get("builtin"):
            out.append({"name": str(t.get("name")), "builtin": True})
            continue
        f = t.get("function") if isinstance(t.get("function"), dict) else t
        for d in (f.get("function_declarations") or f.get("functionDeclarations") or [f]):
            if not isinstance(d, dict) or not d.get("name"):
                continue
            params = d.get("parameters") or d.get("input_schema") or d.get("parameters_json_schema") or d.get("parametersJsonSchema")
            out.append({"name": str(d["name"]), "description": str(d.get("description") or ""),
                        "parameters": params if isinstance(params, dict) else {"type": "object", "properties": {}}})
    return out


def offered_tool_names(obs: Dict[str, Any]) -> List[str]:
    names = [d["name"] for d in tool_definitions(obs)]
    if names:
        return names
    md = (obs.get("metadata") or {}).get("tools")
    return [str(x) for x in md] if isinstance(md, list) else []


# ---------------------------------------------------------------- messages

def _text(parts: Any) -> str:
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        return "\n".join(t for t in (_text(p) for p in parts) if t)
    if isinstance(parts, dict):
        if "text" in parts:
            return str(parts["text"])
        if "content" in parts:
            return _text(parts["content"])
        if "parts" in parts:
            return _text(parts["parts"])
        return ""
    return str(parts)


def _content_str(v: Any) -> str:
    if isinstance(v, str):
        return v
    t = _text(v) if isinstance(v, list) else ""
    return t if t else json.dumps(v, ensure_ascii=False, default=str)


def has_tool_messages(inp: Any) -> bool:
    msgs = inp.get("messages", inp.get("contents")) if isinstance(inp, dict) else inp
    if not isinstance(msgs, list):
        return False
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("role") in ("tool", "function") or m.get("tool_calls") or m.get("function_call") or m.get("tool_call_id"):
            return True
        content = m.get("content", m.get("parts"))
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and (b.get("type") in ("tool_use", "tool_result") or b.get("functionCall") or b.get("function_call")
                                            or b.get("functionResponse") or b.get("function_response")):
                    return True
    return False


def normalize_tool_messages(inp: Any) -> Optional[List[Dict[str, Any]]]:
    """Canonical OpenAI-style messages that keep tool calls and tool results. ``None`` when the input is not a chat."""
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    system: List[Dict[str, Any]] = []
    if isinstance(inp, dict):
        if inp.get("system"):
            system = [{"role": "system", "content": _text(inp["system"])}]
        inp = inp.get("messages", inp.get("contents"))
    if not isinstance(inp, list):
        return None
    out: List[Dict[str, Any]] = list(system)
    names: Dict[str, str] = {}  # tool_call_id -> tool name
    for m in inp:
        if isinstance(m, str):
            out.append({"role": "user", "content": m})
            continue
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("type") or "user"
        role = {"model": "assistant", "ai": "assistant", "human": "user", "developer": "system", "function": "tool"}.get(role, role)
        content = m.get("content") if "content" in m else m.get("parts", m.get("text"))
        if role == "tool":  # OpenAI tool result (or legacy function result)
            tid = m.get("tool_call_id")
            out.append({"role": "tool", "tool_call_id": tid, "name": m.get("name") or names.get(tid or "", ""), "content": _content_str(content)})
            continue
        calls: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        texts: List[str] = []
        if isinstance(m.get("tool_calls"), list):
            calls += [c for c in (_one_call(x) for x in m["tool_calls"]) if c]
        if isinstance(m.get("function_call"), dict):
            c = _call(m["function_call"].get("name"), m["function_call"].get("arguments"))
            if c:
                calls.append(c)
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    texts.append(str(b))
                    continue
                fc = b.get("functionCall") or b.get("function_call")
                fr = b.get("functionResponse") or b.get("function_response")
                if b.get("type") == "tool_use" or fc:
                    c = _one_call(b) if b.get("type") == "tool_use" else _call(fc.get("name"), fc.get("args", fc.get("arguments")), fc.get("id"))
                    if c:
                        calls.append(c)
                elif b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    results.append({"role": "tool", "tool_call_id": tid, "name": names.get(tid or "", ""), "content": _content_str(b.get("content"))})
                elif fr:
                    results.append({"role": "tool", "tool_call_id": fr.get("id"), "name": fr.get("name") or "",
                                    "content": _content_str(fr.get("response"))})
                elif b.get("thought"):
                    continue
                else:
                    t = _text(b)
                    if t:
                        texts.append(t)
        elif content is not None:
            texts.append(_text(content))
        for i, c in enumerate(calls):
            if not c.get("id"):
                c["id"] = f"call_{len(out)}_{i}"
            names[c["id"]] = c["name"]
        if results:
            for r in results:  # tool results without ids (Gemini) pair with the latest calls of the same name
                if not r["tool_call_id"]:
                    prev = next((c for msg in reversed(out) for c in (msg.get("tool_calls") or []) if c["name"] == r["name"]), None)
                    r["tool_call_id"] = prev["id"] if prev else None
                if not r["name"] and r["tool_call_id"]:
                    r["name"] = names.get(r["tool_call_id"], "")
            if texts:
                out.append({"role": role, "content": "\n".join(texts)})
            out += results
            continue
        msg: Dict[str, Any] = {"role": role, "content": "\n".join(t for t in texts if t)}
        if calls:
            msg["role"] = "assistant"
            msg["tool_calls"] = calls
        out.append(msg)
    return out or None


# ---------------------------------------------------------------- comparison

def _norm_value(v: Any) -> Any:
    """Lenient normalization: case/whitespace-insensitive strings, numeric strings -> numbers, "true" -> True,
    None-valued keys dropped, key order irrelevant (dicts compare as dicts)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if math.isfinite(f) and f.is_integer() else round(f, 9)
    if isinstance(v, str):
        s = re.sub(r"\s+", " ", v.strip()).lower()
        if s in ("true", "false"):
            return s == "true"
        try:
            return _norm_value(float(s.replace(",", ""))) if re.fullmatch(r"[-+]?[\d,]*\.?\d+(e[-+]?\d+)?", s) else s
        except ValueError:
            return s
    if isinstance(v, dict):
        return {str(k).strip().lower(): _norm_value(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [_norm_value(x) for x in v]
    return v


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)


def args_equal(a: Any, b: Any, mode: str = "lenient") -> bool:
    a, b = parse_args(a), parse_args(b)
    if mode == "strict":
        return _canon(a) == _canon(b)
    return _canon(_norm_value(a)) == _canon(_norm_value(b))


def _flat(v: Any, prefix: str = "") -> Dict[str, Any]:
    if isinstance(v, dict) and v:
        out: Dict[str, Any] = {}
        for k, x in v.items():
            out.update(_flat(x, f"{prefix}{k}."))
        return out
    return {prefix.rstrip("."): v}


def args_similarity(a: Any, b: Any) -> float:
    """Share of the original's (flattened, normalized) argument fields the candidate reproduces; extra fields count against."""
    na, nb = _norm_value(parse_args(a)), _norm_value(parse_args(b))
    if not isinstance(na, dict) or not isinstance(nb, dict):
        return 1.0 if _canon(na) == _canon(nb) else 0.0
    fa, fb = _flat(na), _flat(nb)
    keys = set(fa) | set(fb)
    if not keys:
        return 1.0
    same = sum(1 for k in keys if k in fa and k in fb and _canon(fa[k]) == _canon(fb[k]))
    return same / len(keys)


def _lcs(a: List[str], b: List[str]) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def _align(orig: List[Dict[str, Any]], cand: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """Pair each original call with the candidate's next unused call of the same name (order-preserving)."""
    used = [False] * len(cand)
    pairs = []
    for o in orig:
        j = next((k for k, c in enumerate(cand) if not used[k] and c["name"] == o["name"]), None)
        if j is not None:
            used[j] = True
        pairs.append((o, cand[j] if j is not None else None))
    return pairs


WEIGHTS = {"names": 0.3, "order": 0.2, "arguments": 0.5}


def _drop_ignored(calls: List[Dict[str, Any]], ignore: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not ignore:
        return calls
    out = []
    for c in calls:
        a = parse_args(c.get("arguments"))
        if isinstance(a, dict):
            a = {k: v for k, v in a.items() if k not in ignore and f"{c['name']}.{k}" not in ignore}
        out.append({**c, "arguments": a})
    return out


def compare_tool_calls(orig: List[Dict[str, Any]], cand: List[Dict[str, Any]], mode: str = "lenient",
                       weights: Optional[Dict[str, float]] = None, ignore_args: Optional[List[str]] = None) -> Dict[str, Any]:
    """Compare a candidate's tool calls with the original's.

    Returns sub-scores in 0..1 and ``agreement`` (True when the decision, tool names, order and arguments all match
    under ``mode``): ``decision`` (call vs no call), ``names`` (multiset F1 of tool names), ``order`` (longest common
    subsequence of the name sequences / longer length), ``arguments`` (aligned pairs whose args match under ``mode``),
    ``arguments_exact`` (strict JSON equality), ``arguments_partial`` (field-level overlap) and ``score``.
    ``ignore_args`` lists argument names (``"query"``) or ``"tool.arg"`` paths left out of the argument comparison, e.g.
    free-text search queries that are expected to be worded differently.
    """
    orig, cand = _drop_ignored(orig, ignore_args), _drop_ignored(cand, ignore_args)
    w = {**WEIGHTS, **(weights or {})}
    o_names, c_names = [c["name"] for c in orig], [c["name"] for c in cand]
    decision = 1.0 if bool(orig) == bool(cand) else 0.0
    res: Dict[str, Any] = {"decision": decision, "original_calls": len(orig), "candidate_calls": len(cand), "mode": mode}
    if not orig and not cand:
        res.update({"names": 1.0, "order": 1.0, "arguments": 1.0, "arguments_exact": 1.0, "arguments_partial": 1.0,
                    "score": 1.0, "agreement": True, "label": "no_call_match"})
        return res
    if not decision:
        res.update({"names": 0.0, "order": 0.0, "arguments": 0.0, "arguments_exact": 0.0, "arguments_partial": 0.0, "score": 0.0,
                    "agreement": False, "label": "decision_mismatch",
                    "reason": "candidate answered without calling a tool" if orig else "candidate called a tool the original did not need"})
        return res
    common = sum(min(o_names.count(n), c_names.count(n)) for n in set(o_names))
    p, r = common / len(c_names), common / len(o_names)
    names = 0.0 if common == 0 else 2 * p * r / (p + r)
    order = _lcs(o_names, c_names) / max(len(o_names), len(c_names))
    pairs = _align(orig, cand)
    denom = max(len(orig), len(cand))
    lenient = sum(1 for o, c in pairs if c is not None and args_equal(o["arguments"], c["arguments"], mode)) / denom
    exact = sum(1 for o, c in pairs if c is not None and args_equal(o["arguments"], c["arguments"], "strict")) / denom
    partial = sum(args_similarity(o["arguments"], c["arguments"]) for o, c in pairs if c is not None) / denom
    args_score = exact if mode == "strict" else lenient
    tot = sum(w.values()) or 1.0
    score = (w["names"] * names + w["order"] * order + w["arguments"] * (args_score if mode != "partial" else partial)) / tot
    agreement = o_names == c_names and args_score == 1.0
    diffs = []
    for o, c in pairs:
        if c is None:
            diffs.append({"name": o["name"], "issue": "missing", "original": o["arguments"]})
        elif not args_equal(o["arguments"], c["arguments"], "strict" if mode == "strict" else "lenient"):
            diffs.append({"name": o["name"], "issue": "arguments", "original": o["arguments"], "candidate": c["arguments"]})
    matched = {id(c) for _, c in pairs if c is not None}
    diffs += [{"name": c["name"], "issue": "extra", "candidate": c["arguments"]} for c in cand if id(c) not in matched]
    if not diffs and o_names != c_names:
        diffs.append({"issue": "order", "original": o_names, "candidate": c_names})
    res.update({"names": round(names, 4), "order": round(order, 4), "arguments": round(args_score, 4),
                "arguments_exact": round(exact, 4), "arguments_partial": round(partial, 4), "score": round(score, 4),
                "agreement": agreement, "label": "match" if agreement else "partial", "diffs": diffs[:10]})
    if not agreement:
        res["reason"] = "; ".join(_describe(d) for d in diffs[:4]) or "tool calls differ"
    return res


def _describe(d: Dict[str, Any]) -> str:
    if d["issue"] == "missing":
        return f"did not call {d['name']}"
    if d["issue"] == "extra":
        return f"extra call to {d['name']}"
    if d["issue"] == "order":
        return f"different order: {' → '.join(d['candidate'])} instead of {' → '.join(d['original'])}"
    return f"{d['name']}: {_canon(d.get('candidate'))[:80]} instead of {_canon(d.get('original'))[:80]}"


def call_signature(calls: List[Dict[str, Any]]) -> List[str]:
    """Compact display form, e.g. ``['get_account(customer_id="cus_001")']``."""
    out = []
    for c in calls:
        a = c.get("arguments")
        if isinstance(a, dict):
            s = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str)}" for k, v in a.items())
        else:
            s = str(a)
        out.append(f"{c['name']}({s[:160]})")
    return out
