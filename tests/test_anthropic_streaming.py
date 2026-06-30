import json
import unittest
from unittest.mock import AsyncMock, patch

from ai_router.services import streaming


def anthropic_event(payload):
    return f"data: {json.dumps(payload)}\n\n".encode()


class FakeStreamResponse:
    def __init__(self, chunks):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeAsyncClient:
    def __init__(self, *args, chunks=None, **kwargs):
        self._chunks = chunks or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, *args, **kwargs):
        return FakeStreamResponse(self._chunks)


def parse_openai_sse(raw):
    payloads = []
    for block in raw.decode().split("\n\n"):
        line = block.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        payloads.append(json.loads(data))
    return payloads


class AnthropicStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def collect_stream(self, chunks):
        response, status = await streaming.proxy_stream(
            "https://example.test/v1/messages",
            {},
            {"stream": True, "tools": [{"name": "process"}]},
            "provider-id",
            "key-id",
            "claude-test",
            0,
            provider_type="anthropic-compatible",
        )
        self.assertEqual(status, 200)
        output = []
        with patch.object(streaming.httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient(*a, chunks=chunks, **kw)):
            with patch.object(streaming.db, "mark_key_success", AsyncMock()), \
                 patch.object(streaming.db, "clear_key_model_lock", AsyncMock()), \
                 patch.object(streaming.db, "mark_key_used", AsyncMock()), \
                 patch.object(streaming.db, "add_log", AsyncMock()):
                async for item in response.body_iterator:
                    output.append(item)
        return parse_openai_sse(b"".join(output))

    async def test_anthropic_tool_arguments_do_not_get_prefixed_with_empty_object(self):
        payloads = await self.collect_stream([
            anthropic_event({"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 1}}}),
            anthropic_event({
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "process", "input": {}},
            }),
            anthropic_event({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"cmd\":\"ls\"}"}}),
            anthropic_event({"type": "content_block_stop", "index": 0}),
            anthropic_event({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}),
            anthropic_event({"type": "message_stop"}),
        ])

        tool_chunks = [
            payload["choices"][0]["delta"]["tool_calls"][0]
            for payload in payloads
            if payload["choices"][0]["delta"].get("tool_calls")
        ]
        self.assertEqual(tool_chunks[0]["function"]["arguments"], "{\"cmd\":\"ls\"}")
        finish_reasons = [payload["choices"][0]["finish_reason"] for payload in payloads if payload["choices"][0]["finish_reason"]]
        self.assertEqual(finish_reasons[-1], "tool_calls")

    async def test_truncated_anthropic_tool_arguments_finish_as_length_without_tool_call(self):
        payloads = await self.collect_stream([
            anthropic_event({"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 1}}}),
            anthropic_event({
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "process", "input": {}},
            }),
            anthropic_event({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"cmd\":"}}),
            anthropic_event({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 2}}),
            anthropic_event({"type": "message_stop"}),
        ])

        self.assertFalse(any(payload["choices"][0]["delta"].get("tool_calls") for payload in payloads))
        finish_reasons = [payload["choices"][0]["finish_reason"] for payload in payloads if payload["choices"][0]["finish_reason"]]
        self.assertEqual(finish_reasons[-1], "length")


if __name__ == "__main__":
    unittest.main()
