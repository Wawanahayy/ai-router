import unittest

from ai_router.services.models import normalize_models_payload


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


if __name__ == "__main__":
    unittest.main()
