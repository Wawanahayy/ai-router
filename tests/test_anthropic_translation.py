import unittest

from ai_router.services.translator import openai_to_anthropic


class AnthropicTranslationTests(unittest.TestCase):
    def test_parallel_tool_results_are_grouped_in_one_user_message(self):
        body = {
            "model": "claude-test",
            "messages": [
                {"role": "user", "content": "Run both tools."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "tool_a", "arguments": "{}"},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "tool_b", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
                {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
                {"role": "assistant", "content": "Done."},
            ],
        }

        converted = openai_to_anthropic(body)["messages"]

        self.assertEqual([message["role"] for message in converted], ["user", "assistant", "user", "assistant"])
        result_blocks = converted[2]["content"]
        self.assertEqual([block["type"] for block in result_blocks], ["tool_result", "tool_result"])
        self.assertEqual([block["tool_use_id"] for block in result_blocks], ["call_a", "call_b"])

    def test_sequential_tool_rounds_remain_separate(self):
        body = {
            "model": "claude-test",
            "messages": [
                {"role": "user", "content": "Start."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_a", "type": "function", "function": {"name": "tool_a", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_b", "type": "function", "function": {"name": "tool_b", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
            ],
        }

        converted = openai_to_anthropic(body)["messages"]

        self.assertEqual([message["role"] for message in converted], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(converted[2]["content"][0]["tool_use_id"], "call_a")
        self.assertEqual(converted[4]["content"][0]["tool_use_id"], "call_b")


if __name__ == "__main__":
    unittest.main()
