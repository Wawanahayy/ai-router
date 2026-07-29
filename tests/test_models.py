import unittest

from ai_router.services import models as models_service
from ai_router.services.models import normalize_models_payload


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("invalid json")
        return self._payload


class FakeAsyncClient:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        self._responses = list(self.responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append({"url": url, "headers": dict(headers or {})})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ModelListTests(unittest.TestCase):
    def test_normalizes_openai_data_shape(self):
        models = normalize_models_payload({
            "object": "list",
            "data": [
                {"id": "gpt-4.1", "created": 1},
                {"id": "gpt-4.1"},
                {"id": "gpt-4.1-mini"},
            ],
        })

        self.assertEqual(
            models,
            [
                {"id": "gpt-4.1", "created": 1},
                {"id": "gpt-4.1-mini", "created": 0},
            ],
        )

    def test_normalizes_alternate_model_shapes(self):
        models = normalize_models_payload({
            "models": [
                "claude-sonnet-4-6",
                {"name": "claude-haiku-4-5"},
                {"model_id": "custom-model"},
            ],
        })

        self.assertEqual(
            [model["id"] for model in models],
            ["claude-sonnet-4-6", "claude-haiku-4-5", "custom-model"],
        )


class UpstreamModelFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeAsyncClient.responses = []
        FakeAsyncClient.requests = []
        self.original_client = models_service.httpx.AsyncClient
        models_service.httpx.AsyncClient = FakeAsyncClient

    def tearDown(self):
        models_service.httpx.AsyncClient = self.original_client

    async def test_anthropic_retries_model_discovery_with_bearer_auth(self):
        FakeAsyncClient.responses = [
            FakeResponse(500, {"error": "native auth rejected"}, "native auth rejected"),
            FakeResponse(200, {"data": [{"id": "claude-sonnet-5"}]}),
        ]
        provider = {
            "id": "anthropic-test",
            "base_url": "https://example.test/v1",
            "type": "anthropic-compatible",
            "request_format": "anthropic-compatible",
            "auth_type": "x-api-key",
            "anthropic_version": "2023-06-01",
        }

        fetched = await models_service._fetch_models_from_upstream(provider, {"key_value": "secret"})

        self.assertEqual(fetched, [{"id": "claude-sonnet-5", "created": 0}])
        self.assertEqual(len(FakeAsyncClient.requests), 2)
        self.assertEqual(FakeAsyncClient.requests[0]["headers"]["x-api-key"], "secret")
        self.assertNotIn("Authorization", FakeAsyncClient.requests[0]["headers"])
        self.assertEqual(FakeAsyncClient.requests[1]["headers"]["Authorization"], "Bearer secret")
        self.assertNotIn("x-api-key", FakeAsyncClient.requests[1]["headers"])
        self.assertEqual(FakeAsyncClient.requests[1]["headers"]["anthropic-version"], "2023-06-01")

    async def test_anthropic_continues_to_bearer_after_native_timeout(self):
        FakeAsyncClient.responses = [
            models_service.httpx.ReadTimeout("native timeout"),
            FakeResponse(200, {"models": [{"name": "claude-opus-test"}]}),
        ]
        provider = {
            "id": "anthropic-timeout-test",
            "base_url": "https://example.test/v1",
            "type": "anthropic-compatible",
            "request_format": "anthropic-compatible",
            "auth_type": "x-api-key",
        }

        fetched = await models_service._fetch_models_from_upstream(provider, {"key_value": "secret"})

        self.assertEqual(fetched, [{"id": "claude-opus-test", "created": 0}])
        self.assertEqual(len(FakeAsyncClient.requests), 2)
        self.assertEqual(FakeAsyncClient.requests[1]["headers"]["Authorization"], "Bearer secret")

    async def test_openai_model_discovery_remains_single_bearer_request(self):
        FakeAsyncClient.responses = [
            FakeResponse(200, {"data": [{"id": "gpt-test"}]}),
        ]
        provider = {
            "id": "openai-test",
            "base_url": "https://example.test/v1",
            "type": "openai-compatible",
            "request_format": "openai-compatible",
            "auth_type": "bearer",
        }

        fetched = await models_service._fetch_models_from_upstream(provider, {"key_value": "secret"})

        self.assertEqual(fetched, [{"id": "gpt-test", "created": 0}])
        self.assertEqual(len(FakeAsyncClient.requests), 1)
        self.assertEqual(FakeAsyncClient.requests[0]["headers"]["Authorization"], "Bearer secret")
        self.assertNotIn("x-api-key", FakeAsyncClient.requests[0]["headers"])


if __name__ == "__main__":
    unittest.main()
