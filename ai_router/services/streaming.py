"""Streaming helpers for upstream chat completions."""
import asyncio
import json
import logging
import os
import time

import httpx
from starlette.responses import StreamingResponse

from .. import db

logger = logging.getLogger(__name__)

STREAM_CONNECT_TIMEOUT = float(os.getenv("AI_ROUTER_STREAM_CONNECT_TIMEOUT", "20"))
# Deprecated: first-byte timeouts cut off slow reasoning/thinking models after
# the upstream has accepted the request. Keep the env var ignored for backwards
# compatibility and rely on raw-byte stall detection instead.
STREAM_FIRST_BYTE_TIMEOUT = 0
STREAM_STALL_TIMEOUT = float(os.getenv("AI_ROUTER_STREAM_STALL_TIMEOUT", "300"))
STREAM_TRANSIENT_WAIT = float(os.getenv("AI_ROUTER_STREAM_TRANSIENT_WAIT", "5"))
STREAM_HEARTBEAT_INTERVAL = float(os.getenv("AI_ROUTER_STREAM_HEARTBEAT_INTERVAL", "15"))


def _timeout_value(seconds: float):
    return None if seconds <= 0 else seconds


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

    def track_delta_text(delta):
        if not isinstance(delta, dict):
            return
        for key in ("content", "reasoning_content"):
            value = delta.get(key)
            if isinstance(value, str):
                output_state["chars"] += len(value)
        for tool_call in delta.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                output_state["chars"] += len(arguments)

    def error_chunk(message: str):
        payload = {
            "id": "chatcmpl-ai-router-error",
            "object": "chat.completion.chunk",
            "model": stream_state["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": f"[ai-router] {message}"},
                    "finish_reason": "stop",
                }
            ],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

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
                    message = choice.get("message") or choice.get("delta") or {}
                    track_delta_text(message)
                    if message.get("tool_calls") and choice.get("finish_reason") not in (None, "tool_calls"):
                        choice["finish_reason"] = "tool_calls"
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
                    if message.get("content"):
                        chunks.append(openai_stream_chunk({"content": message["content"]}))
                    if message.get("reasoning_content"):
                        chunks.append(openai_stream_chunk({"reasoning_content": message["reasoning_content"]}))
                    if message.get("tool_calls"):
                        chunks.append(openai_stream_chunk({"tool_calls": message["tool_calls"]}))
                    finish_reason = "tool_calls" if message.get("tool_calls") else first.get("finish_reason") or "stop"
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
            for part in payload.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    output_state["chars"] += len(part["text"])
                    yield openai_stream_chunk({"content": part["text"]})
                elif part.get("type") == "tool_use":
                    output_state["chars"] += len(json.dumps(part.get("input") or {}, ensure_ascii=False))
                    yield openai_stream_chunk({
                        "tool_calls": [{
                            "index": 0,
                            "id": part.get("id") or "call_0",
                            "type": "function",
                            "function": {
                                "name": part.get("name") or "tool",
                                "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                            },
                        }]
                    })
            finish_reason = "tool_calls" if payload.get("stop_reason") == "tool_use" else payload.get("stop_reason") or "stop"
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
        if message.get("content"):
            output_state["chars"] += len(message["content"])
            yield openai_stream_chunk({"content": message["content"]})
        if message.get("reasoning_content"):
            output_state["chars"] += len(message["reasoning_content"])
            yield openai_stream_chunk({"reasoning_content": message["reasoning_content"]})
        if message.get("tool_calls"):
            output_state["chars"] += len(json.dumps(message["tool_calls"], ensure_ascii=False))
            yield openai_stream_chunk({"tool_calls": message["tool_calls"]})
        finish_reason = "tool_calls" if message.get("tool_calls") else first.get("finish_reason") or "stop"
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
            if block.get("type") == "tool_use":
                return openai_stream_chunk({
                    "tool_calls": [{
                        "index": index,
                        "id": block.get("id") or f"call_{index}",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "tool",
                            "arguments": "",
                        },
                    }]
                }), False
            if block.get("type") == "text" and block.get("text"):
                output_state["chars"] += len(block["text"])
                return openai_stream_chunk({"content": block["text"]}), False
            return None, False
        if event_type == "content_block_delta":
            index = payload.get("index", 0)
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                output_state["chars"] += len(delta["text"])
                return openai_stream_chunk({"content": delta["text"]}), False
            if delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                output_state["chars"] += len(delta["partial_json"])
                return openai_stream_chunk({
                    "tool_calls": [{
                        "index": index,
                        "function": {"arguments": delta["partial_json"]},
                    }]
                }), False
            if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                output_state["chars"] += len(delta["thinking"])
                return openai_stream_chunk({"reasoning_content": delta["thinking"]}), False
            return None, False
        if event_type == "message_delta":
            delta = payload.get("delta") or {}
            update_usage(payload.get("usage"))
            stop_reason = delta.get("stop_reason")
            if stop_reason:
                finish_reason = "tool_calls" if stop_reason == "tool_use" else stop_reason
                return openai_stream_chunk({}, finish_reason), False
            return None, False
        if event_type == "message_stop":
            return b"data: [DONE]\n\n", True
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

        async with httpx.AsyncClient(timeout=stream_timeout) as client:
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
                current_start = time.time()

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

                try:
                    async with client.stream("POST", attempt["url"], headers=attempt["headers"], json=attempt["body"]) as resp:
                        if resp.status_code >= 400:
                            error_text = await resp.aread()
                            error_msg = error_text.decode(errors="replace")[:500]
                            await db.mark_key_error(attempt["key_id"], resp.status_code, error_msg, attempt["model"])
                            latency = int((time.time() - current_start) * 1000)
                            await db.add_log(attempt["provider_id"], attempt["key_id"], attempt["model"], 0, 0, latency, resp.status_code, error_msg, attempt.get("local_key_id"))
                            last_error = f"Upstream returned {resp.status_code}: {error_msg}"
                            if index < len(attempts) - 1:
                                next_group = attempts[index + 1].get("group")
                                if next_group != attempt.get("group") and resp.status_code in (502, 503, 504) and STREAM_TRANSIENT_WAIT > 0:
                                    await asyncio.sleep(STREAM_TRANSIENT_WAIT)
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
                                if current_type == "anthropic-compatible":
                                    output, is_done = normalize_anthropic_sse_line(line)
                                else:
                                    output, is_done = normalize_sse_line(line)
                                if is_done:
                                    sent_done = True
                                    await finalize_stream_success()
                                if output:
                                    await record_stream_opened()
                                    emitted = True
                                    yield output
                                if sent_done:
                                    break
                            if sent_done:
                                return

                        if buffer.strip():
                            if current_type == "anthropic-compatible":
                                output, is_done = normalize_anthropic_sse_line(buffer)
                            else:
                                output, is_done = normalize_sse_line(buffer)
                            if is_done:
                                sent_done = True
                                await finalize_stream_success()
                            if output:
                                await record_stream_opened()
                                emitted = True
                                yield output

                        if not sent_done:
                            await finalize_stream_success()
                            yield b"data: [DONE]\n\n"
                        else:
                            await finalize_stream_success()
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
                    if not emitted and index < len(attempts) - 1:
                        next_group = attempts[index + 1].get("group")
                        if next_group != attempt.get("group") and STREAM_TRANSIENT_WAIT > 0:
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
                    error_msg = str(e)[:500]
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
