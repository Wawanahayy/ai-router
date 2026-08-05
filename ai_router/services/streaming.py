"""Streaming helpers for upstream chat completions."""
import asyncio
import json
import logging
import os
import time
import uuid

import httpx
from starlette.responses import StreamingResponse

from .. import db
from . import error_policy

logger = logging.getLogger(__name__)

STREAM_CONNECT_TIMEOUT = float(os.getenv("AI_ROUTER_STREAM_CONNECT_TIMEOUT", "20"))
# the upstream has accepted the request. Keep the env var ignored for backwards
# compatibility and rely on raw-byte stall detection instead.
STREAM_FIRST_BYTE_TIMEOUT = 0
STREAM_STALL_TIMEOUT = float(os.getenv("AI_ROUTER_STREAM_STALL_TIMEOUT", "300"))
STREAM_TRANSIENT_WAIT = float(os.getenv("AI_ROUTER_STREAM_TRANSIENT_WAIT", "5"))
STREAM_HEARTBEAT_INTERVAL = float(os.getenv("AI_ROUTER_STREAM_HEARTBEAT_INTERVAL", "15"))


def _timeout_value(seconds: float):
    return None if seconds <= 0 else seconds


def _rate_limit_wait_seconds(attempt_count: int):
    steps = (3, 5, 10)
    return steps[min(max(attempt_count - 1, 0), len(steps) - 1)]


def _trim_tool_context(body: dict | None, keep_last: int = 10) -> dict | None:
    """Trim old tool call/result message pairs, keeping the last N pairs.

    Returns a new body dict with trimmed messages, or None if no trimming
    was possible (too few tool messages or invalid body).

    Preserves: system prompt, first user message, last N tool-related
    message groups (assistant+tool_calls followed by tool results), and
    all non-tool messages in between.
    """
    if not isinstance(body, dict):
        return None
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) < 5:
        return None

    from copy import deepcopy

    # Identify tool-related message indices (assistant with tool_calls + tool results)
    tool_groups = []  # list of (start_idx, end_idx) for each tool call group
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            i += 1
            continue
        # Detect assistant message with tool_calls
        is_tool_assistant = (
            msg.get("role") == "assistant"
            and (msg.get("tool_calls") or msg.get("function_call"))
        )
        if is_tool_assistant:
            group_start = i
            group_end = i
            # Collect following tool result messages
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                if isinstance(next_msg, dict) and next_msg.get("role") == "tool":
                    group_end = j
                    j += 1
                else:
                    break
            tool_groups.append((group_start, group_end))
            i = group_end + 1
        else:
            i += 1

    # Only trim if there are more than keep_last tool groups
    if len(tool_groups) <= keep_last:
        return None

    # Groups to remove (all except last keep_last)
    groups_to_remove = tool_groups[:-keep_last]
    remove_indices = set()
    for start, end in groups_to_remove:
        for idx in range(start, end + 1):
            remove_indices.add(idx)

    # Build trimmed messages
    new_body = deepcopy(body)
    trimmed_messages = []
    removed_count = 0
    for idx, msg in enumerate(messages):
        if idx in remove_indices:
            removed_count += 1
        else:
            trimmed_messages.append(msg)

    # Insert a summary note after the system/first messages so model knows context was trimmed
    insert_pos = min(2, len(trimmed_messages))
    trimmed_messages.insert(insert_pos, {
        "role": "user",
        "content": (
            f"[CONTEXT TRIMMED: {removed_count} older tool call/result messages were removed "
            f"to fit context window. {keep_last} most recent tool interactions are preserved below. "
            f"Continue from where you left off.]"
        ),
    })

    new_body["messages"] = trimmed_messages
    return new_body


def _estimate_input_tokens(body: dict) -> int:
    try:
        return max(0, int((len(json.dumps(body or {}, ensure_ascii=False)) + 3) / 4))
    except Exception:
        return 0


def _estimate_output_tokens(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(1, int(char_count / 4))


async def proxy_stream(url, headers, body, provider_id, key_id, model, start_time, local_key_id=None, provider_type=None, fallback_attempts=None):
    """Stream proxy with OpenAI-compatible SSE normalization."""
    stream_state = {"model": model, "provider_type": provider_type}
    token_state = {"in": 0, "out": 0, "estimated": False}
    output_state = {"chars": 0}
    content_state = {"content": False, "tools": False, "text_buffer": ""}
    tool_state = {"calls": {}}
    openai_tool_state = {"calls": {}}
    openai_text_state = {"hold": False, "buffer": ""}

    _ANTHROPIC_EVENT_TYPES = {
        "message_start", "message_delta", "message_stop", "ping",
        "content_block_start", "content_block_delta", "content_block_stop",
    }

    def _looks_like_anthropic_event(line: str) -> bool:
        stripped = line.strip()
        if stripped.startswith("data:"):
            stripped = stripped[5:].strip()
        if not stripped.startswith("{"):
            return False
        try:
            obj = json.loads(stripped)
        except Exception:
            return False
        return isinstance(obj, dict) and obj.get("type") in _ANTHROPIC_EVENT_TYPES

    def update_usage(usage):
        if not isinstance(usage, dict):
            return
        tokens_in = usage.get("prompt_tokens")
        if tokens_in is None:
            tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("completion_tokens")
        if tokens_out is None:
            tokens_out = usage.get("output_tokens")
        if isinstance(tokens_in, int):
            token_state["in"] = max(token_state["in"], tokens_in)
        if isinstance(tokens_out, int):
            token_state["out"] = max(token_state["out"], tokens_out)
        if token_state["in"] or token_state["out"]:
            token_state["estimated"] = False

    def has_semantic_output() -> bool:
        """Return true only when the upstream produced usable assistant output."""
        if content_state.get("tools"):
            return True
        if content_state.get("text_buffer", "").strip():
            return True
        if content_state.get("reasoning_buffer", "").strip():
            return True
        return False

    def request_has_tool_results(request_body: dict | None) -> bool:
        """Return true when this request continues an earlier tool execution."""
        messages = (request_body or {}).get("messages")
        return isinstance(messages, list) and any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in messages
        )

    def request_has_tools(request_body: dict | None) -> bool:
        return isinstance(request_body, dict) and any(
            key in request_body
            for key in ("tools", "tool_choice", "functions", "function_call")
        )

    def track_delta_text(delta):
        if not isinstance(delta, dict):
            return
        for key in ("content",):
            value = delta.get(key)
            if isinstance(value, str):
                output_state["chars"] += len(value)
                content_state["text_buffer"] += value
        # Track reasoning_content separately
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            if "reasoning_buffer" not in content_state:
                content_state["reasoning_buffer"] = ""
            content_state["reasoning_buffer"] += reasoning
        for tool_call in delta.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                output_state["chars"] += len(arguments)

    def error_chunk(message: str):
        payload = {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": message or "Upstream request failed",
            },
        }
        return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    def accumulate_openai_tool_calls(tool_calls):
        if not isinstance(tool_calls, list):
            return False
        saw_tool = False
        for fallback_index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            index = tool_call.get("index")
            if not isinstance(index, int):
                index = fallback_index
            state = openai_tool_state["calls"].setdefault(index, {
                "index": index,
                "id": None,
                "type": "function",
                "name": "",
                "arguments": "",
            })
            if tool_call.get("id"):
                state["id"] = tool_call.get("id")
            if tool_call.get("type"):
                state["type"] = tool_call.get("type")
            function = tool_call.get("function") or {}
            if isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str) and name:
                    if not state["name"] or name.startswith(state["name"]):
                        state["name"] = name
                    elif not state["name"].endswith(name):
                        state["name"] += name
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments:
                    if not state["arguments"] or arguments.startswith(state["arguments"]):
                        state["arguments"] = arguments
                    else:
                        state["arguments"] += arguments
            saw_tool = True
        if saw_tool:
            content_state["tools"] = True
        return saw_tool

    def pop_openai_tool_calls():
        calls = []
        keys = sorted(openai_tool_state["calls"].keys(), key=lambda value: (not isinstance(value, int), str(value)))
        for index in keys:
            state = openai_tool_state["calls"][index]
            calls.append({
                "index": state.get("index", index),
                "id": state.get("id") or f"call_{index}",
                "type": state.get("type") or "function",
                "function": {
                    "name": state.get("name") or "tool",
                    "arguments": state.get("arguments") or "{}",
                },
            })
        openai_tool_state["calls"] = {}
        return calls

    def has_valuable_delta(payload: dict) -> bool:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return True
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return bool(choice.get("finish_reason"))
        return bool(
            delta.get("role")
            or delta.get("content")
            or delta.get("reasoning_content")
            or delta.get("tool_calls")
            or choice.get("finish_reason")
        )

    def normalize_sse_payload(payload: dict) -> dict | None:
        if not isinstance(payload, dict):
            return payload
        update_usage(payload.get("usage"))
        if "choices" in payload:
            payload.setdefault("id", f"chatcmpl-{int(time.time() * 1000)}")
            payload.setdefault("object", "chat.completion.chunk")
            payload.setdefault("created", int(time.time()))
            payload.setdefault("model", stream_state["model"])
            payload.pop("prompt_filter_results", None)
            for choice in payload.get("choices") or []:
                if isinstance(choice, dict):
                    choice.pop("content_filter_results", None)
                    message_key = "message" if isinstance(choice.get("message"), dict) else "delta"
                    message = choice.get(message_key)
                    if not isinstance(message, dict):
                        message = {}
                        choice[message_key] = message
                    track_delta_text(message)
                    if openai_text_state["hold"] and isinstance(message.get("content"), str):
                        openai_text_state["buffer"] += message.get("content") or ""
                        message.pop("content", None)
                    if message.get("tool_calls"):
                        accumulate_openai_tool_calls(message.get("tool_calls"))
                        message.pop("tool_calls", None)
                    _fr_map = {
                        "stop_sequence": "stop",
                        "tool_use": "tool_calls",
                        "max_tokens": "length",
                        "content_filter": "content_filter",
                        "refusal": "content_filter",
                        "end_turn": "stop",
                    }
                    if choice.get("finish_reason") in _fr_map:
                        choice["finish_reason"] = _fr_map[choice["finish_reason"]]
                    if choice.get("finish_reason") and openai_tool_state["calls"]:
                        full_tool_calls = pop_openai_tool_calls()
                        if full_tool_calls:
                            openai_text_state["buffer"] = ""
                            message["tool_calls"] = full_tool_calls
                            choice["finish_reason"] = "tool_calls"
                            for tc in full_tool_calls:
                                logger.info(
                                    "SSE tool_call id=%s name=%s",
                                    tc.get("id"),
                                    (tc.get("function") or {}).get("name"),
                                )
                    if message.get("tool_calls") and choice.get("finish_reason") not in (None, "tool_calls"):
                        choice["finish_reason"] = "tool_calls"
                    if choice.get("finish_reason"):
                        if choice.get("finish_reason") == "tool_calls" and message.get("tool_calls"):
                            message.pop("content", None)
                            openai_text_state["buffer"] = ""
                        elif openai_text_state["hold"] and openai_text_state["buffer"]:
                            message["content"] = openai_text_state["buffer"]
                            openai_text_state["buffer"] = ""
            if not has_valuable_delta(payload):
                return None
        return payload

    def normalize_sse_line(line: str):
        stripped = line.strip()
        if not stripped:
            return None, False
        if stripped in ("[DONE]", "data: [DONE]", "data:[DONE]"):
            return b"data: [DONE]\n\n", True
        if stripped.startswith(("event:", "id:", "retry:")):
            return None, False

        raw_json_line = stripped.startswith("{")
        if not stripped.startswith("data:"):
            if not raw_json_line:
                return None, False
            data = stripped
        else:
            data = stripped[5:].strip()

        if not data or data == "null":
            return None, False
        try:
            parsed = json.loads(data)
        except Exception:
            return None, False
        payload = normalize_sse_payload(parsed)
        if payload is None:
            return None, False

        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                message = first.get("message")
                if isinstance(message, dict):
                    chunks = []
                    has_tool_calls = bool(message.get("tool_calls"))
                    if message.get("content") and not has_tool_calls:
                        chunks.append(openai_stream_chunk({"content": message["content"]}))
                    if has_tool_calls:
                        chunks.append(openai_stream_chunk({"tool_calls": message["tool_calls"]}))
                    finish_reason = "tool_calls" if has_tool_calls else first.get("finish_reason") or "stop"
                    chunks.append(openai_stream_chunk({}, finish_reason))
                    return b"".join(chunks), False

        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode(), False

    def json_response_to_sse(raw: bytes):
        try:
            payload = json.loads(raw.decode(errors="replace"))
        except Exception:
            text = raw.decode(errors="replace").strip()
            if text:
                yield error_chunk(f"Upstream returned non-SSE response: {text[:500]}")
            else:
                yield error_chunk("Upstream returned an empty non-SSE response")
            return

        if stream_state["provider_type"] == "anthropic-compatible" and payload.get("type") == "message":
            update_usage(payload.get("usage"))
            text_chunks = []
            tool_chunks = []
            saw_tool = False
            for idx, part in enumerate(payload.get("content") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    output_state["chars"] += len(part["text"])
                    text_chunks.append(openai_stream_chunk({"content": part["text"]}))
                elif part.get("type") == "tool_use":
                    output_state["chars"] += len(json.dumps(part.get("input") or {}, ensure_ascii=False))
                    saw_tool = True
                    tool_chunks.append(openai_stream_chunk({
                        "tool_calls": [{
                            "index": idx,
                            "id": part.get("id") or f"call_{idx}_{abs(hash(json.dumps(part.get('input') or {}, sort_keys=True, default=str))) % 100000}",
                            "type": "function",
                            "function": {
                                "name": part.get("name") or "tool",
                                "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                            },
                        }]
                    }))
                elif part.get("type") in ("thinking", "redacted_thinking"):
                    continue
            if saw_tool:
                for chunk in tool_chunks:
                    yield chunk
            else:
                for chunk in text_chunks:
                    yield chunk
            finish_reason = {
                "stop_sequence": "stop",
                "tool_use": "tool_calls",
                "max_tokens": "length",
            }.get(payload.get("stop_reason"), payload.get("stop_reason") or "stop")
            yield openai_stream_chunk({}, finish_reason)
            yield b"data: [DONE]\n\n"
            return

        choices = payload.get("choices") if isinstance(payload, dict) else None
        update_usage(payload.get("usage") if isinstance(payload, dict) else None)
        if not isinstance(choices, list) or not choices:
            yield error_chunk(f"Upstream returned unsupported non-SSE response: {json.dumps(payload, ensure_ascii=False)[:500]}")
            return

        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") or first.get("delta") or {}
        has_tool_calls = bool(message.get("tool_calls"))
        if message.get("content") and not has_tool_calls:
            output_state["chars"] += len(message["content"])
            yield openai_stream_chunk({"content": message["content"]})
        if has_tool_calls:
            output_state["chars"] += len(json.dumps(message["tool_calls"], ensure_ascii=False))
            yield openai_stream_chunk({"tool_calls": message["tool_calls"]})
        finish_reason = "tool_calls" if has_tool_calls else first.get("finish_reason") or "stop"
        yield openai_stream_chunk({}, finish_reason)
        yield b"data: [DONE]\n\n"

    anthropic_state = {"id": None, "created": int(time.time())}

    def openai_stream_chunk(delta: dict, finish_reason=None):
        payload = {
            "id": anthropic_state.get("id") or f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": anthropic_state["created"],
            "model": stream_state["model"],
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    def track_reasoning_text(text: str):
        if text:
            output_state["chars"] += len(text)

    def buffer_or_emit_text(text: str):
        if not text:
            return None
        output_state["chars"] += len(text)
        if openai_text_state["hold"]:
            content_state["text_buffer"] += text
            return None
        content_state["content"] = True
        return openai_stream_chunk({"content": text})

    def flush_buffered_text_if_no_tools():
        if content_state["tools"] or not content_state["text_buffer"]:
            content_state["text_buffer"] = ""
            return None
        text = content_state["text_buffer"]
        content_state["text_buffer"] = ""
        content_state["content"] = True
        return openai_stream_chunk({"content": text})

    def pop_anthropic_tool_chunks(index=None):
        chunks = []
        if index is None:
            keys = sorted(tool_state["calls"].keys(), key=lambda value: (not isinstance(value, int), str(value)))
        else:
            keys = [index] if index in tool_state["calls"] else []
        for key in keys:
            state = tool_state["calls"].get(key) or {}
            if state.get("emitted"):
                continue
            if not state.get("completed"):
                continue
            state["emitted"] = True
            chunks.append(openai_stream_chunk({
                "tool_calls": [{
                    "index": key if isinstance(key, int) else state.get("index", 0),
                    "id": state.get("id") or f"call_{key}",
                    "type": "function",
                    "function": {
                        "name": state.get("name") or "tool",
                        "arguments": state.get("arguments") or "{}",
                    },
                }]
            }))
        return chunks

    def has_emitted_anthropic_tool_call():
        return any((state or {}).get("emitted") for state in tool_state["calls"].values())

    def has_incomplete_anthropic_tool_call():
        return any(
            not (state or {}).get("completed")
            for state in tool_state["calls"].values()
        )

    def normalize_anthropic_sse_line(line: str):
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return None, False
        data = stripped[5:].strip()
        if data == "[DONE]":
            return b"data: [DONE]\n\n", True
        try:
            payload = json.loads(data)
        except Exception:
            return None, False

        event_type = payload.get("type")
        if event_type == "message_start":
            message = payload.get("message") or {}
            update_usage(message.get("usage"))
            anthropic_state["id"] = message.get("id") or anthropic_state["id"]
            return openai_stream_chunk({"role": "assistant"}), False
        if event_type == "content_block_start":
            index = payload.get("index", 0)
            block = payload.get("content_block") or {}
            block_type = block.get("type")
            if block_type == "tool_use":
                initial_input = block.get("input")
                initial_arguments = ""
                if initial_input not in (None, {}):
                    initial_arguments = json.dumps(initial_input, ensure_ascii=False)
                content_state["tools"] = True
                content_state["text_buffer"] = ""
                tool_state["calls"][index] = {
                    "index": index,
                    "id": block.get("id") or f"call_{index}",
                    "name": block.get("name") or "tool",
                    "arguments": initial_arguments,
                    "completed": False,
                    "emitted": False,
                }
                output_state["chars"] += len(initial_arguments)
                return None, False
            if block_type == "text" and block.get("text"):
                return buffer_or_emit_text(block["text"]), False
            if block_type in ("thinking", "redacted_thinking"):
                thought = block.get("thinking") or block.get("text") or block.get("data") or ""
                if isinstance(thought, str):
                    track_reasoning_text(thought)
                return None, False
            return None, False
        if event_type == "content_block_delta":
            index = payload.get("index", 0)
            delta = payload.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta" and delta.get("text"):
                return buffer_or_emit_text(delta["text"]), False
            if delta_type == "input_json_delta" and delta.get("partial_json"):
                content_state["tools"] = True
                content_state["text_buffer"] = ""
                state = tool_state["calls"].setdefault(index, {
                    "index": index,
                    "id": f"call_{index}",
                    "name": "tool",
                    "arguments": "",
                    "completed": False,
                    "emitted": False,
                })
                state["arguments"] = (state.get("arguments") or "") + delta["partial_json"]
                state["completed"] = False
                output_state["chars"] += len(delta["partial_json"])
                return None, False
            if delta_type == "thinking_delta" and delta.get("thinking"):
                track_reasoning_text(delta["thinking"])
                return None, False
            return None, False
        if event_type == "content_block_stop":
            index = payload.get("index", 0)
            if index in tool_state["calls"]:
                tool_state["calls"][index]["completed"] = True
            chunks = pop_anthropic_tool_chunks(index)
            if chunks:
                return b"".join(chunks), False
            return None, False
        if event_type == "message_delta":
            delta = payload.get("delta") or {}
            update_usage(payload.get("usage"))
            stop_reason = delta.get("stop_reason")
            if stop_reason:
                finish_reason = {
                    "end_turn": "stop",
                    "stop_sequence": "stop",
                    "tool_use": "tool_calls",
                    "max_tokens": "length",
                }.get(stop_reason, stop_reason or "stop")
                chunks = []
                if content_state["tools"] or tool_state["calls"]:
                    content_state["text_buffer"] = ""
                    chunks.extend(pop_anthropic_tool_chunks())
                    if finish_reason != "length" and (chunks or has_emitted_anthropic_tool_call()):
                        finish_reason = "tool_calls"
                    elif has_incomplete_anthropic_tool_call():
                        logger.warning(
                            "Anthropic stream ended before tool arguments completed; finish_reason=%s",
                            finish_reason,
                        )
                        finish_reason = "length"
                else:
                    flushed = flush_buffered_text_if_no_tools()
                    if flushed:
                        chunks.append(flushed)
                chunks.append(openai_stream_chunk({}, finish_reason))
                return b"".join(chunks), False
            return None, False
        if event_type == "message_stop":
            chunks = []
            if not content_state["tools"]:
                flushed = flush_buffered_text_if_no_tools()
                if flushed:
                    chunks.append(flushed)
            chunks.append(b"data: [DONE]\n\n")
            return b"".join(chunks), True
        if event_type == "error":
            error = payload.get("error") or {}
            return error_chunk(error.get("message") or json.dumps(payload, ensure_ascii=False)), True
        return None, False
    async def byte_generator():
        stream_timeout = httpx.Timeout(
            connect=STREAM_CONNECT_TIMEOUT,
            read=_timeout_value(STREAM_STALL_TIMEOUT),
            write=STREAM_CONNECT_TIMEOUT,
            pool=STREAM_CONNECT_TIMEOUT,
        )
        attempts = fallback_attempts or [{
            "url": url,
            "headers": headers,
            "body": body,
            "provider_id": provider_id,
            "key_id": key_id,
            "model": model,
            "group": f"{provider_id}:{model}",
            "start_time": start_time,
            "local_key_id": local_key_id,
            "provider_type": provider_type,
        }]
        last_error = None

        async with httpx.AsyncClient(timeout=stream_timeout, proxy=await db.get_proxy_url()) as client:
            for index, attempt in enumerate(attempts):
                sent_done = False
                emitted = False
                received_raw = False
                opened_success = False
                finalized_success = False
                buffer = ""
                current_type = attempt.get("provider_type")
                stream_state["model"] = attempt["model"]
                stream_state["provider_type"] = current_type
                token_state["in"] = 0
                token_state["out"] = 0
                token_state["estimated"] = False
                output_state["chars"] = 0
                content_state["content"] = False
                content_state["tools"] = False
                content_state["text_buffer"] = ""
                content_state["reasoning_buffer"] = ""
                tool_state["calls"] = {}
                openai_tool_state["calls"] = {}
                openai_text_state["hold"] = request_has_tools(attempt.get("body"))
                openai_text_state["buffer"] = ""
                current_start = time.time()
                defer_output = request_has_tool_results(attempt.get("body"))
                deferred_outputs = []

                async def record_stream_opened():
                    nonlocal opened_success
                    if opened_success:
                        return
                    opened_success = True
                    await db.mark_key_success(attempt["key_id"])
                    await db.clear_key_model_lock(attempt["key_id"], attempt["model"])
                    await db.mark_key_used(attempt["key_id"])

                async def finalize_stream_success():
                    nonlocal finalized_success
                    if finalized_success:
                        return
                    finalized_success = True
                    await record_stream_opened()
                    
                    # Store reasoning_content for thinking models
                    from .. import reasoning_middleware
                    if reasoning_middleware.is_thinking_model(attempt.get("model", "")):
                        final_content = content_state.get("text_buffer", "")
                        final_reasoning = content_state.get("reasoning_buffer", "")
                        if final_content and final_reasoning:
                            reasoning_middleware.store_reasoning(final_content, final_reasoning)
                    
                    tokens_in = token_state["in"]
                    tokens_out = token_state["out"]
                    if not tokens_in and not tokens_out:
                        tokens_in = _estimate_input_tokens(attempt.get("body") or {})
                        tokens_out = _estimate_output_tokens(output_state["chars"])
                        token_state["estimated"] = bool(tokens_in or tokens_out)
                    if tokens_in or tokens_out:
                        await db.mark_key_success(attempt["key_id"], tokens_in, tokens_out)
                    latency = int((time.time() - current_start) * 1000)
                    await db.add_log(
                        attempt["provider_id"],
                        attempt["key_id"],
                        attempt["model"],
                        tokens_in,
                        tokens_out,
                        latency,
                        resp.status_code,
                        local_key_id=attempt.get("local_key_id"),
                    )
                    if attempt.get("local_key_id"):
                        await db.mark_local_key_used(attempt["local_key_id"], tokens_in + tokens_out)
                    if (attempt.get("body") or {}).get("tools") or content_state["tools"]:
                        tool_arg_chars = sum(len(state.get("arguments") or "") for state in tool_state["calls"].values())
                        logger.info(
                            "Tool stream provider=%s model=%s tool_calls=%s tool_arg_chars=%s content=%s",
                            attempt.get("provider_name") or attempt.get("provider_id"),
                            attempt["model"],
                            len(tool_state["calls"]),
                            tool_arg_chars,
                            content_state["content"],
                        )

                try:
                    async with client.stream("POST", attempt["url"], headers=attempt["headers"], json=attempt["body"]) as resp:
                        if resp.status_code >= 400:
                            error_text = await resp.aread()
                            error_msg = error_policy._sanitize_error(error_text.decode(errors="replace")[:2000], status_code=resp.status_code)
                            await db.mark_key_error(attempt["key_id"], resp.status_code, error_msg, attempt["model"])
                            latency = int((time.time() - current_start) * 1000)
                            await db.add_log(attempt["provider_id"], attempt["key_id"], attempt["model"], 0, 0, latency, resp.status_code, error_msg, attempt.get("local_key_id"))
                            last_error = error_msg[:200]
                            # Retry fallback if more attempts available. Retry ALL remaining attempts:
                            # - different keys in same provider (exhaust all keys first), OR
                            # - different provider/group (cross-provider fallback)
                            if index < len(attempts) - 1:
                                next_group = attempts[index + 1].get("group")
                                same_provider = attempts[index + 1].get("provider_id") == attempt.get("provider_id")
                                
                                # Special handling for rate limit (429) - always wait longer
                                if resp.status_code == 429:
                                    if same_provider or next_group != attempt.get("group"):
                                        await asyncio.sleep(_rate_limit_wait_seconds(index + 1))
                                        continue
                                # Transient errors (5xx) - retry with delay
                                elif resp.status_code in (502, 503, 504, 524):
                                    if same_provider or next_group != attempt.get("group"):
                                        if STREAM_TRANSIENT_WAIT > 0:
                                            await asyncio.sleep(STREAM_TRANSIENT_WAIT)
                                        continue
                                # Other errors - retry immediately if same provider or different group
                                elif same_provider or next_group != attempt.get("group"):
                                    continue
                            yield error_chunk(last_error)
                            return

                        content_type = resp.headers.get("content-type", "").lower()
                        if "text/event-stream" not in content_type and "application/x-ndjson" not in content_type:
                            raw = await resp.aread()
                            for output in json_response_to_sse(raw):
                                await record_stream_opened()
                                yield output
                            await finalize_stream_success()
                            return

                        byte_iter = resp.aiter_bytes().__aiter__()
                        pending_chunk = None
                        while True:
                            try:
                                if pending_chunk is None:
                                    pending_chunk = asyncio.create_task(byte_iter.__anext__())

                                timeout = STREAM_HEARTBEAT_INTERVAL if STREAM_HEARTBEAT_INTERVAL > 0 else None

                                if timeout is None:
                                    chunk = await pending_chunk
                                    pending_chunk = None
                                else:
                                    done, _ = await asyncio.wait({pending_chunk}, timeout=timeout)
                                    if not done:
                                        yield b": ai-router waiting for upstream\n\n"
                                        continue
                                    chunk = pending_chunk.result()
                                    pending_chunk = None
                            except StopAsyncIteration:
                                break
                            if not chunk:
                                continue
                            received_raw = True
                            buffer += chunk.decode(errors="replace")
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                if current_type != "anthropic-compatible" and _looks_like_anthropic_event(line):
                                    current_type = "anthropic-compatible"
                                    stream_state["provider_type"] = current_type
                                if current_type == "anthropic-compatible":
                                    output, is_done = normalize_anthropic_sse_line(line)
                                else:
                                    output, is_done = normalize_sse_line(line)
                                if is_done:
                                    sent_done = True
                                if output and not is_done:
                                    if defer_output:
                                        deferred_outputs.append(output)
                                    else:
                                        await record_stream_opened()
                                        emitted = True
                                        yield output
                                if sent_done:
                                    break
                            if sent_done:
                                break

                        if buffer.strip():
                            if current_type != "anthropic-compatible" and _looks_like_anthropic_event(buffer):
                                current_type = "anthropic-compatible"
                                stream_state["provider_type"] = current_type
                            if current_type == "anthropic-compatible":
                                output, is_done = normalize_anthropic_sse_line(buffer)
                            else:
                                output, is_done = normalize_sse_line(buffer)
                            if is_done:
                                sent_done = True
                            if output and not is_done:
                                if defer_output:
                                    deferred_outputs.append(output)
                                else:
                                    await record_stream_opened()
                                    emitted = True
                                    yield output

                        # Do not classify an HTTP 200 stream with no semantic output
                        # as success. Keep tool-result continuations buffered so a
                        # retry can happen before any bytes reach Hermes.
                        if defer_output and not has_semantic_output():
                            last_error = "upstream returned an empty response after tool results"
                            await db.mark_key_error(attempt["key_id"], 502, last_error, attempt["model"])
                            latency = int((time.time() - current_start) * 1000)
                            await db.add_log(
                                attempt["provider_id"], attempt["key_id"], attempt["model"],
                                0, 0, latency, 502, last_error, attempt.get("local_key_id"),
                            )
                            logger.warning(
                                "Empty tool continuation provider=%s model=%s received_raw=%s outputs=%s",
                                attempt.get("provider_name") or attempt.get("provider_id"),
                                attempt["model"], received_raw, len(deferred_outputs),
                            )
                            if index < len(attempts) - 1:
                                if STREAM_TRANSIENT_WAIT > 0:
                                    await asyncio.sleep(STREAM_TRANSIENT_WAIT)
                                continue

                            # ── Context trim retry: keep last 10 tool pairs ──
                            # If no more attempts AND context has many tool calls,
                            # trim old tool call/result pairs (keep last 10) and
                            # retry once with the same key. This handles the case
                            # where upstream silently returns empty due to context
                            # overflow without an explicit error code.
                            if not attempt.get("_context_trimmed"):
                                trimmed_body = _trim_tool_context(attempt.get("body"), keep_last=10)
                                if trimmed_body is not None:
                                    attempt["body"] = trimmed_body
                                    attempt["_context_trimmed"] = True
                                    logger.info(
                                        "Context trimmed (keep last 10 tool pairs) — retrying provider=%s model=%s",
                                        attempt.get("provider_name") or attempt.get("provider_id"),
                                        attempt["model"],
                                    )
                                    # Reset state for retry
                                    sent_done = False
                                    emitted = False
                                    received_raw = False
                                    opened_success = False
                                    finalized_success = False
                                    buffer = ""
                                    content_state["content"] = False
                                    content_state["tools"] = False
                                    content_state["text_buffer"] = ""
                                    content_state["reasoning_buffer"] = ""
                                    tool_state["calls"] = {}
                                    openai_tool_state["calls"] = {}
                                    openai_text_state["buffer"] = ""
                                    current_start = time.time()
                                    defer_output = request_has_tool_results(attempt.get("body"))
                                    deferred_outputs = []
                                    if STREAM_TRANSIENT_WAIT > 0:
                                        await asyncio.sleep(STREAM_TRANSIENT_WAIT)
                                    # Re-run this attempt by manipulating index
                                    # We can't easily re-enter the for loop, so we
                                    # break and let the outer code yield error.
                                    # Instead, use a goto-like pattern: append trimmed
                                    # attempt to attempts list and continue.
                                    attempts.append(attempt)
                                    continue

                            yield error_chunk("upstream failed to produce an executable response after retries")
                            return

                        await finalize_stream_success()
                        for deferred_output in deferred_outputs:
                            emitted = True
                            yield deferred_output
                        if not sent_done:
                            yield b"data: [DONE]\n\n"
                        return
                except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                    latency = int((time.time() - current_start) * 1000)
                    timeout_msg = "Stream stalled waiting for upstream bytes"
                    await db.mark_key_error(attempt["key_id"], 504, timeout_msg, attempt["model"])
                    await db.add_log(attempt["provider_id"], attempt["key_id"], attempt["model"], 0, 0, latency, 504, timeout_msg, attempt.get("local_key_id"))
                    logger.warning(
                        "Stream timeout provider=%s key=%s model=%s emitted=%s received_raw=%s latency_ms=%s error=%s",
                        attempt["provider_id"],
                        attempt["key_id"],
                        attempt["model"],
                        emitted,
                        received_raw,
                        latency,
                        timeout_msg,
                    )
                    last_error = f"Upstream {timeout_msg.lower()}"
                    # Retry fallback if we haven't committed bytes to the client yet
                    # (avoids corrupting the SSE response). Retry ALL remaining attempts:
                    # - different keys in same provider (exhaust all keys first), OR
                    # - different provider/group (cross-provider fallback)
                    if not emitted and index < len(attempts) - 1:
                        next_group = attempts[index + 1].get("group")
                        same_provider = attempts[index + 1].get("provider_id") == attempt.get("provider_id")
                        
                        # Retry if next attempt is same provider (different key) OR different group
                        if same_provider or next_group != attempt.get("group"):
                            if STREAM_TRANSIENT_WAIT > 0:
                                await asyncio.sleep(STREAM_TRANSIENT_WAIT)
                            continue
                    yield error_chunk(last_error)
                    return
                except asyncio.CancelledError:
                    if opened_success or emitted or received_raw:
                        try:
                            await asyncio.shield(finalize_stream_success())
                        except Exception:
                            logger.exception(
                                "Failed to finalize cancelled stream provider=%s key=%s model=%s",
                                attempt["provider_id"],
                                attempt["key_id"],
                                attempt["model"],
                            )
                    raise
                except Exception as e:
                    latency = int((time.time() - current_start) * 1000)
                    error_msg = error_policy._sanitize_error(str(e)[:2000], status_code=500)
                    await db.add_log(attempt["provider_id"], attempt["key_id"], attempt["model"], 0, 0, latency, 500, error_msg, attempt.get("local_key_id"))
                    last_error = error_msg
                    if not emitted and index < len(attempts) - 1:
                        continue
                    yield error_chunk(error_msg)
                    return

        yield error_chunk(last_error or "All upstream stream attempts failed")

    return StreamingResponse(
        byte_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    ), 200




