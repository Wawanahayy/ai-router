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
from .services import translator
from .services import error_policy
from .services.upstream import build_request, provider_format

logger = logging.getLogger(__name__)

UPSTREAM_CONNECT_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_CONNECT_TIMEOUT", "20"))
UPSTREAM_READ_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_READ_TIMEOUT", "300"))
UPSTREAM_WRITE_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_WRITE_TIMEOUT", "20"))
UPSTREAM_POOL_TIMEOUT = float(os.getenv("AI_ROUTER_UPSTREAM_POOL_TIMEOUT", "20"))
STRICT_CONTEXT_FILTER = os.getenv("AI_ROUTER_STRICT_CONTEXT_FILTER", "false").lower() in ("1", "true", "yes", "on")

# Proxy support — env var fallback (AI_ROUTER_PROXY) when DB settings not configured
UPSTREAM_PROXY_URL = os.getenv("AI_ROUTER_PROXY", "").strip()


async def _proxy_kwargs() -> dict:
    """Return proxy kwarg for httpx.AsyncClient if configured.
    Priority: DB settings (proxy_enabled + proxy_url) > env var AI_ROUTER_PROXY.
    """
    db_proxy = await db.get_proxy_url()
    proxy_url = db_proxy or UPSTREAM_PROXY_URL or None
    if proxy_url:
        return {"proxy": proxy_url}
    return {}
RESPONSES_SESSION_TTL = float(os.getenv("AI_ROUTER_RESPONSES_SESSION_TTL", "3600"))
RESPONSES_SESSION_MAX_MESSAGES = int(os.getenv("AI_ROUTER_RESPONSES_SESSION_MAX_MESSAGES", "120"))
TOOL_MIN_OUTPUT_TOKENS = int(os.getenv("AI_ROUTER_TOOL_MIN_OUTPUT_TOKENS", "32768"))
DISABLE_ANTHROPIC_THINKING_FOR_TOOLS = os.getenv(
    "AI_ROUTER_DISABLE_ANTHROPIC_THINKING_FOR_TOOLS",
    "true",
).lower() in ("1", "true", "yes", "on")
DEBUG_REQUEST_SHAPE = os.getenv("AI_ROUTER_DEBUG_REQUEST_SHAPE", "false").lower() in ("1", "true", "yes", "on")
_responses_sessions: dict[str, dict] = {}


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


def _prune_responses_sessions():
    if not _responses_sessions:
        return
    now = time.time()
    expired = [
        response_id
        for response_id, session in _responses_sessions.items()
        if now - float(session.get("updated_at") or 0) > RESPONSES_SESSION_TTL
    ]
    for response_id in expired:
        _responses_sessions.pop(response_id, None)


def _responses_history(response_id: str | None):
    _prune_responses_sessions()
    if not response_id:
        return []
    session = _responses_sessions.get(response_id)
    if not session:
        return []
    return copy.deepcopy(session.get("messages") or [])


def _store_responses_history(response_id: str, messages: list[dict]):
    if not response_id:
        return
    _prune_responses_sessions()
    clean_messages = [copy.deepcopy(message) for message in messages if isinstance(message, dict)]
    if RESPONSES_SESSION_MAX_MESSAGES > 0:
        clean_messages = clean_messages[-RESPONSES_SESSION_MAX_MESSAGES:]
    _responses_sessions[response_id] = {
        "messages": clean_messages,
        "updated_at": time.time(),
    }


def _estimate_input_tokens(body: dict) -> int:
    """Estimate input tokens with multi-language awareness.

    Different scripts have very different chars-per-token ratios:
      - English/Latin: ~4 chars/token
      - Indonesian: ~3 chars/token (similar to English, slight diff)
      - CJK (Chinese/Japanese/Korean): ~1.5 chars/token
      - Code/whitespace: ~3 chars/token
    """
    try:
        text = json.dumps(body or {}, ensure_ascii=False)
    except Exception:
        return 0
    if not text:
        return 0

    cjk_count = 0
    latin_count = 0
    digit_count = 0
    whitespace_count = 0
    other_count = 0

    for ch in text:
        cp = ord(ch)
        # CJK ranges: CJK Unified, Hiragana, Katakana, Hangul
        if (
            0x4E00 <= cp <= 0x9FFF       # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
            or 0x3040 <= cp <= 0x30FF    # Hiragana + Katakana
            or 0xAC00 <= cp <= 0xD7AF    # Hangul Syllables
            or 0x3000 <= cp <= 0x303F    # CJK Symbols
        ):
            cjk_count += 1
        elif ch.isalpha():
            latin_count += 1
        elif ch.isdigit():
            digit_count += 1
        elif ch.isspace():
            whitespace_count += 1
        else:
            other_count += 1

    # Token estimates per char class (closer to real tokenizer ratios)
    cjk_tokens = cjk_count / 1.5
    latin_tokens = latin_count / 4.0
    digit_tokens = digit_count / 4.0
    other_tokens = other_count / 3.0
    # Whitespace merges with adjacent tokens (BPE), minimal extra cost
    whitespace_extra = whitespace_count / 8.0

    return max(0, int(cjk_tokens + latin_tokens + digit_tokens + other_tokens + whitespace_extra))

def _requested_output_tokens(body: dict | None) -> int:
    if not isinstance(body, dict):
        return 0
    for key in ("max_completion_tokens", "max_tokens"):
        value = body.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return 0


def _coerce_positive_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _raise_tool_output_limit(body: dict | None, minimum: int = TOOL_MIN_OUTPUT_TOKENS):
    """Keep tool-call JSON from being truncated by too-small client output limits."""
    if not isinstance(body, dict) or minimum <= 0 or not translator.request_needs_tools(body):
        return

    present = [key for key in ("max_tokens", "max_completion_tokens") if key in body]
    if not present:
        body["max_tokens"] = minimum
        return

    for key in present:
        current = _coerce_positive_int(body.get(key))
        if current <= 0 or current < minimum:
            body[key] = minimum


def _disable_anthropic_thinking_for_tool_request(body: dict | None):
    if (
        DISABLE_ANTHROPIC_THINKING_FOR_TOOLS
        and isinstance(body, dict)
        and translator.request_needs_tools(body)
    ):
        body.pop("thinking", None)


def _text_len(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, str):
                total += len(item)
            elif isinstance(item, dict):
                total += _text_len(item.get("text"))
                total += _text_len(item.get("content"))
                total += _text_len(item.get("input"))
            elif item is not None:
                total += len(str(item))
        return total
    if isinstance(value, dict):
        total = 0
        for key in ("text", "content", "input", "arguments"):
            total += _text_len(value.get(key))
        return total
    return 0


def _message_shapes(body: dict | None):
    if not isinstance(body, dict):
        return []
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    shapes = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            shapes.append({"i": index, "type": type(message).__name__})
            continue
        content = message.get("content")
        part_types = []
        if isinstance(content, list):
            part_types = [
                part.get("type", type(part).__name__) if isinstance(part, dict) else type(part).__name__
                for part in content[:12]
            ]
        shapes.append({
            "i": index,
            "role": message.get("role"),
            "content_type": type(content).__name__,
            "content_chars": _text_len(content),
            "parts": part_types,
            "tool_calls": len(message.get("tool_calls") or []) if isinstance(message.get("tool_calls"), list) else 0,
            "tool_call_id": bool(message.get("tool_call_id")),
        })
    return shapes


def _tool_names(body: dict | None):
    if not isinstance(body, dict):
        return []
    names = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name") if isinstance(function, dict) else None
        if name:
            names.append(name)
    return names[:20]


def _nested_debug_flags(body: dict | None):
    if not isinstance(body, dict):
        return {}
    extra_body = body.get("extra_body") if isinstance(body.get("extra_body"), dict) else {}
    model_kwargs = body.get("model_kwargs") if isinstance(body.get("model_kwargs"), dict) else {}
    return {
        "has_thinking": "thinking" in body,
        "has_reasoning": "reasoning" in body,
        "has_reasoning_effort": "reasoning_effort" in body,
        "extra_body_keys": sorted(extra_body.keys()),
        "model_kwargs_keys": sorted(model_kwargs.keys()),
        "extra_body_has_thinking": "thinking" in extra_body,
        "extra_body_has_reasoning": "reasoning" in extra_body,
        "model_kwargs_has_thinking": "thinking" in model_kwargs,
        "model_kwargs_has_reasoning": "reasoning" in model_kwargs,
    }


def _debug_anthropic_tool_ids(body: dict):
    """Log tool_use IDs vs tool_result IDs to debug MISMATCH errors."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return
    tool_use_ids = {}  # id -> msg index
    tool_result_ids = {}  # id -> msg index
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_use_ids[block.get("id")] = idx
            elif block.get("type") == "tool_result":
                tool_result_ids[block.get("tool_use_id")] = idx
    orphan_results = set(tool_result_ids.keys()) - set(tool_use_ids.keys())
    orphan_uses = set(tool_use_ids.keys()) - set(tool_result_ids.keys())
    if orphan_results:
        detail = {rid: f"msg[{tool_result_ids[rid]}]" for rid in orphan_results}
        logger.warning("ANTHROPIC_TOOL_ID_MISMATCH tool_result refers to non-existent tool_use: %s", detail)
    if orphan_uses and len(orphan_uses) > 1:
        # More than 1 orphan use is suspicious (1 is normal - the last turn)
        detail = {uid: f"msg[{tool_use_ids[uid]}]" for uid in orphan_uses}
        logger.warning("ANTHROPIC_TOOL_ID_INFO multiple pending tool_use without result: %s", detail)


def _log_request_shape(stage: str, provider: dict | None, body: dict | None, actual_model: str = "", rtk_stats=None):
    if not DEBUG_REQUEST_SHAPE or not isinstance(body, dict):
        return
    payload = {
        "stage": stage,
        "provider": (provider or {}).get("name"),
        "provider_id": (provider or {}).get("id"),
        "format": provider_format(provider or {}) if provider else "",
        "model": actual_model or body.get("model"),
        "stream": bool(body.get("stream")),
        "needs_tools": translator.request_needs_tools(body),
        "tools": len(body.get("tools") or []),
        "tool_names": _tool_names(body),
        "tool_choice": body.get("tool_choice"),
        "max_tokens": body.get("max_tokens"),
        "max_completion_tokens": body.get("max_completion_tokens"),
        "max_output_tokens": body.get("max_output_tokens"),
        "estimated_input_tokens": _estimate_input_tokens(body),
        "messages": _message_shapes(body),
        **_nested_debug_flags(body),
    }
    if rtk_stats is not None:
        payload["rtk"] = {
            "changed": bool(rtk_stats.changed),
            "seen": rtk_stats.messages_seen,
            "compressed": rtk_stats.messages_compressed,
            "chars_before": rtk_stats.chars_before,
            "chars_after": rtk_stats.chars_after,
            "saved_chars": rtk_stats.saved_chars,
        }
    logger.warning("AI_ROUTER_REQUEST_SHAPE %s", json.dumps(payload, ensure_ascii=False, default=str))


def _response_shape(data):
    if not isinstance(data, dict):
        return {"type": type(data).__name__}

    shape = {
        "type": data.get("type"),
        "object": data.get("object"),
        "model": data.get("model"),
        "stop_reason": data.get("stop_reason"),
        "stop_sequence": data.get("stop_sequence"),
        "usage": data.get("usage") or data.get("usageMetadata"),
    }

    if isinstance(data.get("content"), list):
        blocks = []
        for index, part in enumerate(data.get("content") or []):
            if not isinstance(part, dict):
                blocks.append({"i": index, "type": type(part).__name__})
                continue
            part_type = part.get("type")
            blocks.append({
                "i": index,
                "type": part_type,
                "text_chars": _text_len(part.get("text") or part.get("thinking") or part.get("data")),
                "input_chars": _text_len(part.get("input")),
                "name": part.get("name") if part_type == "tool_use" else None,
            })
        shape["content_blocks"] = blocks

    if isinstance(data.get("choices"), list):
        choices = []
        for index, choice in enumerate(data.get("choices") or []):
            if not isinstance(choice, dict):
                choices.append({"i": index, "type": type(choice).__name__})
                continue
            message = choice.get("message") or choice.get("delta") or {}
            tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
            choices.append({
                "i": index,
                "finish_reason": choice.get("finish_reason"),
                "message_keys": sorted(message.keys()) if isinstance(message, dict) else [],
                "content_chars": _text_len(message.get("content")) if isinstance(message, dict) else 0,
                "reasoning_chars": _text_len(message.get("reasoning_content")) if isinstance(message, dict) else 0,
                "tool_calls": len(tool_calls or []) if isinstance(tool_calls, list) else 0,
                "tool_arg_chars": [
                    _text_len(((tool_call or {}).get("function") or {}).get("arguments"))
                    for tool_call in (tool_calls or [])[:10]
                    if isinstance(tool_call, dict)
                ] if isinstance(tool_calls, list) else [],
            })
        shape["choices"] = choices

    return shape


def _log_response_shape(stage: str, provider: dict | None, data, actual_model: str = "", status_code: int | None = None):
    if not DEBUG_REQUEST_SHAPE:
        return
    payload = {
        "stage": stage,
        "provider": (provider or {}).get("name"),
        "provider_id": (provider or {}).get("id"),
        "format": provider_format(provider or {}) if provider else "",
        "model": actual_model,
        "status_code": status_code,
        "response": _response_shape(data),
    }
    logger.warning("AI_ROUTER_RESPONSE_SHAPE %s", json.dumps(payload, ensure_ascii=False, default=str))


def _requested_context_tokens(body: dict | None) -> int:
    if not isinstance(body, dict):
        return 0
    return _estimate_input_tokens(body) + _requested_output_tokens(body)


def _estimate_output_tokens_from_response(data) -> int:
    """Estimate output tokens with multi-language awareness.

    Walks the response shape (OpenAI choices, Anthropic content blocks,
    Gemini candidates) and applies per-script char-to-token ratios so
    CJK-heavy completions are not undercounted.
    """
    if isinstance(data, str):
        return _chars_to_tokens(data)

    if not isinstance(data, dict):
        return 0

    chars = 0

    # OpenAI shape: choices[].message / choices[].delta
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or choice.get("delta") or {}
        if isinstance(message, dict):
            for key in ("content", "reasoning_content"):
                value = message.get(key)
                if isinstance(value, str):
                    chars += _count_weighted_chars(value)
            if message.get("tool_calls"):
                try:
                    chars += _count_weighted_chars(
                        json.dumps(message.get("tool_calls"), ensure_ascii=False)
                    )
                except Exception:
                    pass

    # Anthropic shape: content[].text / content[].input (tool_use)
    for part in data.get("content") or []:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            chars += _count_weighted_chars(part["text"])
        if part.get("type") == "tool_use":
            try:
                chars += _count_weighted_chars(
                    json.dumps(part.get("input") or {}, ensure_ascii=False)
                )
            except Exception:
                pass

    # Anthropic-style nested response (already in OpenAI shape)
    if data.get("response"):
        chars += _estimate_output_tokens_from_response(data["response"]) * 4

    # Gemini shape: candidates[].content.parts[].text
    if data.get("usageMetadata") is None and isinstance(data.get("andidates"), list) if False else isinstance(data.get("candidates"), list) and data.get("usageMetadata") is None:
        for candidate in data.get("candidates") or []:
            content = candidate.get("content") if isinstance(candidate, dict) else {}
            for part in content.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += _count_weighted_chars(part["text"])

    return max(0, int(chars)) if chars <= 0 else max(1, int(chars))


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3040 <= cp <= 0x30FF
        or 0xAC00 <= cp <= 0xD7AF
        or 0x3000 <= cp <= 0x303F
    )


def _count_weighted_chars(text: str) -> int:
    """Return weighted char count tuned for tokenizer ratios (1 weight = ~1 token)."""
    if not text:
        return 0
    cjk = 0
    latin = 0
    digit = 0
    other = 0
    whitespace = 0
    for ch in text:
        if _is_cjk_char(ch):
            cjk += 1
        elif ch.isalpha():
            latin += 1
        elif ch.isdigit():
            digit += 1
        elif ch.isspace():
            whitespace += 1
        else:
            other += 1
    return int(
        cjk / 1.5
        + latin / 4.0
        + digit / 4.0
        + other / 3.0
        + whitespace / 8.0
    )


def _chars_to_tokens(text: str) -> int:
    if not text:
        return 0
    n = _count_weighted_chars(text)
    return max(0, n) if n <= 0 else max(1, n)

def _with_estimated_tokens(tokens_in: int, tokens_out: int, request_body: dict, response_data) -> tuple[int, int]:
    """Reconcile reported usage with estimated tokens.

    Behavior:
      - Both reported values > 0  -> trust the upstream (already accurate).
      - Reported 0 / None        -> fall back to local estimate.
      - Suspicious imbalance     -> log warning and use max of estimate + reported.

    "Suspicious" = reported completion_tokens == 0 but response_data has visible content
    (or reported prompt_tokens == 0 but request_body is non-empty). Upstreams occasionally
    emit usage={0, 0} instead of leaving it absent, which would otherwise skip the
    fallback branch and silently undercount.
    """
    has_content = _response_has_content(response_data)
    has_request = bool(request_body)

    if tokens_in and tokens_out:
        return tokens_in, tokens_out

    estimated_in = _estimate_input_tokens(request_body)
    estimated_out = _estimate_output_tokens_from_response(response_data)

    if (tokens_in == 0 and estimated_in > 0) and has_request:
        tokens_in = estimated_in
    if (tokens_out == 0 and estimated_out > 0) and has_content:
        tokens_out = estimated_out

    if tokens_in == 0 and tokens_out == 0 and (has_content or has_request):
        try:
            logger.warning(
                "Upstream returned no usage; using local estimate. "
                "estimated_in=%d estimated_out=%d",
                estimated_in, estimated_out,
            )
        except Exception:
            pass

    return tokens_in, tokens_out


def _response_has_content(response_data) -> bool:
    """Quick check whether the response carries any visible text or tool calls."""
    if isinstance(response_data, str):
        return bool(response_data.strip())
    if not isinstance(response_data, dict):
        return False
    for choice in response_data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or choice.get("delta") or {}
        if not isinstance(message, dict):
            continue
        if message.get("content") or message.get("reasoning_content"):
            return True
        if message.get("tool_calls"):
            return True
    for part in response_data.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("text") or part.get("type") == "tool_use":
            return True
    for candidate in response_data.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                return True
    return False


async def _model_fits_context(model: str, requested_tokens: int, fallback_window: int = 0) -> bool:
    if not STRICT_CONTEXT_FILTER:
        return True
    if requested_tokens <= 0:
        return True
    window = await db.get_model_context_window(model)
    if not window and fallback_window:
        window = fallback_window
    return not window or requested_tokens <= window


async def _filter_attempts_by_context(candidates: list[dict], requested_tokens: int) -> list[dict]:
    if not candidates or requested_tokens <= 0:
        return candidates

    windows = []
    for candidate in candidates:
        window = await db.get_model_context_window(candidate.get("model_id") or candidate.get("model") or "")
        candidate["context_window"] = window
        if window > 0:
            windows.append(window)

    if not windows:
        return candidates

    default_window = min(windows)
    fits = []
    unknown = []
    over_limit = []
    for candidate in candidates:
        window = candidate.get("context_window") or default_window
        candidate["context_over_limit"] = bool(window and requested_tokens > window)
        if requested_tokens <= window:
            fits.append(candidate)
        elif not candidate.get("context_window"):
            unknown.append(candidate)
        else:
            over_limit.append(candidate)
    if STRICT_CONTEXT_FILTER:
        return fits
    return fits + unknown + over_limit


async def _filter_attempts_by_tools(candidates: list[dict], request_body: dict | None) -> list[dict]:
    if not candidates or not translator.request_needs_tools(request_body):
        return candidates
    filtered = []
    for candidate in candidates:
        provider = await db.get_provider(candidate.get("provider_id"))
        if provider and provider.get("supports_tools", 1):
            filtered.append(candidate)
    return filtered


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
    6. Unknown explicit model -> reject instead of routing randomly
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
        matching = [p for p in providers if p["prefix"] == prefix and p["is_active"] and p.get("prefix_enabled", 0)]
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


async def resolve_model_for_request(model: str, request_body: dict | None = None):
    if not model or not model.strip():
        return await _auto_pick_rr_for_request(request_body)

    requested_model = model.strip()
    lookup_model = requested_model.lower()
    needs_tools = translator.request_needs_tools(request_body)
    requested_tokens = _requested_context_tokens(request_body)

    alias = await db.resolve_alias(lookup_model)
    if alias:
        provider = await db.get_provider(alias["provider_id"])
        if (
            provider
            and (not needs_tools or provider.get("supports_tools", 1))
            and await _model_fits_context(alias["model_id"], requested_tokens)
        ):
            return alias["provider_id"], alias["model_id"]
        return None, requested_model

    providers = await db.list_providers()
    if "/" in lookup_model:
        prefix, _ = lookup_model.split("/", 1)
        _, actual_model = requested_model.split("/", 1)
        matching = [
            p for p in providers
            if p["prefix"] == prefix
            and p["is_active"]
            and p.get("prefix_enabled", 0)
            and (not needs_tools or p.get("supports_tools", 1))
        ]
        if matching:
            for p in matching:
                if await db.has_alive_key(p["id"], actual_model) and await _model_fits_context(actual_model, requested_tokens):
                    return p["id"], actual_model
        return None, requested_model

    matching = []
    for p in providers:
        if not p["is_active"] or (needs_tools and not p.get("supports_tools", 1)):
            continue
        for a in p.get("aliases", []):
            if a["model_id"].lower() == lookup_model and a.get("is_active", 1):
                if await db.has_alive_key(p["id"], a["model_id"]) and await _model_fits_context(a["model_id"], requested_tokens):
                    matching.append((p, a["model_id"]))
                break
    if matching:
        rr_key = "tool_model_rr" if needs_tools else "model_rr"
        rr = await db._get_rr(f"{rr_key}_{lookup_model}")
        picked, actual_model = matching[rr % len(matching)]
        await db._set_rr(f"{rr_key}_{lookup_model}", (rr + 1) % len(matching))
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


async def _auto_pick_rr_for_request(request_body: dict | None = None):
    providers = await db.list_providers()
    available = []
    needs_tools = translator.request_needs_tools(request_body)
    requested_tokens = _requested_context_tokens(request_body)
    for p in providers:
        if not p["is_active"]:
            continue
        if needs_tools and not p.get("supports_tools", 1):
            continue
        if await db.has_alive_key(p["id"]):
            alias_model = ""
            for a in p.get("aliases", []):
                if a.get("is_active", 1) and await _model_fits_context(a["model_id"], requested_tokens):
                    alias_model = a["model_id"]
                    break
            if alias_model or not requested_tokens:
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


async def resolve_attempts(model: str, request_body: dict | None = None):
    """Resolve request model into ordered provider/model attempts."""
    requested = (model or "").strip()
    requested_tokens = _requested_context_tokens(request_body)
    if requested:
        combo_candidates = await db.resolve_combo_candidates(requested.lower())
        combo_candidates = await _filter_attempts_by_context(combo_candidates, requested_tokens)
        combo_candidates = await _filter_attempts_by_tools(combo_candidates, request_body)
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
                "error": f"Combo '{requested}' has no available active models with alive keys or enough context",
            }]

    provider_id, actual_model = await resolve_model_for_request(model, request_body)
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
    return error_policy.error_kind(status_code, error_msg)


def _fallbackable(status_code: int, error_msg: str = ""):
    return error_policy.fallbackable(status_code, error_msg)


def _transient_wait_seconds(status_code: int, error_msg: str = ""):
    return error_policy.transient_wait_seconds(status_code, error_msg)


def _rate_limit_wait_seconds(attempt_count: int):
    return error_policy.rate_limit_wait_seconds(attempt_count)


def _openai_error_type(status_code: int, error_msg: str = ""):
    return error_policy.openai_error_type(status_code, error_msg)


def _error_response(message: str, status_code: int = 502, fallback_chain=None):
    return error_policy.error_response(message, status_code, fallback_chain)


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

def _filter_anthropic_native_tool_text(data):
    if not isinstance(data, dict):
        return data
    content = data.get("content")
    if not isinstance(content, list):
        return data
    has_tool = any(isinstance(part, dict) and part.get("type") == "tool_use" for part in content)
    if not has_tool:
        return data
    clean = copy.deepcopy(data)
    clean["content"] = [
        part for part in content
        if isinstance(part, dict) and part.get("type") not in ("text", "thinking", "redacted_thinking")
    ]
    return clean

def _provider_unsupported_reason(provider: dict, request_body: dict):
    if translator.request_needs_tools(request_body) and not provider.get("supports_tools", 1):
        return f"Provider '{provider['name']}' does not support tool calling"
    if request_body.get("stream") and not provider.get("supports_streaming", 1):
        return f"Provider '{provider['name']}' does not support streaming"
    if request_body.get("response_format") and not provider.get("supports_json_mode", 1):
        return f"Provider '{provider['name']}' does not support JSON response_format"
    return None


async def _prepare_upstream(provider: dict, request_body: dict, actual_model: str):
    request_body["model"] = actual_model
    tool_stats = translator.normalize_messages(request_body, provider, actual_model)
    _log_request_shape("pre_rtk", provider, request_body, actual_model)
    
    # Auto-inject reasoning_content for thinking models (DeepSeek V4 Pro, etc)
    from . import reasoning_middleware
    reasoning_middleware.inject_reasoning_content(request_body, actual_model)
    if tool_stats["tools_declared"] or tool_stats["assistant_tool_calls"] or tool_stats["tool_results"]:
        logger.info(
            "Tool flow provider=%s model=%s tools=%s assistant_calls=%s tool_results=%s missing_ids=%s orphan_results=%s",
            provider.get("name"),
            actual_model,
            tool_stats["tools_declared"],
            tool_stats["assistant_tool_calls"],
            tool_stats["tool_results"],
            tool_stats["missing_tool_call_id"],
            tool_stats["orphan_tool_results"],
        )
    rtk_setting = await db.get_setting("rtk_enabled")
    rtk_enabled = str(rtk_setting).lower() != "false"
    request_body, rtk_stats = rtk.compress_request_body(request_body, enabled=rtk_enabled)
    _log_request_shape("post_rtk", provider, request_body, actual_model, rtk_stats)
    if rtk_stats.changed:
        logger.info(
            "RTK compressed %s tool message(s), saved %s chars",
            rtk_stats.messages_compressed,
            rtk_stats.saved_chars,
        )

    _raise_tool_output_limit(request_body)
    if "max_tokens" not in request_body and "max_completion_tokens" not in request_body:
        request_body["max_tokens"] = 16384
    if provider_format(provider) == "anthropic-compatible":
        _disable_anthropic_thinking_for_tool_request(request_body)

    prepared = build_request(provider, "chat", event_stream=bool(request_body.get("stream")))
    headers = prepared["headers"]
    url = prepared["url"]

    if provider_format(provider) == "anthropic-compatible":
        request_body = translator.openai_to_anthropic(request_body)
        # DEBUG: log tool_use/tool_result ID pairs to diagnose MISMATCH errors
        _debug_anthropic_tool_ids(request_body)
        _log_request_shape("post_anthropic_translate", provider, request_body, actual_model)
    elif request_body.get("stream"):
        stream_options = request_body.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        stream_options.setdefault("include_usage", True)
        request_body["stream_options"] = stream_options
    _log_request_shape("prepared_upstream", provider, request_body, actual_model)

    return {"url": url, "headers": headers, "body": request_body}


async def proxy_chat_completions(request_body: dict, headers: dict, local_key_id: str = None):
    """Proxy a chat completions request to the appropriate upstream provider."""
    model = request_body.get("model", "")
    stream = request_body.get("stream", False)

    attempts = await resolve_attempts(model, request_body)
    if not attempts:
        await db.add_log(None, None, model, 0, 0, 0, 502, f"No active provider found for model '{model}'", local_key_id)
        return _error_response(f"No active provider found for model '{model}'", 502), 502
    if attempts[0].get("source") == "combo_unavailable":
        combo_error = attempts[0].get("error") or "Combo has no available active models with alive keys"
        combo_chain = [{
            "provider_id": None,
            "provider": "combo",
            "model": model,
            "status_code": 502,
            "error_kind": "combo_unavailable",
            "error": combo_error,
            "latency_ms": 0,
        }]
        await db.add_log(None, None, model, 0, 0, 0, 502, combo_error, local_key_id, combo_chain)
        return _error_response(combo_error, 502, combo_chain), 502

    # Streaming keeps a single selected upstream because fallback after bytes are sent
    # can corrupt the SSE response. We still try the combo/provider/key chain before
    # the first byte is sent, then stay on the first upstream that opens cleanly.
    if stream:
        return await _proxy_stream_with_fallback(request_body, attempts, local_key_id)

    return await _proxy_with_fallback(request_body, attempts, local_key_id)


async def proxy_responses(request_body: dict, headers: dict, local_key_id: str = None):
    wants_stream = bool(request_body.get("stream"))
    previous_response_id = request_body.get("previous_response_id")
    chat_body = translator.responses_to_chat_request(request_body)
    previous_messages = _responses_history(previous_response_id)
    if previous_messages:
        chat_body["messages"] = previous_messages + (chat_body.get("messages") or [])
    chat_body["stream"] = False
    result, status = await proxy_chat_completions(chat_body, headers, local_key_id)
    if isinstance(result, dict) and "error" in result:
        return result, status
    response_body = translator.chat_to_responses_response(result, request_body)
    assistant_messages = translator.chat_response_to_history_messages(result)
    _store_responses_history(
        response_body["id"],
        (chat_body.get("messages") or []) + assistant_messages,
    )
    if not wants_stream:
        return response_body, status

    async def event_generator():
        sequence = 0

        def event_bytes(event_type: str, payload: dict):
            nonlocal sequence
            sequence += 1
            if isinstance(payload, dict):
                payload.setdefault("type", event_type)
                payload.setdefault("sequence_number", sequence)
            return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        created = {
            "type": "response.created",
            "response": {
                "id": response_body["id"],
                "object": "response",
                "created_at": response_body["created_at"],
                "status": "in_progress",
                "model": response_body["model"],
            },
        }
        yield event_bytes("response.created", created)
        for output_index, item in enumerate(response_body.get("output") or []):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{output_index}"
                item_id = item.get("id") or f"fc_{call_id}"
                name = item.get("name") or "tool"
                arguments = item.get("arguments") or "{}"
                added_item = {
                    "id": item_id,
                    "type": "function_call",
                    "arguments": "",
                    "call_id": call_id,
                    "name": name,
                }
                yield event_bytes("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": added_item,
                })
                if arguments:
                    yield event_bytes("response.function_call_arguments.delta", {
                        "type": "response.function_call_arguments.delta",
                        "item_id": item_id,
                        "output_index": output_index,
                        "delta": arguments,
                    })
                yield event_bytes("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "arguments": arguments,
                })
                done_item = {
                    "id": item_id,
                    "type": "function_call",
                    "arguments": arguments,
                    "call_id": call_id,
                    "name": name,
                }
                yield event_bytes("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": output_index,
                    "item": done_item,
                })
                continue

            if item_type == "message":
                item_id = item.get("id") or f"msg_{response_body['id']}_{output_index}"
                content = item.get("content") or []
                text = ""
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text += str(part.get("text") or "")
                yield event_bytes("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": {"id": item_id, "type": "message", "content": [], "role": "assistant"},
                })
                yield event_bytes("response.content_part.added", {
                    "type": "response.content_part.added",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": ""},
                })
                if text:
                    yield event_bytes("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": text,
                        "logprobs": [],
                    })
                yield event_bytes("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "text": text,
                    "logprobs": [],
                })
                done_part = {"type": "output_text", "annotations": [], "logprobs": [], "text": text}
                yield event_bytes("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": done_part,
                })
                yield event_bytes("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": output_index,
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "content": [done_part],
                        "role": "assistant",
                    },
                })
                continue

            yield event_bytes("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": item,
            })
            yield event_bytes("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": item,
            })

        completed = {"type": "response.completed", "response": response_body}
        yield event_bytes("response.completed", completed)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    ), status


async def proxy_anthropic_messages(request_body: dict, headers: dict, local_key_id: str = None):
    """Proxy Anthropic-native /v1/messages requests to anthropic-compatible providers."""
    model = request_body.get("model", "")
    attempts = await resolve_attempts(model, request_body)
    if not attempts:
        return _error_response(f"No active provider found for model '{model}'", 502), 502
    if attempts[0].get("source") == "combo_unavailable":
        message = attempts[0].get("error") or "Combo has no available active models with alive keys"
        return _error_response(message, 502), 502

    last_error = "No anthropic-compatible provider found"
    last_status = 502
    fallback_chain = []

    _pk = await _proxy_kwargs()
    async with httpx.AsyncClient(timeout=_upstream_timeout(), **_pk) as client:
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
            if not provider or provider_format(provider) != "anthropic-compatible":
                continue

            if request_body.get("stream") and not provider.get("supports_streaming", 1):
                last_error = f"Provider '{provider['name']}' does not support streaming"
                last_status = 400
                fallback_chain.append(_chain_item(provider, None, actual_model, 400, last_error, 0))
                continue

            keys = await db.list_alive_keys(provider_id, advance=False, model=actual_model)
            if not keys:
                last_error = f"No alive API keys for provider '{provider['name']}'"
                fallback_chain.append(_chain_item(provider, None, actual_model, 502, last_error, 0))
                continue

            prepared = _prepare_anthropic_native(provider, copy.deepcopy(request_body), actual_model)
            if prepared["body"].get("stream"):
                return await _proxy_anthropic_native_stream(prepared, provider, keys[0], actual_model, local_key_id)

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
                    await db.mark_key_error(key["id"], 504, error_msg, actual_model, provider.get("name", ""))
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
                    error_msg = error_policy._sanitize_error(resp.text[:2000], status_code=resp.status_code)
                    await db.mark_key_error(key["id"], resp.status_code, error_msg, actual_model, provider.get("name", ""))
                    fallback_chain.append(_chain_item(provider, key, actual_model, resp.status_code, error_msg, latency))
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, resp.status_code, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = resp.status_code
                    if _fallbackable(resp.status_code, error_msg):
                        continue
                    break

                try:
                    response_data = resp.json()
                    _log_response_shape("anthropic_native_upstream", provider, response_data, actual_model, resp.status_code)
                except Exception as e:
                    error_msg = error_policy._sanitize_error(f"Invalid JSON response: {e}", status_code=502)
                    await db.mark_key_error(key["id"], 502, error_msg, actual_model, provider.get("name", ""))
                    fallback_chain.append(_chain_item(provider, key, actual_model, 502, error_msg, latency))
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 502, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 502
                    continue
                tokens_in, tokens_out = _extract_anthropic_tokens(resp)
                tokens_in, tokens_out = _with_estimated_tokens(tokens_in, tokens_out, prepared["body"], response_data)
                total_tokens = tokens_in + tokens_out
                await db.mark_key_success(key["id"], tokens_in, tokens_out)
                await db.clear_key_model_lock(key["id"], actual_model)
                await db.mark_key_used(key["id"])
                if local_key_id:
                    await db.mark_local_key_used(local_key_id, total_tokens)
                success_chain = fallback_chain if fallback_chain else None
                await db.add_log(
                    provider_id,
                    key["id"],
                    actual_model,
                    tokens_in,
                    tokens_out,
                    latency,
                    resp.status_code,
                    local_key_id=local_key_id,
                    fallback_chain=success_chain,
                )
                return response_data, resp.status_code

            wait_seconds = _transient_wait_seconds(last_status, last_error)
            if wait_seconds:
                await asyncio.sleep(wait_seconds)

    return _error_response(last_error, last_status, fallback_chain), last_status


async def _proxy_stream_attempt(request_body: dict, provider_id: str, actual_model: str, local_key_id: str = None):
    provider = await db.get_provider(provider_id)
    if not provider:
        return _error_response(f"Provider '{provider_id}' not found", 502), 502

    unsupported = _provider_unsupported_reason(provider, request_body)
    if unsupported:
        return _error_response(unsupported, 400), 400
    
    key = await db.get_alive_key(provider_id, advance=False, model=actual_model)
    if not key:
        return _error_response(f"No alive API keys for provider '{provider['name']}'", 502), 502

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

        keys = await db.list_alive_keys(provider_id, advance=False, model=actual_model)
        if not keys:
            last_error = f"No alive API keys for provider '{provider['name']}'"
            continue

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
                "provider_name": provider.get("name", ""),
            })

    if not stream_attempts:
        return _error_response(last_error, 502), 502

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

    _pk = await _proxy_kwargs()
    async with httpx.AsyncClient(timeout=_upstream_timeout(), **_pk) as client:
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
                    return _error_response(unsupported, 400, fallback_chain), 400
                continue

            keys = await db.list_alive_keys(provider_id, advance=False, model=actual_model)
            if not keys:
                msg = f"No alive API keys for provider '{provider['name']}'"
                fallback_chain.append(_chain_item(provider, None, actual_model, 502, msg, 0))
                last_error = msg
                last_status = 502
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
                    await db.mark_key_error(key["id"], 504, error_msg, actual_model, provider.get("name", ""))
                    item = _chain_item(provider, key, actual_model, 504, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 504, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 504
                    continue
                except Exception as e:
                    latency = int((time.time() - start) * 1000)
                    error_msg = error_policy._sanitize_error(str(e)[:2000], status_code=500)
                    item = _chain_item(provider, key, actual_model, 500, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 500, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 500
                    continue

                if resp.status_code >= 400:
                    error_msg = error_policy._sanitize_error(resp.text[:2000], status_code=resp.status_code)
                    await db.mark_key_error(key["id"], resp.status_code, error_msg, actual_model, provider.get("name", ""))
                    item = _chain_item(provider, key, actual_model, resp.status_code, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, resp.status_code, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = resp.status_code
                    if _fallbackable(resp.status_code, error_msg):
                        continue
                    break

                try:
                    raw_response_data = resp.json()
                    _log_response_shape("chat_upstream_raw", provider, raw_response_data, actual_model, resp.status_code)
                    response_data = _filter_anthropic_native_tool_text(raw_response_data)
                    if response_data is not raw_response_data:
                        _log_response_shape("chat_upstream_filtered", provider, response_data, actual_model, resp.status_code)
                except Exception as e:
                    error_msg = error_policy._sanitize_error(f"Invalid JSON response: {e}", status_code=502)
                    await db.mark_key_error(key["id"], 502, error_msg, actual_model, provider.get("name", ""))
                    item = _chain_item(provider, key, actual_model, 502, error_msg, latency)
                    fallback_chain.append(item)
                    await db.add_log(provider_id, key["id"], actual_model, 0, 0, latency, 502, error_msg, local_key_id, fallback_chain)
                    last_error = error_msg
                    last_status = 502
                    continue
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
                if fallback_chain:
                    prev = fallback_chain[-1]
                    logger.info(
                        "Combo fallback detected: %s/%s -> %s/%s (model=%s, prev_status=%s %s, latency=%sms)",
                        prev.get("provider"), prev.get("key_label"),
                        provider.get("name"), key.get("label"),
                        actual_model,
                        prev.get("status_code"), prev.get("error_kind"),
                        latency,
                    )
                client_response = translator.normalize_chat_response(response_data, provider, actual_model, request_body)
                _log_response_shape("client_response", provider, client_response, actual_model, resp.status_code)
                response_tool_stats = translator.response_tool_stats(client_response)
                if request_body.get("tools") or request_body.get("tool_choice") or response_tool_stats["tool_calls"]:
                    logger.info(
                        "Tool response provider=%s model=%s finish=%s tool_calls=%s content=%s reasoning=%s",
                        provider.get("name"),
                        actual_model,
                        response_tool_stats["finish_reason"],
                        response_tool_stats["tool_calls"],
                        response_tool_stats["has_content"],
                        response_tool_stats["has_reasoning"],
                    )
                return client_response, resp.status_code

            wait_seconds = _transient_wait_seconds(last_status, last_error)
            if wait_seconds:
                await asyncio.sleep(wait_seconds)

    return _error_response(last_error, last_status, fallback_chain), last_status


def _prepare_anthropic_native(provider: dict, body: dict, actual_model: str):
    body["model"] = actual_model
    translator.normalize_messages(body, provider, actual_model)
    _log_request_shape("anthropic_native_pre_prepare", provider, body, actual_model)
    _raise_tool_output_limit(body)
    _disable_anthropic_thinking_for_tool_request(body)
    body.setdefault("max_tokens", 40960)
    _log_request_shape("anthropic_native_pre_send", provider, body, actual_model)
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

        native_filter_enabled = translator.request_needs_tools(prepared.get("body"))
        native_filter_state = {"buffer": "", "held": [], "held_indexes": set(), "tool_seen": False}

        def anthropic_sse_payload(block: str):
            data_lines = []
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith("data:"):
                    data_lines.append(stripped[5:].strip())
            data = "\n".join(part for part in data_lines if part)
            if not data or data == "[DONE]":
                return None
            try:
                return json.loads(data)
            except Exception:
                return None

        def flush_native_held():
            if not native_filter_state["held"]:
                return []
            held = native_filter_state["held"]
            native_filter_state["held"] = []
            native_filter_state["held_indexes"] = set()
            return held

        def filter_native_anthropic_block(block: str):
            if not native_filter_enabled:
                return [block]
            payload = anthropic_sse_payload(block)
            if not isinstance(payload, dict):
                return [block]
            event_type = payload.get("type")
            if event_type == "content_block_start":
                index = payload.get("index", 0)
                content_block = payload.get("content_block") or {}
                block_type = content_block.get("type")
                if block_type == "tool_use":
                    native_filter_state["tool_seen"] = True
                    native_filter_state["held"] = []
                    native_filter_state["held_indexes"] = set()
                    return [block]
                if block_type in ("text", "thinking", "redacted_thinking"):
                    native_filter_state["held_indexes"].add(index)
                    native_filter_state["held"].append(block)
                    return []
                return [block]
            if event_type == "content_block_delta":
                index = payload.get("index", 0)
                delta = payload.get("delta") or {}
                delta_type = delta.get("type")
                if index in native_filter_state["held_indexes"] or delta_type in ("text_delta", "thinking_delta"):
                    native_filter_state["held_indexes"].add(index)
                    native_filter_state["held"].append(block)
                    return []
                if delta_type == "input_json_delta":
                    native_filter_state["tool_seen"] = True
                    native_filter_state["held"] = []
                    native_filter_state["held_indexes"] = set()
                return [block]
            if event_type == "content_block_stop":
                index = payload.get("index", 0)
                if index in native_filter_state["held_indexes"]:
                    native_filter_state["held"].append(block)
                    return []
                return [block]
            if event_type == "message_delta":
                stop_reason = (payload.get("delta") or {}).get("stop_reason")
                if stop_reason == "tool_use" or native_filter_state["tool_seen"]:
                    native_filter_state["tool_seen"] = True
                    native_filter_state["held"] = []
                    native_filter_state["held_indexes"] = set()
                    return [block]
                return flush_native_held() + [block]
            if event_type == "message_stop":
                if native_filter_state["tool_seen"]:
                    native_filter_state["held"] = []
                    native_filter_state["held_indexes"] = set()
                    return [block]
                return flush_native_held() + [block]
            return [block]

        def filter_native_anthropic_chunk(text: str):
            if not native_filter_enabled:
                return [text] if text else []
            outputs = []
            native_filter_state["buffer"] += text
            while "\n\n" in native_filter_state["buffer"]:
                block, native_filter_state["buffer"] = native_filter_state["buffer"].split("\n\n", 1)
                if block:
                    outputs.extend(filter_native_anthropic_block(block + "\n\n"))
            return outputs

        def flush_native_anthropic_filter():
            outputs = []
            if native_filter_state["buffer"]:
                outputs.extend(filter_native_anthropic_block(native_filter_state["buffer"]))
                native_filter_state["buffer"] = ""
            if not native_filter_state["tool_seen"]:
                outputs = flush_native_held() + outputs
            else:
                native_filter_state["held"] = []
                native_filter_state["held_indexes"] = set()
            return outputs
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
            _pk = await _proxy_kwargs()
            async with httpx.AsyncClient(timeout=stream_timeout, **_pk) as client:
                async with client.stream("POST", auth["url"], headers=headers, json=prepared["body"]) as resp:
                    if resp.status_code >= 400:
                        error_text = await resp.aread()
                        error_msg = error_text.decode(errors="replace")[:500]
                        await db.mark_key_error(key["id"], resp.status_code, error_msg, model)
                        latency = int((time.time() - start) * 1000)
                        await db.add_log(provider["id"], key["id"], model, 0, 0, latency, resp.status_code, error_msg, local_key_id)
                        if resp.status_code == 429:
                            await asyncio.sleep(_rate_limit_wait_seconds(1))
                        yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': error_msg}}, ensure_ascii=False)}\n\n".encode()
                        return

                    await db.mark_key_success(key["id"])
                    await db.clear_key_model_lock(key["id"], model)
                    await db.mark_key_used(key["id"])
                    stream_opened = True

                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            text = chunk.decode(errors="replace")
                            saw_stop = scan_anthropic_usage(text)
                            if saw_stop:
                                await finalize_stream_success(resp)
                            for output in filter_native_anthropic_chunk(text):
                                yield output.encode() if isinstance(output, str) else output
                    if buffer.strip():
                        scan_anthropic_usage("\n")
                    for output in flush_native_anthropic_filter():
                        yield output.encode() if isinstance(output, str) else output
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
    """Extract Anthropic-style token usage. Returns (in, out) as ints; bad/missing -> (0, 0)."""
    try:
        data = resp.json()
    except Exception:
        return 0, 0
    usage = data.get("usage") or {}
    raw_in = usage.get("input_tokens")
    raw_out = usage.get("output_tokens")
    if raw_in is None or raw_out is None:
        return 0, 0
    try:
        return max(0, int(raw_in)), max(0, int(raw_out))
    except (TypeError, ValueError):
        return 0, 0


def _extract_tokens(resp):
    """Extract OpenAI-style token usage. Returns (in, out) as ints; bad/missing -> (0, 0)."""
    try:
        data = resp.json()
    except Exception:
        return 0, 0
    usage = data.get("usage") or {}
    raw_in = usage.get("prompt_tokens")
    raw_out = usage.get("completion_tokens")
    if raw_in is None or raw_out is None:
        return 0, 0
    try:
        return max(0, int(raw_in)), max(0, int(raw_out))
    except (TypeError, ValueError):
        return 0, 0
