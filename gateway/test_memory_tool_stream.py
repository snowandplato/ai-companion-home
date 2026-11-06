import asyncio
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


if "memory_recall" not in sys.modules:
    stub = types.ModuleType("memory_recall")
    stub.TOOL_NAME = "recall_memory"
    stub.recall_memory = AsyncMock(return_value='{"events":[]}')
    sys.modules["memory_recall"] = stub

from memory_tool_stream import stream_with_memory_tool  # noqa: E402


def event(payload):
    data = payload if payload == "[DONE]" else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


async def emit(*items):
    for item in items:
        yield event(item)


async def collect(initial, backend, body, followup):
    return b"".join([chunk async for chunk in stream_with_memory_tool(initial, backend, body, followup)]).decode()


class MemoryToolStreamTests(unittest.TestCase):
    def test_ordinary_stream_is_unchanged(self):
        called = []
        async def followup(body):
            called.append(body)
            yield event("[DONE]")
        raw = asyncio.run(collect(emit({"choices": [{"delta": {"content": "你好"}}]}, "[DONE]"), "zenmux", {"messages": []}, followup))
        self.assertIn("你好", raw)
        self.assertEqual(raw.count("data: [DONE]"), 1)
        self.assertEqual(called, [])

    def test_anthropic_tool_result_continues_same_turn(self):
        bodies = []
        async def followup(body):
            bodies.append(body)
            yield event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "想起来了"}})
            yield event({"type": "message_stop"})
        initial = emit(
            {"type": "message_start", "message": {"usage": {"input_tokens": 100}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "tool-1", "name": "recall_memory", "input": {}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"thread_key":"home"}'}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "usage": {"output_tokens": 20}},
            {"type": "message_stop"},
        )
        with patch("memory_tool_stream.recall_memory", new=AsyncMock(return_value='{"thread":"home"}')) as lookup:
            raw = asyncio.run(collect(initial, "anthropic", {"messages": [], "tools": [{"name": "recall_memory"}]}, followup))
        self.assertEqual(lookup.await_args.args[0], {"thread_key": "home"})
        self.assertEqual(raw.count("memory_recall_started"), 1)
        self.assertIn('"type": "memory_recall_finished", "found": true', raw)
        self.assertEqual(raw.count("memory_recall_next_request"), 1)
        self.assertEqual(raw.count("message_stop"), 1)
        self.assertEqual(bodies[0]["messages"][0]["content"][0]["input"], {"thread_key": "home"})
        self.assertEqual(bodies[0]["messages"][1]["content"][0]["tool_use_id"], "tool-1")

    def test_openai_function_output_is_returned(self):
        bodies = []
        async def followup(body):
            bodies.append(body)
            yield event({"type": "response.output_text.delta", "delta": "我记得"})
            yield event({"type": "response.completed", "response": {"usage": {"input_tokens": 80, "output_tokens": 10}}})
        call = {"type": "function_call", "call_id": "call-1", "name": "recall_memory", "arguments": '{"query":"搬家"}'}
        initial = emit(
            {"type": "response.output_item.added", "output_index": 0, "item": call},
            {"type": "response.output_item.done", "output_index": 0, "item": call},
            {"type": "response.completed", "response": {"output": [call], "usage": {"input_tokens": 100, "output_tokens": 20}}},
            "[DONE]",
        )
        with patch("memory_tool_stream.recall_memory", new=AsyncMock(return_value='{"events":[]}')):
            raw = asyncio.run(collect(initial, "openai", {"input": [], "tools": [{"type": "function", "name": "recall_memory"}]}, followup))
        self.assertEqual(raw.count("response.completed"), 1)
        self.assertIn("memory_recall_usage", raw)
        self.assertIn('"type": "memory_recall_finished", "found": false', raw)
        self.assertEqual(bodies[0]["input"][1]["type"], "function_call_output")
        self.assertEqual(bodies[0]["input"][1]["call_id"], "call-1")

    def test_zenmux_tool_call_continues_and_removes_tool(self):
        bodies = []
        async def followup(body):
            bodies.append(body)
            yield event({"choices": [{"delta": {"content": "想起来了"}}]})
            yield event("[DONE]")
        initial = emit(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-2", "function": {"name": "recall_memory", "arguments": '{"query":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"搬家"}'}}]}}]},
            "[DONE]",
        )
        with patch("memory_tool_stream.recall_memory", new=AsyncMock(return_value='{"events":[]}')) as lookup:
            raw = asyncio.run(collect(initial, "zenmux", {"messages": [], "tools": [{"function": {"name": "recall_memory"}}]}, followup))
        self.assertEqual(lookup.await_args.args[0], {"query": "搬家"})
        self.assertEqual(raw.count("data: [DONE]"), 1)
        self.assertEqual(bodies[0]["messages"][-1]["tool_call_id"], "call-2")
        self.assertNotIn("tools", bodies[0])

    def test_zenmux_tool_rejection_falls_back_to_plain_chat(self):
        bodies = []

        async def rejected():
            error = RuntimeError("tool schema unsupported")
            error.response = types.SimpleNamespace(status_code=400)
            raise error
            yield b""  # pragma: no cover

        async def followup(body):
            bodies.append(body)
            yield event({"choices": [{"delta": {"content": "普通回复"}}]})
            yield event("[DONE]")

        raw = asyncio.run(collect(
            rejected(), "zenmux",
            {"messages": [{"role": "user", "content": "你好"}], "tools": [{"function": {"name": "recall_memory"}}]},
            followup,
        ))
        self.assertIn("普通回复", raw)
        self.assertNotIn("memory_recall_started", raw)
        self.assertNotIn("tools", bodies[0])


if __name__ == "__main__":
    unittest.main()
