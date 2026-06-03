"""Provider connectivity and agent-tool tests."""
import json
import logging
import os
import shutil
import time

import httpx

from .. import db
from .upstream import build_request, provider_format

logger = logging.getLogger(__name__)


def _jsonish_arguments(content):
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        json.loads(text)
    except Exception:
        return None
    return text


async def test_provider(provider_id: str):
    """Test connectivity to a provider."""
    provider = await db.get_provider(provider_id)
    if not provider:
        return {"valid": False, "error": "Provider not found"}

    key = await db.get_alive_key(provider_id)
    if not key:
        return {"valid": False, "error": "No alive keys for this provider"}

    start = time.time()

    try:
        if provider.get("type") == "claude-cli":
            binary = os.getenv("AI_ROUTER_CLAUDE_CLI_BINARY", "claude")
            path = shutil.which(binary)
            latency = int((time.time() - start) * 1000)
            if not path:
                return {"valid": False, "latency_ms": latency, "error": f"Claude CLI binary not found: {binary}"}
            return {"valid": True, "latency_ms": latency, "status": "cli-found", "binary": path}

        if provider_format(provider) == "anthropic-compatible":
            req = build_request(provider, "chat", key["key_value"])
            body = {"model": "claude-3-haiku-20240307", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(req["url"], headers=req["headers"], json=body)
        else:
            req = build_request(provider, "models", key["key_value"])
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(req["url"], headers=req["headers"])

        latency = int((time.time() - start) * 1000)
        if resp.status_code < 400:
            return {"valid": True, "latency_ms": latency, "status": resp.status_code}
        return {"valid": False, "latency_ms": latency, "status": resp.status_code, "error": resp.text[:200]}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


async def test_provider_tools(provider_id: str, model_id: str = None):
    """Test whether a provider/model returns OpenAI-compatible tool_calls."""
    provider = await db.get_provider(provider_id)
    if not provider:
        return {"agent_ready": False, "valid": False, "error": "Provider not found"}
    if provider_format(provider) != "openai-compatible":
        return {
            "agent_ready": False,
            "valid": False,
            "supports_tools": False,
            "error": "Tool test currently supports openai-compatible providers only",
        }

    model = model_id or ""
    if not model:
        for alias in provider.get("aliases", []):
            if alias.get("is_active", 1):
                model = alias["model_id"]
                break
    if not model:
        return {"agent_ready": False, "valid": False, "error": "No model_id provided and no active alias found"}

    key = await db.get_alive_key(provider_id, model)
    if not key:
        return {"agent_ready": False, "valid": False, "error": "No alive keys for this provider/model"}

    req = build_request(provider, "chat", key["key_value"])

    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": "Call get_weather for Paris."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(req["url"], headers=req["headers"], json=payload)
        latency = int((time.time() - start) * 1000)
        text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code >= 400:
            return {
                "agent_ready": False,
                "valid": False,
                "supports_tools": False,
                "model": model,
                "latency_ms": latency,
                "status": resp.status_code,
                "error": text[:500],
            }

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            arguments = _jsonish_arguments(message.get("content"))
            if arguments is not None:
                tool_calls = [{
                    "id": f"call_{int(time.time() * 1000)}",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": arguments},
                }]
                message["tool_calls"] = tool_calls
                message["content"] = None
                choice["finish_reason"] = "tool_calls"
        agent_ready = bool(tool_calls)
        return {
            "agent_ready": agent_ready,
            "valid": True,
            "supports_tools": agent_ready,
            "model": model,
            "latency_ms": latency,
            "status": resp.status_code,
            "finish_reason": choice.get("finish_reason"),
            "tool_call_count": len(tool_calls),
            "tool_calls_preview": tool_calls[:1],
            "error": None if agent_ready else "No message.tool_calls returned; not support agent command",
            "content_preview": (message.get("content") or "")[:300],
        }
    except Exception as e:
        return {
            "agent_ready": False,
            "valid": False,
            "supports_tools": False,
            "model": model,
            "error": str(e)[:500],
        }
