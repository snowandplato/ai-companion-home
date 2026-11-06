import asyncio
import json
import sys
import types
import unittest


if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

if "httpx" not in sys.modules:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.Response = type("Response", (), {})
    httpx_stub.AsyncClient = type("AsyncClient", (), {})
    httpx_stub.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    sys.modules["httpx"] = httpx_stub

from formatter import RequestFormatter
from router import Router


class OpenAIResponsesRequestTests(unittest.TestCase):
    def test_openai_backend_uses_native_responses_endpoint(self):
        url, body, headers = Router().prepare_request({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hello"}],
            "system": "system",
            "stream": True,
            "enable_search": True,
        }, "openai", "sk-test")
        self.assertTrue(url.endswith("/v1/responses"))
        self.assertIn("input", body)
        self.assertNotIn("messages", body)
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_search_is_absent_when_switch_is_off(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hello"}],
            "system": "system",
            "stream": True,
            "enable_search": False,
        })
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)

    def test_search_is_optional_when_switch_is_on(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "latest news"}],
            "system": "system",
            "stream": True,
            "enable_search": True,
        })
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["tool_choice"], "auto")

    def test_native_image_tool_has_three_call_ceiling(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "draw three versions"}],
            "stream": True,
            "enable_native_image_generation": True,
            "native_image_model": "gpt-image-2",
        })
        self.assertEqual(body["tools"], [{
            "type": "image_generation",
            "quality": "medium",
            "model": "gpt-image-2",
        }])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["max_tool_calls"], 3)

    def test_search_and_native_image_tools_can_coexist(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "search, then draw"}],
            "stream": True,
            "enable_search": True,
            "enable_native_image_generation": True,
        })
        self.assertEqual([tool["type"] for tool in body["tools"]], ["web_search", "image_generation"])
        self.assertEqual(body["max_tool_calls"], 3)

    def test_reasoning_summary_is_only_requested_when_switch_is_on(self):
        base = {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "think about this"}],
            "stream": True,
        }
        disabled = RequestFormatter.to_openai_responses_format({**base, "enable_thinking": False})
        enabled = RequestFormatter.to_openai_responses_format({**base, "enable_thinking": True})
        self.assertNotIn("reasoning", disabled)
        self.assertEqual(enabled["reasoning"], {"effort": "medium", "summary": "auto"})

    def test_structured_output_maps_to_responses_text_format(self):
        schema = {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        }
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "repair json"}],
            "stream": False,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        })
        self.assertEqual(body["text"]["format"], {
            "type": "json_schema",
            "name": "structured_output",
            "strict": True,
            "schema": schema,
        })

    def test_openai_cache_uses_three_explicit_prefix_breakpoints(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hello"}],
            "system": "all system text",
            "stream": True,
            "enable_prompt_cache": True,
            "prompt_cache_key": "mhome:conversation-1",
            "prompt_cache_system_sections": [
                {"text": "core", "cache_boundary": True},
                {"text": "notes", "cache_boundary": True},
                {"text": "snapshot", "cache_boundary": True},
                {"text": "dynamic", "cache_boundary": False},
            ],
        })
        self.assertNotIn("instructions", body)
        self.assertEqual(body["prompt_cache_options"], {"mode": "explicit", "ttl": "30m"})
        self.assertEqual(body["prompt_cache_key"], "mhome:conversation-1")
        blocks = body["input"][0]["content"]
        self.assertEqual(sum("prompt_cache_breakpoint" in block for block in blocks), 3)
        self.assertNotIn("prompt_cache_breakpoint", blocks[-1])

    def test_openai_cache_off_disables_implicit_breakpoint(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "hello"}],
            "system": "all system text",
            "stream": True,
            "enable_prompt_cache": False,
            "prompt_cache_system_sections": [
                {"text": "core", "cache_boundary": True},
                {"text": "dynamic", "cache_boundary": False},
            ],
        })
        self.assertEqual(body["prompt_cache_options"], {"mode": "explicit"})
        self.assertFalse(any(
            "prompt_cache_breakpoint" in block
            for block in body["input"][0]["content"]
        ))

    def test_anthropic_image_block_becomes_responses_input_image(self):
        body = RequestFormatter.to_openai_responses_format({
            "model": "gpt-5.6-sol",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                    {"type": "text", "text": "look"},
                ],
            }],
            "stream": True,
        })
        self.assertEqual(body["input"][0]["content"][0]["type"], "input_image")
        self.assertEqual(body["input"][0]["content"][0]["image_url"], "data:image/png;base64,abc")


class OpenAIResponsesStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_is_normalized_for_existing_frontend(self):
        async def source():
            events = [
                {"type": "response.reasoning_summary_text.delta", "delta": "thinking"},
                {"type": "response.web_search_call.in_progress"},
                {"type": "response.output_text.delta", "delta": "hello"},
                {"type": "response.completed", "response": {"usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                }}},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n\n".encode()

        output = b"".join([chunk async for chunk in Router.normalize_openai_responses_stream(source())]).decode()
        self.assertIn('"thinking": "thinking"', output)
        self.assertIn("response.web_search_call.in_progress", output)
        self.assertIn('"content": "hello"', output)
        self.assertIn('"input_tokens": 10', output)
        self.assertIn("data: [DONE]", output)

    async def test_visible_text_closes_reasoning_block_if_done_event_is_missing(self):
        async def source():
            for event in [
                {"type": "response.reasoning_summary_text.delta", "delta": "thinking"},
                {"type": "response.output_text.delta", "delta": "answer"},
                {"type": "response.completed", "response": {}},
            ]:
                yield f"data: {json.dumps(event)}\n\n".encode()

        output = b"".join([chunk async for chunk in Router.normalize_openai_responses_stream(source())]).decode()
        stop_at = output.index('"type":"content_block_stop"')
        answer_at = output.index('"content": "answer"')
        self.assertLess(stop_at, answer_at)

    async def test_refusal_delta_remains_visible(self):
        async def source():
            yield b'data: {"type":"response.refusal.delta","delta":"cannot help"}\n\n'
            yield b'data: {"type":"response.completed","response":{}}\n\n'

        output = b"".join([chunk async for chunk in Router.normalize_openai_responses_stream(source())]).decode()
        self.assertIn('"content": "cannot help"', output)

    async def test_native_image_result_is_forwarded_as_internal_event(self):
        async def source():
            events = [
                {
                    "type": "response.output_item.added",
                    "item": {"type": "image_generation_call", "id": "ig_1"},
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "result": "base64-image",
                        "revised_prompt": "a quiet room",
                    },
                },
                {"type": "response.completed", "response": {}},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n\n".encode()

        output = b"".join([chunk async for chunk in Router.normalize_openai_responses_stream(source())]).decode()
        self.assertIn('"type":"mhome_native_image_started"', output)
        self.assertIn('"type": "mhome_native_images"', output)
        self.assertIn('"b64_json": "base64-image"', output)
        self.assertEqual(output.count("base64-image"), 1)

    async def test_non_streaming_response_is_normalized(self):
        result = Router.normalize_openai_responses_response({
            "id": "resp_1",
            "model": "gpt-5.6-sol",
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "brief thought"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        })
        self.assertEqual(result["choices"][0]["message"]["content"], "hello")
        self.assertEqual(result["choices"][0]["message"]["reasoning"], "brief thought")


if __name__ == "__main__":
    unittest.main()
