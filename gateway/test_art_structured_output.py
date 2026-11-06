import unittest

from formatter import RequestFormatter


ART_REVIEW_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"decision": {"type": "string"}},
            "required": ["decision"],
            "additionalProperties": False,
        },
    }
}


class ArtStructuredOutputTests(unittest.TestCase):
    def test_claude_formatter_forwards_output_config(self):
        parsed = {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "审稿"}],
            "system": "系统",
            "stream": False,
            "max_tokens": 1400,
            "output_config": ART_REVIEW_OUTPUT_CONFIG,
        }
        body = RequestFormatter.to_claude_format(parsed)
        self.assertEqual(body["output_config"], ART_REVIEW_OUTPUT_CONFIG)
        schema = body["output_config"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("decision", schema["required"])


if __name__ == "__main__":
    unittest.main()
