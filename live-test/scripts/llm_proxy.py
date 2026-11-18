"""Test-only stand-in for the Claude API: the platform's ChatAnthropic calls (ANTHROPIC_BASE_URL) are translated
to Groq (OpenAI-compatible) with Gemini (OpenAI-compatible endpoint) as the next step, so the platform's REAL
agent code (tools, structured output, price guard, hand-over) runs against real models without any code change.

Modes (POST /_mode {"mode": ...}):
  real  translate to Groq -> Gemini (default)
  fake  forward to the deterministic fake Claude in fake_services.py (capacity tests without rate limits)
  fail  answer 529 overloaded, like a Claude outage: the platform's own Gemini standby must take over
GET /_stats: calls per provider/model, 429s, errors, latency, tokens.  POST /_reset clears them.
Keys come from the environment only (GROQ_API_KEY, GEMINI_API_KEY); nothing secret is logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from collections import defaultdict
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
FAKE_URL = os.environ.get("FAKE_CLAUDE_URL", "http://fakes:9000/v1/messages")
CHAIN = [
    ("groq", m) for m in os.environ.get("GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b").split(",")
] + [("gemini", m) for m in os.environ.get("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash").split(",")]

app = FastAPI()
STATE: dict[str, Any] = {"mode": os.environ.get("PROXY_MODE", "real")}
COOLDOWN: dict[str, float] = {}
STATS: dict[str, Any] = defaultdict(int)
LAT: dict[str, list[float]] = defaultdict(list)
http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _find_schema(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if node.get("type") == "json_schema" and isinstance(node.get("schema"), dict):
            return node["schema"]
        for v in node.values():
            if (s := _find_schema(v)) is not None:
                return s
    elif isinstance(node, list):
        for v in node:
            if (s := _find_schema(v)) is not None:
                return s
    return None


def to_openai(req: dict[str, Any]) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = []
    if req.get("system"):
        msgs.append({"role": "system", "content": _text(req["system"])})
    for m in req.get("messages", []):
        role, content = m["role"], m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        texts, calls, results = [], [], []
        for b in content or []:
            t = b.get("type")
            if t == "text":
                texts.append(b.get("text", ""))
            elif t == "tool_use":
                calls.append({"id": b["id"], "type": "function",
                              "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}})
            elif t == "tool_result":
                c = b.get("content")
                results.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": c if isinstance(c, str) else _text(c) or json.dumps(c)})
        if role == "assistant":
            out: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                out["tool_calls"] = calls
            msgs.append(out)
        else:
            msgs.extend(results)
            if texts:
                msgs.append({"role": "user", "content": "\n".join(texts)})
    body: dict[str, Any] = {"messages": msgs, "max_tokens": max(int(req.get("max_tokens", 1024)), 1024)}
    tools = [t for t in req.get("tools") or [] if isinstance(t, dict) and "input_schema" in t]
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t["input_schema"]}} for t in tools]
        tc = req.get("tool_choice") or {}
        if tc.get("type") == "any":
            body["tool_choice"] = "required"
        elif tc.get("type") == "tool":
            body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    schema = _find_schema({k: v for k, v in req.items() if k not in ("tools", "messages")})
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
        msgs[0:0] = [] if msgs and msgs[0]["role"] == "system" else [{"role": "system", "content": ""}]
        msgs[0]["content"] += "\nAnswer with one JSON object that matches the given schema, nothing else."
    return body


def to_anthropic(model: str, data: dict[str, Any]) -> dict[str, Any]:
    choice = data["choices"][0]
    msg = choice["message"]
    content: list[dict[str, Any]] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        try:
            args = json.loads(c["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        content.append({"type": "tool_use", "id": c.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": c["function"]["name"], "input": args})
    if not content:
        content = [{"type": "text", "text": ""}]
    stop = "tool_use" if msg.get("tool_calls") else ("max_tokens" if choice.get("finish_reason") == "length" else "end_turn")
    u = data.get("usage") or {}
    return {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


async def real(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    body = to_openai(req)
    last = (529, {"type": "error", "error": {"type": "overloaded_error", "message": "all backends busy"}})
    for provider, model in CHAIN:
        key = f"{provider}:{model}"
        if COOLDOWN.get(key, 0) > time.time():
            STATS[f"skip_cooldown:{key}"] += 1
            continue
        url, auth = (GROQ_URL, os.environ["GROQ_API_KEY"]) if provider == "groq" else (GEMINI_URL, os.environ["GEMINI_API_KEY"])
        t0 = time.perf_counter()
        try:
            r = await http.post(url, headers={"Authorization": f"Bearer {auth}"}, json={**body, "model": model})
        except httpx.HTTPError as e:
            STATS[f"neterr:{key}"] += 1
            STATS["last_error"] = f"{key} {type(e).__name__}"
            continue
        dt = time.perf_counter() - t0
        if r.status_code == 429 or r.status_code >= 500:
            STATS[f"http{r.status_code}:{key}"] += 1
            wait = float(r.headers.get("retry-after", "20") or 20)
            COOLDOWN[key] = time.time() + min(max(wait, 5.0), 120.0)
            continue
        if r.status_code >= 400:
            STATS[f"http{r.status_code}:{key}"] += 1
            STATS["last_error"] = f"{key} {r.status_code} {r.text[:300]}"
            continue
        data = r.json()
        STATS[f"ok:{key}"] += 1
        LAT[key].append(dt)
        u = data.get("usage") or {}
        STATS["tokens_in"] += u.get("prompt_tokens", 0)
        STATS["tokens_out"] += u.get("completion_tokens", 0)
        return 200, to_anthropic(req.get("model", "claude"), data)
    STATS["all_failed"] += 1
    return last


@app.post("/v1/messages")
async def messages(request: Request) -> Response:
    raw = await request.body()
    req = json.loads(raw)
    STATS["requests"] += 1
    STATS["tier:" + str(req.get("model"))] += 1
    mode = STATE["mode"]
    if mode == "fail":
        STATS["forced_529"] += 1
        return Response(json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "test outage"}}),
                        status_code=529, media_type="application/json")
    if mode == "fake":
        r = await http.post(FAKE_URL, content=raw, headers={"content-type": "application/json"})
        STATS["fake"] += 1
        return Response(r.content, status_code=r.status_code, media_type="application/json")
    code, body = await real(req)
    return Response(json.dumps(body), status_code=code, media_type="application/json")


@app.post("/_mode")
async def set_mode(request: Request) -> dict[str, Any]:
    STATE["mode"] = (await request.json())["mode"]
    return STATE


@app.get("/_stats")
async def stats() -> dict[str, Any]:
    lat = {k: {"n": len(v), "p50": round(statistics.median(v), 2),
               "p95": round(sorted(v)[max(0, int(len(v) * 0.95) - 1)], 2), "max": round(max(v), 2)}
           for k, v in LAT.items() if v}
    return {"mode": STATE["mode"], "counts": dict(STATS), "latency_s": lat}


@app.post("/_reset")
async def reset() -> dict[str, str]:
    STATS.clear()
    LAT.clear()
    COOLDOWN.clear()
    return {"ok": "reset"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100, log_level="warning")
