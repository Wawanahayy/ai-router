"""Proxy logic - forward requests to upstream providers with key rotation & round-robin."""
import asyncio
import httpx
import copy
import json
import logging
import os
import time
from starlette.responses import StreamingResponse
from . import db, rtk
from .services.models import fetch_upstream_models, invalidate_models_cache, proxy_models
from .services.provider_tests import test_provider, test_provider_tools
from .services.streaming import STREAM_CONNECT_TIMEOUT, STREAM_STALL_TIMEOUT, proxy_stream
from .services.upstream import build_request, parse_extra_headers, provider_format

logger = logging.getLogger(__name__)

UPSTREAM_CONNECT_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_CONNECT_TIMEOUT", "20"))
UPSTREAM_READ_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_READ_TIMEOUT", "300"))
UPSTREAM_WRITE_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_WRITE_TIMEOUT", "20"))
UPSTREAM_POOL_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_POOL_TIMEOUT", "20"))
CLAUDE_CLI_TIMEOUT = float(os.getenv("AI_ROUTER_CLAUDE_CLI_TIMEOUT", "300"))


def _upstream_timeout():
    return httpx.Timeout(
        connect=UPSTREAM_CONNECT_TIMEOUT,
        read=UPSTREAM_READ_TIMEOUT,
        write=UPSTREAM_WRITE_TIMEOUT,
        pool=UPSTREAM_POOL_TIMEOUT,
    )


def _timeout_message(exc: Exception):
    if isinstance(exc, httpx.ConnectTimeout):
        return f"Upstream connect timeout after {UPSTREAM_CONNECT_TIMEOUT:g}s"
    if isinstance(exc, httpx.ReadTimeout):
        return f"Upstream read timeout after {UPSTREAM_READ_TIMEOUT:g}s"
    if isinstance(exc, httpx.WriteTimeout):
        return f"Upstream write timeout after {UPSTREAM_WRITE_TIMEOUT:g}s"
    if isinstance(exc, httpx.PoolTimeout):
        return f"Upstream connection pool timeout after {UPSTREAM_POOL_TIMEOUT:g}s"
    return "Upstream timeout"


def _timeout_value(seconds: float):
    return None if seconds <= 0 else seconds


def _estimate_input_tokens(body: dict) -> int:
    try:
        return max(0, int((len(json.dumps(body or {}, ensure_ascii=False)) + 3) / 4))
    except Exception:
        return 0


def _estimate_output_tokens_from_response(data) -> int:
    chars = 0
    if isinstance(data, dict):
        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or choice.get("delta") or {}
            if isinstance(message, dict):
                for key in ("content", "reasoning_content"):
                    value = message.get(key)
                    if isinstance(value, str):
                        chars += len(value)
                if message.get("tool_calls"):
                    chars += len(json.dumps(message.get("tool_calls"), ensure_ascii=False))
        for part in data.get("content") or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                chars += len(part["text"])
            if part.get("type") == "tool_use":
                chars += len(json.dumps(part.get("input") or {}, ensure_ascii=False))
        if data.get("response"):
            chars += _estimate_output_tokens_from_response(data["response"]) * 4
        if data.get("usageMetadata") is None and isinstance(data.get("candidates"), list):
            for candidate in data.get("candidates") or []:
                content = candidate.get("content") if isinstance(candidate, dict) else {}
                for part in content.get("parts") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chars += len(part["text"])
    elif isinstance(data, str):
        chars += len(data)
    return max(0, int(chars / 4)) if chars <= 0 else max(1, int(chars / 4))


def _with_estimated_tokens(tokens_in: int, tokens_out: int, request_body: dict, response_data) -> tuple[int, int]:
    if tokens_in or tokens_out:
        return tokens_in, tokens_out
    return _estimate_input_tokens(request_body), _estimate_output_tokens_from_response(response_data)


async def resolve_model(model: str):
    """
    Resolve a model name to (provider_id, actual_model_name) with round-robin.
    
    If model is empty/None -> auto-pick: round-robin across all active providers with alive keys.
    If model matches a combo name -> round-robin across combo's available models.
    
    Priority when model is specified:
    1. Combo match (combo name = model string)
    2. Exact alias match in model_aliases table (only is_active=1)
    3. Check if alias exists but deactivated -> return None (explicit reject)
    4. Prefix match (only if provider prefix_enabled=1): "prefix/model" -> find provider + active alias
    5. Direct model name -> round-robin across providers that have this model active
    6. Empty model only: round-robin across active providers with alive keys
    """
    if not model or not model.strip():
        return await _auto_pick_rr()
    
    requested_model = model.strip()
    lookup_model = requested_model.lower()
    
    provider_id, actual_model = await db.resolve_combo_model(lookup_model)
    if provider_id:
        return provider_id, actual_model
    
    alias = await db.resolve_alias(lookup_model)
    if alias:
        return alias["provider_id"], alias["model_id"]
    
    alias_any = await db.get_alias(lookup_model)
    if alias_any and not alias_any.get("is_active", 1):
        return None, requested_model
    
    if "/" in lookup_model:
        prefix, _ = lookup_model.split("/", 1)
        _, actual_model = requested_model.split("/", 1)
        providers = await db.list_providers()
        matching = [p for p in providers if (p.get("prefix") or "").lower() == prefix and p["is_active"] and p.get("prefix_enabled", 0)]
        if matching:
            for p in matching:
                for a in p.get("aliases", []):
                    if a["alias"].lower() == lookup_model and a.get("is_active", 1):
                        return p["id"], a["model_id"]
                    if a["alias"].lower() == lookup_model and not a.get("is_active", 1):
                        return None, requested_model
            available = []
            for p in matching:
                if await db.has_alive_key(p["id"], actual_model):
                    available.append(p)
            if available:
                rr = await db._get_rr(f"prefix_rr_{prefix}")
                picked = available[rr % len(available)]
                await db._set_rr(f"prefix_rr_{prefix}", (rr + 1) % len(available))
                return picked["id"], actual_model
            return None, requested_model
    
    providers = await db.list_providers()
    matching = []
    for p in providers:
        if not p["is_active"]:
            continue
        for a in p.get("aliases", []):
            if a["model_id"].lower() == lookup_model and a.get("is_active", 1):
                if await db.has_alive_key(p["id"], a["model_id"]):
                    matching.append((p, a["model_id"]))
                break
    if matching:
        rr = await db._get_rr(f"model_rr_{lookup_model}")
        picked, actual_model = matching[rr % len(matching)]
        await db._set_rr(f"model_rr_{lookup_model}", (rr + 1) % len(matching))
        return picked["id"], actual_model
    
    return None, requested_model


async def _auto_pick_rr():
    """Auto-pick provider using global round-robin across all active providers with alive keys."""
    providers = await db.list_providers()
    available = []
    for p in providers:
        if not p["is_active"]:
            continue
        if await db.has_alive_key(p["id"]):
            alias_model = ""
            for a in p.get("aliases", []):
                if a.get("is_active", 1):
                    alias_model = a["model_id"]
                    break
            available.append((p["id"], alias_model))
    
    if not available:
        return None, ""
    
    idx = await db.increment_rr_counter()
    picked = available[idx % len(available)]
    return picked[0], picked[1]


def get_effective_prefix(provider: dict) -> str:
    """Return prefix only if prefix_enabled, else empty string."""
    if provider.get("prefix_enabled", 0):
        return provider.get("prefix", "")
    return ""


async def resolve_attempts(model: str):
    """Resolve request model into ordered provider/model attempts."""
    requested = (model or "").strip()
    if requested:
        combo_candidates = await db.resolve_combo_candidates(requested.lower())
        if combo_candidates:
            return [
                {
                    "provider_id": c["provider_id"],
                    "model": c["model_id"],
                    "source": "combo",
                    "combo_model_id": c.get("id"),
                }
                for c in combo_candidates
            ]
        combo = await db.combo_exists(requested.lower())
        if combo and combo.get("is_active", 0):
            return [{
                "provider_id": None,
                "model": requested,
                "source": "combo_unavailable",
                "error": f"Combo '{requested}' has no available active models with alive keys",
            }]

    provider_id, actual_model = await resolve_model(model)
    if not provider_id:
        return []
    return [{"provider_id": provider_id, "model": actual_model, "source": "model"}]


async def authenticate_local_key(auth_header: str):
    """Authenticate request using our local API key. Returns local_key dict or None."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    return await db.get_local_key(token)


def _error_kind(status_code: int, error_msg: str = ""):
    if status_code in (401, 403):
        return "auth_dead"
    if status_code == 429:
        return "rate_limited"
    if status_code in (408, 504):
        return "timeout"
    if status_code in (500, 502, 503, 529):
        return "overloaded"
    if status_code in (400, 404):
        lowered = (error_msg or "").lower()
        if "model" in lowered or "not found" in lowered or "unsupported" in lowered:
            return "unsupported_model"
    return "upstream_error"


def _fallbackable(status_code: int):
    return status_code in (401, 403, 408, 429, 500, 502, 503, 504, 529)


def _transient_wait_seconds(status_code: int):
    return 5 if status_code in (502, 503, 504) else 0


def _chain_item(provider, key, model, status_code, error_msg, latency_ms):
    return {
        "provider_id": provider["id"],
        "provider": provider.get("name"),
        "key_id": key.get("id") if key else None,
        "key_label": key.get("label") if key else None,
        "model": model,
        "status_code": status_code,
        "error_kind": _error_kind(status_code, error_msg),
        "error": (error_msg or "")[:220],
        "latency_ms": latency_ms,
    }


def _provider_unsupported_reason(provider: dict, request_body: dict):
    has_tools = any(k in request_body for k in ("tools", "tool_choice", "function_call", "functions"))
    if provider.get("type") == "claude-cli":
        return None
    if has_tools and not provider.get("supports_tools", 1):
        return f"Provider '{provider['name']}' does not support tool calling"
    if request_body.get("stream") and not provider.get("supports_streaming", 1):
        return f"Provider '{provider['name']}' does not support streaming"
    if request_body.get("response_format") and not provider.get("supports_json_mode", 1):
        return f"Provider '{provider['name']}' does not support JSON response_format"
    return None


async def _prepare_upstream(provider: dict, request_body: dict, actual_model: str):
    request_body["model"] = actual_model
    _normalize_messages(request_body, provider, actual_model)
    rtk_enabled = (await db.get_setting("rtk_enabled")) == "true"
    request_body, rtk_stats = rtk.compress_request_body(request_body, enabled=rtk_enabled)
    if rtk_stats.changed:
        logger.info(
            "RTK compressed %s tool message(s), saved %s chars",
            rtk_stats.messages_compressed,
            rtk_stats.saved_chars,
        )

    prepared = build_request(provider, "chat", event_stream=bool(request_body.get("stream")))
    headers = prepared["headers"]
    url = prepared["url"]

    if provider_format(provider) == "anthropic-compatible":
        request_body = _openai_to_anthropic(request_body)
    elif request_body.get("stream"):
        stream_options = request_body.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        stream_options.setdefault("include_usage", True)
        request_body["stream_options"] = stream_options

    return {"url": url, "headers": headers, "body": request_body}


def _claude_cli_env(provider: dict, key: dict, actual_model: str) -> dict:
    env = os.environ.copy()
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    if provider.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = provider["base_url"].rstrip("/")
    if key and key.get("key_value"):
        env["ANTHROPIC_AUTH_TOKEN"] = key["key_value"]
        env["ANTHROPIC_API_KEY"] = key["key_value"]
    if actual_model:
        env["ANTHROPIC_MODEL"] = actual_model

    for name, value in parse_extra_headers(provider).items():
        if not isinstance(name, str):
            continue
        if name.startswith("CLAUDE_") or name.startswith("ANTHROPIC_") or name.startswith("AI_ROUTER_"):
            env[name] = str(value)
    return env


def _cli_tool_definitions(body: dict) -> list[dict]:
    tools = body.get("tools") or body.get("functions") or []
    result = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or tool
        name = function.get("name")
        if not name:
            continue
        result.append({
            "name": name,
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or function.get("input_schema") or {},
        })
    return result


def _cli_tool_instruction(body: dict) -> str | None:
    tools = _cli_tool_definitions(body)
    if not tools:
        return None

    choice = body.get("tool_choice") or body.get("function_call") or "auto"
    return (
        "You may call exactly one tool if needed.\n"
        "Available tools:\n"
        f"{json.dumps(tools, ensure_ascii=False)}\n\n"
        f"Tool choice: {json.dumps(choice, ensure_ascii=False)}\n\n"
        "If you need a tool, respond only with valid JSON in this exact shape:\n"
        '{"tool_call":{"name":"tool_name","arguments":{}}}\n'
        "If you do not need a tool, respond normally as plain text.\n"
        "Do not wrap JSON in markdown fences."
    )


def _request_to_cli_prompt(body: dict, native_anthropic: bool = False) -> str:
    parts = []
    system = body.get("system")
    if isinstance(system, list):
        system = "\n".join(_content_to_text(part.get("text") if isinstance(part, dict) else part) for part in system)
    if system:
        parts.append(f"System:\n{_content_to_text(system)}")

    if not native_anthropic:
        for msg in body.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "user"
            if role == "system":
                text = _content_to_text(msg.get("content"))
                if text:
                    parts.append(f"System:\n{text}")
                continue
            text = _content_to_text(msg.get("content"))
            if text:
                parts.append(f"{role.title()}:\n{text}")
    else:
        for msg in body.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            text = _content_to_text(msg.get("content"))
            if text:
                parts.append(f"{(msg.get('role') or 'user').title()}:\n{text}")

    json_instruction = _json_mode_instruction(body.get("response_format"))
    if json_instruction:
        parts.append(f"System:\n{json_instruction}")

    tool_instruction = _cli_tool_instruction(body)
    if tool_instruction:
        parts.append(f"System:\n{tool_instruction}")

    return "\n\n".join(parts).strip() or "Continue."


def _extract_cli_tool_call(text: str, request_body: dict) -> dict | None:
    if not _cli_tool_definitions(request_body):
        return None

    parsed = _jsonish_tool_arguments(text)
    if parsed is None:
        return None
    try:
        data = json.loads(parsed)
    except Exception:
        return None

    tool_call = data.get("tool_call") if isinstance(data, dict) else None
    if not isinstance(tool_call, dict):
        return None

    name = tool_call.get("name")
    allowed = {tool["name"] for tool in _cli_tool_definitions(request_body)}
    if not name or name not in allowed:
        return None

    arguments = tool_call.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, (dict, list)):
        return None

    return {
        "id": f"call_cli_{int(time.time() * 1000)}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _openai_cli_response(text: str, model: str, tokens_in: int, tokens_out: int, request_body: dict | None = None) -> dict:
    tool_call = _extract_cli_tool_call(text, request_body or {})
    message = {"role": "assistant", "content": text}
    finish_reason = "stop"
    if tool_call:
        message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        finish_reason = "tool_calls"

    return {
        "id": f"chatcmpl-cli-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


def _anthropic_cli_response(text: str, model: str, tokens_in: int, tokens_out: int) -> dict:
    return {
        "id": f"msg_cli_{int(time.time() * 1000)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


def _parse_claude_cli_json_output(out: str, request_body: dict) -> tuple[str, int, int, str | None]:
    try:
        data = json.loads(out)
    except Exception:
        tokens_in = _estimate_input_tokens(request_body)
        tokens_out = _estimate_output_tokens_from_response(out)
        return out, tokens_in, tokens_out, None

    if not isinstance(data, dict) or data.get("type") != "result":
        tokens_in = _estimate_input_tokens(request_body)
        tokens_out = _estimate_output_tokens_from_response(data)
        return out, tokens_in, tokens_out, None

    result = data.get("result") or ""
    if data.get("is_error"):
        return result, 0, 0, result or "Claude CLI returned an error"

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_in += int(usage.get("cache_creation_input_tokens") or 0)
    tokens_in += int(usage.get("cache_read_input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    if not tokens_in:
        tokens_in = _estimate_input_tokens(request_body)
    if not tokens_out:
        tokens_out = _estimate_output_tokens_from_response(result)
    return result, tokens_in, tokens_out, None


async def _run_claude_cli(provider: dict, key: dict, actual_model: str, request_body: dict, native_anthropic: bool = False):
    body = copy.deepcopy(request_body)
    body["model"] = actual_model
    if not native_anthropic:
        _normalize_messages(body, provider, actual_model)
        rtk_enabled = (await db.get_setting("rtk_enabled")) == "true"
        body, _ = rtk.compress_request_body(body, enabled=rtk_enabled)
    else:
        body.setdefault("max_tokens", 4096)

    prompt = _request_to_cli_prompt(body, native_anthropic=native_anthropic)
    binary = os.getenv("AI_ROUTER_CLAUDE_CLI_BINARY", "claude")
    command = [binary, "-p", "--bare", "--no-session-persistence", "--output-format", "json"]
    if actual_model:
        command.extend(["--model", actual_model])
    working_directory = (
        (provider.get("working_directory") or "").strip()
        or os.getenv("AI_ROUTER_CLAUDE_CLI_WORKDIR", "").strip()
        or os.path.expanduser("~")
    )
    if working_directory and not os.path.isdir(working_directory):
        return None, 500, 0, f"Claude CLI working directory not found: {working_directory}", 0, 0

    start = time.time()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_claude_cli_env(provider, key, actual_model),
            cwd=working_directory,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=_timeout_value(CLAUDE_CLI_TIMEOUT),
        )
    except asyncio.TimeoutError:
        latency = int((time.time() - start) * 1000)
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return None, 504, latency, f"Claude CLI timeout after {CLAUDE_CLI_TIMEOUT:g}s", 0, 0
    except FileNotFoundError:
        latency = int((time.time() - start) * 1000)
        return None, 500, latency, f"Claude CLI binary not found: {binary}", 0, 0
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return None, 500, latency, str(e)[:500], 0, 0

    latency = int((time.time() - start) * 1000)
    out = (stdout or b"").decode("utf-8", errors="replace").strip()
    err = (stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return None, 502, latency, (err or out or f"Claude CLI exited with code {proc.returncode}")[:500], 0, 0
    if not out:
        return None, 502, latency, (err or "Claude CLI returned empty output")[:500], 0, 0

    result_text, tokens_in, tokens_out, parsed_error = _parse_claude_cli_json_output(out, body)
    if parsed_error:
        return None, 502, latency, parsed_error[:500], tokens_in, tokens_out
    if native_anthropic:
        return _anthropic_cli_response(result_text, actual_model, tokens_in, tokens_out), 200, latency, None, tokens_in, tokens_out
    return _openai_cli_response(result_text, actual_model, tokens_in, tokens_out, body), 200, latency, None, tokens_in, tokens_out


async def _proxy_claude_cli_attempt(provider: dict, key: dict, actual_model: str, request_body: dict, local_key_id: str, fallback_chain: list, native_anthropic: bool = False):
    response_data, status, latency, error_msg, tokens_in, tokens_out = await _run_claude_cli(
        provider, key, actual_model, request_body, native_anthropic=native_anthropic
    )
    if status >= 400:
        await db.mark_key_error(key["id"], status, error_msg, actual_model)
        item = _chain_item(provider, key, actual_model, status, error_msg, latency)
        fallback_chain.append(item)
        await db.add_log(provider["id"], key["id"], actual_model, 0, 0, latency, status, error_msg, local_key_id, fallback_chain)
        return response_data, status, error_msg

    total_tokens = tokens_in + tokens_out
    await db.mark_key_success(key["id"], tokens_in, tokens_out)
    await db.clear_key_model_lock(key["id"], actual_model)
    await db.mark_key_used(key["id"])
    if local_key_id:
        await db.mark_local_key_used(local_key_id, total_tokens)
    await db.add_log(provider["id"], key["id"], actual_model, tokens_in, tokens_out, latency, status, local_key_id=local_key_id, fallback_chain=fallback_chain or None)
    return response_data, status, None


def _openai_stream_chunk(base_id: str, model: str, delta: dict, finish_reason=None):
    return {
        "id": base_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


def _synthetic_openai_stream(response_data: dict, model: str):
    async def generate():
        base_id = response_data.get("id") or f"chatcmpl-cli-stream-{int(time.time() * 1000)}"
        yield "data: " + json.dumps(_openai_stream_chunk(base_id, model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"

        choice = (response_data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason") or "stop"
        tool_calls = message.get("tool_calls")
        content = message.get("content")

        if tool_calls:
            stream_tool_calls = []
            for idx, tool_call in enumerate(tool_calls):
                item = dict(tool_call)
                item.setdefault("index", idx)
                stream_tool_calls.append(item)
            yield "data: " + json.dumps(
                _openai_stream_chunk(base_id, model, {"tool_calls": stream_tool_calls}),
                ensure_ascii=False,
            ) + "\n\n"
        elif content:
            yield "data: " + json.dumps(
                _openai_stream_chunk(base_id, model, {"content": content}),
                ensure_ascii=False,
            ) + "\n\n"

        yield "data: " + json.dumps(_openai_stream_chunk(base_id, model, {}, finish_reason), ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _claude_cli_live_synthetic_stream(provider: dict, key: dict, actual_model: str, request_body: dict, local_key_id: str, fallback_chain: list):
    async def generate():
        base_id = f"chatcmpl-cli-stream-{int(time.time() * 1000)}"
        yield "data: " + json.dumps(_openai_stream_chunk(base_id, actual_model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps(_openai_stream_chunk(base_id, actual_model, {"content": ""}), ensure_ascii=False) + "\n\n"

        body = copy.deepcopy(request_body)
        body["stream"] = False
        response_data, status, error_msg = await _proxy_claude_cli_attempt(
            provider, key, actual_model, body, local_key_id, fallback_chain
        )
        if status >= 400:
            error_text = error_msg or "Claude CLI stream failed"
            yield "data: " + json.dumps(
                _openai_stream_chunk(base_id, actual_model, {"content": error_text}, "stop"),
                ensure_ascii=False,
            ) + "\n\n"
            yield "data: [DONE]\n\n"
            return

        choice = (response_data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason") or "stop"
        tool_calls = message.get("tool_calls")
        content = message.get("content")

        if tool_calls:
            stream_tool_calls = []
            for idx, tool_call in enumerate(tool_calls):
                item = dict(tool_call)
                item.setdefault("index", idx)
                stream_tool_calls.append(item)
            yield "data: " + json.dumps(
                _openai_stream_chunk(base_id, actual_model, {"tool_calls": stream_tool_calls}),
                ensure_ascii=False,
            ) + "\n\n"
        elif content:
            yield "data: " + json.dumps(
                _openai_stream_chunk(base_id, actual_model, {"content": content}),
                ensure_ascii=False,
            ) + "\n\n"

        yield "data: " + json.dumps(_openai_stream_chunk(base_id, actual_model, {}, finish_reason), ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _normalize_messages(body: dict, provider: dict | None = None, model: str = ""):
    """Make chat history acceptable for stricter OpenAI-compatible validators."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return

    provider_key = ""
    if provider:
        provider_key = " ".join(
            str(provider.get(key, "")).lower()
            for key in ("name", "prefix", "base_url")
        )
    model_key = str(model or body.get("model") or "").lower()
    inject_all_reasoning = "deepseek" in provider_key or model_key.startswith("deepseek-")
    inject_tool_reasoning = "kimi" in provider_key or model_key.startswith("kimi-")

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        content = msg.get("content")
        if content == "":
            # Some agent clients emit empty assistant/tool/user content placeholders.
            # Several upstream gateways reject empty strings with min_length=1.
            msg["content"] = " "
            content = msg["content"]

        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text") == "":
                    part["text"] = " "

        if msg.get("role") == "assistant":
            has_tool_calls = isinstance(msg.get("tool_calls"), list) and bool(msg["tool_calls"])
            needs_reasoning = inject_all_reasoning or (inject_tool_reasoning and has_tool_calls)
            if needs_reasoning and not msg.get("reasoning_content"):
                # DeepSeek/Kimi-compatible gateways can reject assistant history unless
                # reasoning_content is echoed back as a non-empty string.
                msg["reasoning_content"] = " "


async def proxy_chat_completions(request_body: dict, headers: dict, local_key_id: str = None):
    """Proxy a chat completions request to the appropriate upstream provider."""
    model = request_body.get("model", "")
    stream = request_body.get("stream", False)

    attempts = await resolve_attempts(model)
    if not attempts:
        return {"error": f"No active provider found for model '{model}'"}, 502
    if attempts[0].get("source") == "combo_unavailable":
        return {
            "error": attempts[0].get("error") or "Combo has no available active models with alive keys",
            "fallback_chain": [{
                "provider_id": None,
                "provider": "combo",
                "model": model,
                "status_code": 502,
                "error_kind": "combo_unavailable",
                "error": attempts[0].get("error"),
                "latency_ms": 0,
            }],
        }, 502

    # Streaming keeps a single selected upstream because fallback after bytes are sent
    # can corrupt the SSE response. We still try the combo/provider/key chain before
    # the first byte is sent, then stay on the first upstream that opens cleanly.
    if stream:
        stream_response = await _proxy_stream_with_fallback(request_body, attempts, local_key_id)
        if isinstance(stream_response, tuple):
            return stream_response
        return stream_response, 200

    return await _proxy_with_fallback(request_body, attempts, local_key_id)


async def proxy_anthropic_messages(request_body: dict, headers: dict, local_key_id: str = None):
    """Proxy Anthropic-native /v1/messages requests to anthropic-compatible providers."""
    model = request_body.get("model", "")
    attempts = await resolve_attempts(model)
    if not attempts:
        return {"error": f"No active provider found for model '{model}'"}, 502
    if attempts[0].get("source") == "combo_unavailable":
        return {"error": attempts[0].get("error") or "Combo has no available active models with alive keys"}, 502

    last_error = "No anthropic-compatible provider found"
    last_status = 502
    fallback_chain = []

    async with httpx.AsyncClient(timeout=_upstream_timeout()) as client:
        for attempt in attempts:
            provider_id = attempt["provider_id"]
            actual_model = attempt["model"]
            if attempt.get("source") == "combo_unavailable":
                last_error = attempt.get("error") or "Combo has no available models"
                last_status = 502
                fallback_chain.append({
                    "provider_id": None,
                    "provider": "combo",
                    "model": actual_model,
                    "status_code": 502,
                    "error_kind": "combo_unavailable",
                    "error": last_error,
                    "latency_ms": 0,
                })
                continue
            provider = await db.get_provider(provider_id)
            if not provider or (provider_format(provider) != "anthropic-compatible" and provider.get("type") != "claude-cli"):
                continue

            unsupported = _provider_unsupported_reason(provider, request_body)
            if unsupported:
                last_error = unsupported
                last_status = 400
                fallback_chain.append(_chain_item(provider, None, actual_model, 400, unsupported, 0))
                continue

            keys = await db.list_alive_keys(provider_id, model=actual_model)
            if not keys:
                last_error = f"No alive API keys for provider '{provider['name']}'"
                fallback_chain.append(_chain_item(provider, None, actual_model, 502, last_error, 0))
                continue

            if provider.get("type") == "claude-cli":
                for key in keys:
                    response_data, status, error_msg = await _proxy_claude_cli_attempt(
                        provider, key, actual_model, request_body, local_key_id, fallback_chain, native_anthropic=True
                    )
                    if status < 400:
                        return response_data, status
                    last_error = error_msg
                    last_status = status
                    if not _fallbackable(status):
                        break
                wait_seconds = _transient_wait_seconds(last_status)
                if wait_seconds:
                    await asyncio.sleep(wait_seconds)
                continue

            prepared = _prepare_anthropic_native(provider, copy.deepcopy(request_body), actual_model)
            if prepared["body"].get("stream"):
                stream_response = await _proxy_anthropic_native_stream(prepared, provider, keys[0], actual_model, local_key_id)
                return stream_response, 200

            for key in keys:
                auth = build_request(provider, "chat", key["key_value"], event_stream=bool(prepared["body"].get("stream")))
                request_headers = {**prepared["headers"], **auth["headers"]}
                prepared_url = auth["url"]
                start = time.time()
                try:
                    resp = await client.post(prepared_url, headers=request_headers, json=prepared["body"])
                    latency = int((time.time() - start) * 1000)
                except httpx.TimeoutException as e:
                    latency = int((time.time() - start) * 1000)
                    error_msg = _timeout_message(e)
                    await db.mark_key_error(key["id"], 504, error_msg, actual_model)
                    fallback_chain.append(_chain_item(provider, key, actual_model, 504, error_msg, latency))
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 504, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 504
                    continue
                except Exception as e:
                    latency = int((time.time() - start) * 1000)
                    error_msg = str(e)[:500]
                    fallback_chain.append(_chain_item(provider, key, actual_model, 500, error_msg, latency))
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 500, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 500
                    continue

                if resp.status_code >= 400:
                    error_msg = resp.text[:500]
                    await db.mark_key_error(key["id"], resp.status_code, error_msg, actual_model)
                    fallback_chain.append(_chain_item(provider, key, actual_model, resp.status_code, error_msg, latency))
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, resp.status_code, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = resp.status_code
                    if _fallbackable(resp.status_code):
                        continue
                    break

                response_data = resp.json()
                tokens_in, tokens_out = _extract_anthropic_tokens(resp)
                tokens_in, tokens_out = _with_estimated_tokens(tokens_in, tokens_out, prepared["body"], response_data)
                total_tokens = tokens_in + tokens_out
                await db.mark_key_success(key["id"], tokens_in, tokens_out)
                await db.clear_key_model_lock(key["id"], actual_model)
                await db.mark_key_used(key["id"])
                if local_key_id:
                    await db.mark_local_key_used(local_key_id, total_tokens)
                await db.add_log(provider_id, key["id"], actual_model, tokens_in, tokens_out, latency, resp.status_code, local_key_id=local_key_id, fallback_chain=fallback_chain or None)
                return response_data, resp.status_code

            wait_seconds = _transient_wait_seconds(last_status)
            if wait_seconds:
                await asyncio.sleep(wait_seconds)

    return {"error": last_error, "fallback_chain": fallback_chain}, last_status


async def proxy_anthropic_count_tokens(request_body: dict):
    """Return an Anthropic-compatible token count estimate for gateway clients."""
    return {"input_tokens": _estimate_input_tokens(request_body)}, 200


async def _proxy_stream_attempt(request_body: dict, provider_id: str, actual_model: str, local_key_id: str = None):
    provider = await db.get_provider(provider_id)
    if not provider:
        return {"error": f"Provider '{provider_id}' not found"}, 502

    unsupported = _provider_unsupported_reason(provider, request_body)
    if unsupported:
        return {"error": unsupported}, 400
    
    key = await db.get_alive_key(provider_id, actual_model)
    if not key:
        return {"error": f"No alive API keys for provider '{provider['name']}'"}, 502

    prepared = await _prepare_upstream(provider, copy.deepcopy(request_body), actual_model)
    auth = build_request(provider, "chat", key["key_value"], event_stream=bool(prepared["body"].get("stream")))
    upstream_headers = {**prepared["headers"], **auth["headers"]}

    start = time.time()
    return await proxy_stream(auth["url"], upstream_headers, prepared["body"], provider_id, key["id"], actual_model, start, local_key_id, provider_format(provider))


async def _proxy_stream_with_fallback(request_body: dict, attempts: list, local_key_id: str = None):
    stream_attempts = []
    last_error = "No stream attempts were available"

    for attempt in attempts:
        provider_id = attempt["provider_id"]
        actual_model = attempt["model"]
        provider = await db.get_provider(provider_id)
        if not provider:
            last_error = f"Provider '{provider_id}' not found"
            continue

        unsupported = _provider_unsupported_reason(provider, request_body)
        if unsupported:
            last_error = unsupported
            continue

        keys = await db.list_alive_keys(provider_id, model=actual_model)
        if not keys:
            last_error = f"No alive API keys for provider '{provider['name']}'"
            continue

        if provider.get("type") == "claude-cli":
            return _claude_cli_live_synthetic_stream(provider, keys[0], actual_model, request_body, local_key_id, [])

        prepared = await _prepare_upstream(provider, copy.deepcopy(request_body), actual_model)
        for key in keys:
            auth = build_request(provider, "chat", key["key_value"], event_stream=bool(prepared["body"].get("stream")))
            stream_attempts.append({
                "url": auth["url"],
                "headers": {**prepared["headers"], **auth["headers"]},
                "body": prepared["body"],
                "provider_id": provider_id,
                "key_id": key["id"],
                "model": actual_model,
                "group": f"{provider_id}:{actual_model}",
                "start_time": time.time(),
                "local_key_id": local_key_id,
                "provider_type": provider_format(provider),
            })

    if not stream_attempts:
        return {"error": last_error}, 502

    first = stream_attempts[0]
    return await proxy_stream(
        first["url"],
        first["headers"],
        first["body"],
        first["provider_id"],
        first["key_id"],
        first["model"],
        first["start_time"],
        first.get("local_key_id"),
        first.get("provider_type"),
        fallback_attempts=stream_attempts,
    )


async def _proxy_with_fallback(request_body: dict, attempts: list, local_key_id: str = None):
    fallback_chain = []
    last_error = "No upstream attempts were made"
    last_status = 502

    async with httpx.AsyncClient(timeout=_upstream_timeout()) as client:
        for attempt in attempts:
            provider_id = attempt["provider_id"]
            actual_model = attempt["model"]
            provider = await db.get_provider(provider_id)
            if not provider:
                fallback_chain.append({
                    "provider_id": provider_id,
                    "model": actual_model,
                    "status_code": 502,
                    "error_kind": "provider_missing",
                    "error": "Provider not found",
                    "latency_ms": 0,
                })
                continue

            unsupported = _provider_unsupported_reason(provider, request_body)
            if unsupported:
                item = _chain_item(provider, None, actual_model, 400, unsupported, 0)
                fallback_chain.append(item)
                last_error = unsupported
                last_status = 400
                if len(attempts) == 1:
                    return {"error": unsupported, "fallback_chain": fallback_chain}, 400
                continue

            keys = await db.list_alive_keys(provider_id, model=actual_model)
            if not keys:
                msg = f"No alive API keys for provider '{provider['name']}'"
                fallback_chain.append(_chain_item(provider, None, actual_model, 502, msg, 0))
                last_error = msg
                last_status = 502
                continue

            if provider.get("type") == "claude-cli":
                for key in keys:
                    response_data, status, error_msg = await _proxy_claude_cli_attempt(
                        provider, key, actual_model, request_body, local_key_id, fallback_chain
                    )
                    if status < 400:
                        return response_data, status
                    last_error = error_msg
                    last_status = status
                    if not _fallbackable(status):
                        break
                wait_seconds = _transient_wait_seconds(last_status)
                if wait_seconds:
                    await asyncio.sleep(wait_seconds)
                continue

            prepared = await _prepare_upstream(provider, copy.deepcopy(request_body), actual_model)
            for key in keys:
                auth = build_request(provider, "chat", key["key_value"], event_stream=bool(prepared["body"].get("stream")))
                headers = {**prepared["headers"], **auth["headers"]}

                start = time.time()
                try:
                    resp = await client.post(auth["url"], headers=headers, json=prepared["body"])
                    latency = int((time.time() - start) * 1000)
                except httpx.TimeoutException as e:
                    latency = int((time.time() - start) * 1000)
                    error_msg = _timeout_message(e)
                    await db.mark_key_error(key["id"], 504, error_msg, actual_model)
                    item = _chain_item(provider, key, actual_model, 504, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 504, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 504
                    continue
                except Exception as e:
                    latency = int((time.time() - start) * 1000)
                    error_msg = str(e)[:500]
                    item = _chain_item(provider, key, actual_model, 500, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 500, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 500
                    continue

                if resp.status_code >= 400:
                    error_msg = resp.text[:500]
                    await db.mark_key_error(key["id"], resp.status_code, error_msg, actual_model)
                    item = _chain_item(provider, key, actual_model, resp.status_code, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, resp.status_code, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = resp.status_code
                    if _fallbackable(resp.status_code):
                        continue
                    break

                response_data = resp.json()
                tokens_in, tokens_out = _extract_tokens(resp)
                tokens_in, tokens_out = _with_estimated_tokens(tokens_in, tokens_out, prepared["body"], response_data)
                total_tokens = tokens_in + tokens_out
                await db.mark_key_success(key["id"], tokens_in, tokens_out)
                await db.clear_key_model_lock(key["id"], actual_model)
                await db.mark_key_used(key["id"])
                if local_key_id:
                    await db.mark_local_key_used(local_key_id, total_tokens)
                success_chain = fallback_chain if fallback_chain else None
                await db.add_log(provider_id, key["id"], actual_model, tokens_in, tokens_out, latency, resp.status_code, local_key_id=local_key_id, fallback_chain=success_chain)
                return _normalize_chat_response(response_data, provider, actual_model, request_body), resp.status_code

            wait_seconds = _transient_wait_seconds(last_status)
            if wait_seconds:
                await asyncio.sleep(wait_seconds)

    return {
        "error": last_error,
        "fallback_chain": fallback_chain,
    }, last_status


def _openai_to_anthropic(body: dict):
    """Convert OpenAI chat format to Anthropic messages format."""
    messages = body.get("messages", [])
    system_parts = []
    converted = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            text = _content_to_text(msg.get("content"))
            if text:
                system_parts.append(text)
            continue

        blocks = _anthropic_content_blocks(msg)
        if not blocks:
            continue

        converted_role = "user" if role in ("user", "tool") else "assistant"
        if converted and converted[-1]["role"] == converted_role and role != "tool":
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": converted_role, "content": blocks})

    result = {
        "model": body.get("model", "claude-3-haiku-20240307"),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 4096,
        "messages": converted,
    }
    if system_parts:
        result["system"] = "\n".join(system_parts)
    if body.get("temperature") is not None:
        result["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        result["top_p"] = body["top_p"]
    if body.get("stream"):
        result["stream"] = True
    if body.get("tools"):
        result["tools"] = _openai_tools_to_anthropic(body["tools"])
    if body.get("tool_choice"):
        result["tool_choice"] = _openai_tool_choice_to_anthropic(body["tool_choice"])
    if body.get("response_format"):
        json_instruction = _json_mode_instruction(body["response_format"])
        if json_instruction:
            result["system"] = "\n".join([p for p in (result.get("system"), json_instruction) if p])
    return result


def _anthropic_content_blocks(msg: dict):
    role = msg.get("role")
    content = msg.get("content")
    blocks = []

    if role == "tool":
        return [{
            "type": "tool_result",
            "tool_use_id": msg.get("tool_call_id") or msg.get("id") or "tool_call",
            "content": _content_to_text(content) or " ",
        }]

    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text" and part.get("text"):
                blocks.append({"type": "text", "text": part["text"]})
            elif part_type == "image_url":
                image_url = part.get("image_url") or {}
                url = image_url.get("url")
                if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
                    media_type, data = url[5:].split(";base64,", 1)
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
                elif isinstance(url, str) and url:
                    blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            elif part_type in ("tool_result", "tool_use", "image"):
                blocks.append(part)

    if role == "assistant":
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function") or {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or f"call_{len(blocks)}",
                "name": function.get("name") or tc.get("name") or "tool",
                "input": _try_json(function.get("arguments")),
            })

    return blocks


def _content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text") or "")
                elif "content" in part:
                    parts.append(str(part.get("content") or ""))
            elif part is not None:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _try_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except Exception:
            return value
    return value or {}


def _openai_tools_to_anthropic(tools):
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") and tool.get("type") != "function":
            converted.append(tool)
            continue
        function = tool.get("function") or tool
        converted.append({
            "name": function.get("name") or "tool",
            "description": function.get("description") or "",
            "input_schema": function.get("parameters") or function.get("input_schema") or {"type": "object", "properties": {}},
        })
    return converted


def _openai_tool_choice_to_anthropic(choice):
    if choice in (None, "auto", "none"):
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if isinstance(choice, dict):
        function = choice.get("function") or {}
        if function.get("name"):
            return {"type": "tool", "name": function["name"]}
        if choice.get("type") in ("auto", "any", "tool"):
            return choice
    return {"type": "auto"}


def _json_mode_instruction(response_format):
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") == "json_object":
        return "Respond only with valid JSON."
    if response_format.get("type") == "json_schema":
        schema = response_format.get("json_schema", {}).get("schema")
        if schema:
            return "Respond only with valid JSON matching this schema:\n" + json.dumps(schema, ensure_ascii=False)
    return None


def _normalize_chat_response(data: dict, provider: dict, model: str, request_body: dict | None = None):
    """Return a client-facing OpenAI chat completion shape where possible."""
    if not isinstance(data, dict):
        return data

    if provider_format(provider) == "anthropic-compatible" and data.get("type") == "message":
        return _anthropic_to_openai_response(data, model)

    if isinstance(data.get("choices"), list):
        data.setdefault("id", f"chatcmpl-{int(time.time() * 1000)}")
        data.setdefault("object", "chat.completion")
        data.setdefault("created", int(time.time()))
        data.setdefault("model", model)
        for choice in data["choices"]:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                message.setdefault("role", "assistant")
                if message.get("content") is None and not message.get("tool_calls"):
                    message["content"] = ""
                _synthesize_forced_tool_call(choice, message, request_body)
        return data

    return data


def _forced_tool_name(request_body: dict | None):
    if not isinstance(request_body, dict):
        return None
    choice = request_body.get("tool_choice") or request_body.get("function_call")
    if isinstance(choice, dict):
        function = choice.get("function") or {}
        name = function.get("name") or choice.get("name")
        if name:
            return name
    if isinstance(choice, str) and choice not in ("auto", "none", "required"):
        return choice
    tools = request_body.get("tools") or request_body.get("functions") or []
    if len(tools) == 1 and isinstance(tools[0], dict):
        function = tools[0].get("function") or tools[0]
        return function.get("name")
    return None


def _jsonish_tool_arguments(content):
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        json.loads(text)
    except Exception:
        return None
    return text


def _synthesize_forced_tool_call(choice: dict, message: dict, request_body: dict | None):
    """Compatibility for providers that answer forced tools as JSON text."""
    if message.get("tool_calls"):
        return
    tool_name = _forced_tool_name(request_body)
    if not tool_name:
        return
    arguments = _jsonish_tool_arguments(message.get("content"))
    if arguments is None:
        return
    message["tool_calls"] = [{
        "id": f"call_{int(time.time() * 1000)}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": arguments,
        },
    }]
    message["content"] = None
    choice["finish_reason"] = "tool_calls"


def _anthropic_to_openai_response(data: dict, model: str):
    text_parts = []
    tool_calls = []
    for idx, part in enumerate(data.get("content") or []):
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text_parts.append(part.get("text") or "")
        elif part.get("type") == "tool_use":
            tool_calls.append({
                "id": part.get("id") or f"call_{idx}",
                "type": "function",
                "function": {
                    "name": part.get("name") or "tool",
                    "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                },
            })

    usage = data.get("usage") or {}
    message = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": data.get("id") or f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": data.get("stop_reason") or "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def _prepare_anthropic_native(provider: dict, body: dict, actual_model: str):
    body["model"] = actual_model
    body.setdefault("max_tokens", 4096)
    prepared = build_request(provider, "chat", event_stream=bool(body.get("stream")))
    return {
        "url": prepared["url"],
        "headers": prepared["headers"],
        "body": body,
    }


async def _proxy_anthropic_native_stream(prepared: dict, provider: dict, key: dict, model: str, local_key_id: str = None):
    auth = build_request(provider, "chat", key["key_value"], event_stream=True)
    headers = {**prepared["headers"], **auth["headers"]}
    start = time.time()

    async def byte_generator():
        stream_timeout = httpx.Timeout(
            connect=STREAM_CONNECT_TIMEOUT,
            read=_timeout_value(STREAM_STALL_TIMEOUT),
            write=STREAM_CONNECT_TIMEOUT,
            pool=STREAM_CONNECT_TIMEOUT,
        )
        token_state = {"in": 0, "out": 0}
        output_state = {"chars": 0}
        buffer = ""

        def update_usage(usage):
            if not isinstance(usage, dict):
                return
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if isinstance(input_tokens, int):
                token_state["in"] = max(token_state["in"], input_tokens)
            if isinstance(output_tokens, int):
                token_state["out"] = max(token_state["out"], output_tokens)

        def scan_anthropic_usage(text: str):
            nonlocal buffer
            saw_stop = False
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except Exception:
                    continue
                if payload.get("type") == "message_start":
                    update_usage((payload.get("message") or {}).get("usage"))
                elif payload.get("type") == "message_delta":
                    update_usage(payload.get("usage"))
                elif payload.get("type") == "message_stop":
                    saw_stop = True
                elif payload.get("type") == "content_block_start":
                    block = payload.get("content_block") or {}
                    if isinstance(block.get("text"), str):
                        output_state["chars"] += len(block["text"])
                    if block.get("type") == "tool_use":
                        output_state["chars"] += len(json.dumps(block.get("input") or {}, ensure_ascii=False))
                elif payload.get("type") == "content_block_delta":
                    delta = payload.get("delta") or {}
                    for key in ("text", "thinking", "partial_json"):
                        value = delta.get(key)
                        if isinstance(value, str):
                            output_state["chars"] += len(value)
            return saw_stop

        async def finalize_stream_success(resp):
            nonlocal finalized
            if finalized:
                return
            finalized = True
            tokens_in = token_state["in"]
            tokens_out = token_state["out"]
            if not tokens_in and not tokens_out:
                tokens_in = _estimate_input_tokens(prepared.get("body") or {})
                tokens_out = _estimate_output_tokens_from_response("x" * output_state["chars"])
            if tokens_in or tokens_out:
                await db.mark_key_success(key["id"], tokens_in, tokens_out)
            latency = int((time.time() - start) * 1000)
            await db.add_log(provider["id"], key["id"], model, tokens_in, tokens_out, latency, resp.status_code, local_key_id=local_key_id)
            if local_key_id:
                await db.mark_local_key_used(local_key_id, tokens_in + tokens_out)

        stream_opened = False
        finalized = False
        try:
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream("POST", auth["url"], headers=headers, json=prepared["body"]) as resp:
                    if resp.status_code >= 400:
                        error_text = await resp.aread()
                        error_msg = error_text.decode(errors="replace")[:500]
                        await db.mark_key_error(key["id"], resp.status_code, error_msg, model)
                        latency = int((time.time() - start) * 1000)
                        await db.add_log(provider["id"], key["id"], model, 0, 0, latency, resp.status_code, error_msg, local_key_id)
                        yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': error_msg}}, ensure_ascii=False)}\n\n".encode()
                        return

                    await db.mark_key_success(key["id"])
                    await db.clear_key_model_lock(key["id"], model)
                    await db.mark_key_used(key["id"])
                    stream_opened = True

                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            saw_stop = scan_anthropic_usage(chunk.decode(errors="replace"))
                            if saw_stop:
                                await finalize_stream_success(resp)
                            yield chunk
                    if buffer.strip():
                        scan_anthropic_usage("\n")
                    await finalize_stream_success(resp)
        except httpx.TimeoutException:
            latency = int((time.time() - start) * 1000)
            timeout_msg = "Stream stalled waiting for upstream bytes"
            await db.mark_key_error(key["id"], 504, timeout_msg, model)
            await db.add_log(provider["id"], key["id"], model, 0, 0, latency, 504, timeout_msg, local_key_id)
            logger.warning(
                "Anthropic native stream timeout provider=%s key=%s model=%s latency_ms=%s error=%s",
                provider["id"],
                key["id"],
                model,
                latency,
                timeout_msg,
            )
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'timeout_error', 'message': timeout_msg}}, ensure_ascii=False)}\n\n".encode()
        except asyncio.CancelledError:
            if stream_opened and not finalized:
                try:
                    tokens_in = token_state["in"]
                    tokens_out = token_state["out"]
                    if not tokens_in and not tokens_out:
                        tokens_in = _estimate_input_tokens(prepared.get("body") or {})
                        tokens_out = _estimate_output_tokens_from_response("x" * output_state["chars"])
                    if tokens_in or tokens_out:
                        await asyncio.shield(db.mark_key_success(key["id"], tokens_in, tokens_out))
                    latency = int((time.time() - start) * 1000)
                    await asyncio.shield(db.add_log(provider["id"], key["id"], model, tokens_in, tokens_out, latency, 200, local_key_id=local_key_id))
                    if local_key_id:
                        await asyncio.shield(db.mark_local_key_used(local_key_id, tokens_in + tokens_out))
                except Exception:
                    logger.exception(
                        "Failed to finalize cancelled Anthropic native stream provider=%s key=%s model=%s",
                        provider["id"],
                        key["id"],
                        model,
                    )
            raise
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            error_msg = str(e)[:500]
            await db.add_log(provider["id"], key["id"], model, 0, 0, latency, 500, error_msg, local_key_id)
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': error_msg}}, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(
        byte_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    ), 200


def _extract_anthropic_tokens(resp):
    try:
        data = resp.json()
        usage = data.get("usage", {})
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    except Exception:
        return 0, 0


def _extract_tokens(resp):
    """Extract token usage from response."""
    try:
        data = resp.json()
        usage = data.get("usage", {})
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    except Exception:
        return 0, 0
