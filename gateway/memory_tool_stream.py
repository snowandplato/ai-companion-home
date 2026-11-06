"""Execute one local memory tool call within a streamed model turn."""

import codecs
import copy
import json
import logging
from typing import Any, AsyncGenerator, Callable, Dict, List

from memory_recall import TOOL_NAME, recall_memory


logger = logging.getLogger(__name__)


def _sse(payload: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _event_payload(event: str) -> Any:
    data = "\n".join(line[5:].strip() for line in event.splitlines() if line.startswith("data:"))
    if not data or data == "[DONE]":
        return data
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


class MemoryToolTurn:
    def __init__(self, backend: str):
        self.backend = backend
        self.calls: Dict[Any, Dict[str, Any]] = {}
        self.blocks: Dict[int, Dict[str, Any]] = {}
        self.output_items: List[Dict[str, Any]] = []
        self.assistant_text = ""
        self.first_usage: Dict[str, Any] = {}
        self.announced = False

    def observe(self, payload: Any) -> bool:
        """Return true once a real recall_memory call has been identified."""
        if not isinstance(payload, dict):
            return False
        event_type = payload.get("type")
        if self.backend == "anthropic":
            if event_type == "content_block_start":
                index = int(payload.get("index") or 0)
                block = copy.deepcopy(payload.get("content_block") or {})
                self.blocks[index] = block
                if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                    self.calls[index] = {"id": block.get("id"), "name": TOOL_NAME, "arguments": block.get("input") or {}}
            elif event_type == "content_block_delta":
                index = int(payload.get("index") or 0)
                block = self.blocks.get(index)
                delta = payload.get("delta") or {}
                if block is not None:
                    if delta.get("type") == "text_delta":
                        block["text"] = str(block.get("text") or "") + str(delta.get("text") or "")
                    elif delta.get("type") == "thinking_delta":
                        block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
                    elif delta.get("type") == "signature_delta":
                        block["signature"] = str(delta.get("signature") or "")
                    elif delta.get("type") == "input_json_delta":
                        block["_raw_input"] = str(block.get("_raw_input") or "") + str(delta.get("partial_json") or "")
                        if index in self.calls:
                            self.calls[index]["raw_arguments"] = block["_raw_input"]
            elif event_type == "content_block_stop":
                block = self.blocks.get(int(payload.get("index") or 0))
                raw_input = block.pop("_raw_input", None) if block else None
                if block and raw_input:
                    try:
                        block["input"] = json.loads(raw_input)
                    except json.JSONDecodeError:
                        block["input"] = {}
            if event_type == "message_start":
                self.first_usage = (payload.get("message") or {}).get("usage") or {}
            elif event_type == "message_delta":
                self.first_usage = {**self.first_usage, **(payload.get("usage") or {})}
        elif self.backend == "openai":
            item = payload.get("item") or {}
            if event_type in {"response.output_item.added", "response.output_item.done"} and item.get("type") == "function_call":
                key = payload.get("output_index", item.get("id", 0))
                call = self.calls.setdefault(key, {})
                call.update({"id": item.get("call_id"), "name": item.get("name"), "arguments": item.get("arguments", call.get("arguments", ""))})
                if event_type == "response.output_item.done":
                    self.output_items.append(item)
            elif event_type == "response.output_item.done" and isinstance(item, dict):
                self.output_items.append(item)
            elif event_type in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
                key = payload.get("output_index", payload.get("item_id", 0))
                call = self.calls.setdefault(key, {})
                if event_type.endswith(".done"):
                    call["arguments"] = payload.get("arguments", "")
                else:
                    call["arguments"] = str(call.get("arguments") or "") + str(payload.get("delta") or "")
            elif event_type == "response.completed":
                response = payload.get("response") or {}
                self.first_usage = response.get("usage") or {}
                if response.get("output"):
                    self.output_items = response["output"]
                    for index, output in enumerate(self.output_items):
                        if output.get("type") == "function_call":
                            self.calls[index] = {
                                "id": output.get("call_id"), "name": output.get("name"),
                                "arguments": output.get("arguments", ""),
                            }
        else:
            for choice in payload.get("choices", []) or []:
                delta = choice.get("delta") or {}
                self.assistant_text += str(delta.get("content") or "")
                for item in delta.get("tool_calls", []) or []:
                    key = int(item.get("index") or 0)
                    call = self.calls.setdefault(key, {"arguments": ""})
                    if item.get("id"):
                        call["id"] = item["id"]
                    function = item.get("function") or {}
                    if function.get("name"):
                        call["name"] = function["name"]
                    call["arguments"] += str(function.get("arguments") or "")
            if isinstance(payload.get("usage"), dict):
                self.first_usage = payload["usage"]
        return self.recall_call() is not None

    def recall_call(self) -> Dict[str, Any] | None:
        return next((call for call in self.calls.values() if call.get("name") == TOOL_NAME), None)

    def recall_calls(self) -> List[Dict[str, Any]]:
        return [call for call in self.calls.values() if call.get("name") == TOOL_NAME]

    def continuation_body(self, body: Dict[str, Any], result: str) -> Dict[str, Any]:
        calls = self.recall_calls()
        already_used = json.dumps({"error": "本轮已翻阅一次记忆"}, ensure_ascii=False)
        next_body = copy.deepcopy(body)
        tools = next_body.get("tools") or []
        if self.backend == "anthropic":
            next_body["tools"] = [tool for tool in tools if tool.get("name") != TOOL_NAME]
            if not next_body["tools"]:
                next_body.pop("tools", None)
            blocks = [self.blocks[index] for index in sorted(self.blocks)]
            next_body["messages"].append({"role": "assistant", "content": blocks})
            next_body["messages"].append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call.get("id"), "content": result if index == 0 else already_used}
                    for index, call in enumerate(calls)
                ],
            })
        elif self.backend == "openai":
            next_body["tools"] = [tool for tool in tools if tool.get("name") != TOOL_NAME]
            if not next_body["tools"]:
                next_body.pop("tools", None)
                next_body.pop("tool_choice", None)
            next_body["input"].extend(copy.deepcopy(self.output_items))
            next_body["input"].extend({
                "type": "function_call_output", "call_id": call.get("id"),
                "output": result if index == 0 else already_used,
            } for index, call in enumerate(calls))
        else:
            next_body["tools"] = [tool for tool in tools if (tool.get("function") or {}).get("name") != TOOL_NAME]
            if not next_body["tools"]:
                next_body.pop("tools", None)
                next_body.pop("tool_choice", None)
            next_body["messages"].append({
                "role": "assistant",
                "content": self.assistant_text or None,
                "tool_calls": [{
                    "id": call.get("id"), "type": "function",
                    "function": {"name": TOOL_NAME, "arguments": call.get("arguments") or "{}"},
                } for call in calls],
            })
            next_body["messages"].extend({
                "role": "tool", "tool_call_id": call.get("id"),
                "content": result if index == 0 else already_used,
            } for index, call in enumerate(calls))
        return next_body


async def stream_with_memory_tool(
    initial_stream: AsyncGenerator[bytes, None],
    backend: str,
    body: Dict[str, Any],
    send_followup: Callable[[Dict[str, Any]], AsyncGenerator[bytes, None]],
) -> AsyncGenerator[bytes, None]:
    turn = MemoryToolTurn(backend)
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""

    try:
        async for chunk in initial_stream:
            buffer += decoder.decode(chunk).replace("\r\n", "\n")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                payload = _event_payload(event)
                turn.observe(payload)
                has_recall = turn.recall_call() is not None
                if has_recall and not turn.announced:
                    turn.announced = True
                    yield _sse({"type": "memory_recall_started"})
                is_final = payload == "[DONE]" or (
                    isinstance(payload, dict) and payload.get("type") in {"message_stop", "response.completed"}
                )
                if has_recall and is_final:
                    continue
                yield (event + "\n\n").encode("utf-8")
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if backend != "zenmux" or status != 400 or turn.announced:
            raise
        # Some compatible models reject custom tools. Preserve ordinary chat for them.
        logger.warning("ZenMux model rejected memory tool; retrying this turn without it")
        fallback = copy.deepcopy(body)
        fallback.pop("tools", None)
        fallback.pop("tool_choice", None)
        async for chunk in send_followup(fallback):
            yield chunk
        return

    buffer += decoder.decode(b"", final=True).replace("\r\n", "\n")
    if buffer.strip() and not turn.recall_call():
        yield (buffer + "\n\n").encode("utf-8")
    call = turn.recall_call()
    if not call:
        return

    try:
        args = call.get("arguments") or call.get("raw_arguments") or {}
        if isinstance(args, str):
            args = json.loads(args)
        result = await recall_memory(args)
    except (ValueError, TypeError) as exc:
        result = json.dumps({"error": f"记忆查阅参数无法解析：{type(exc).__name__}"}, ensure_ascii=False)
    except Exception:
        logger.exception("Memory recall lookup failed")
        result = json.dumps({"error": "记忆查阅暂时不可用"}, ensure_ascii=False)

    try:
        recalled = json.loads(result)
        found_memory = bool(
            isinstance(recalled, dict) and
            (recalled.get("thread") or recalled.get("threads") or recalled.get("events"))
        )
    except (TypeError, ValueError):
        found_memory = False

    if backend == "anthropic":
        for block in turn.blocks.values():
            if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                block["input"] = args if isinstance(args, dict) else {}

    if backend == "openai" and turn.first_usage:
        yield _sse({"type": "memory_recall_usage", "usage": turn.first_usage})
    yield _sse({"type": "memory_recall_finished", "found": found_memory})
    yield _sse({"type": "memory_recall_next_request"})
    continuation = turn.continuation_body(body, result)
    try:
        async for chunk in send_followup(continuation):
            yield chunk
    except Exception:
        logger.exception("Memory recall continuation failed")
        message = "我刚才翻记忆时遇到了问题，暂时没能接着说完。我们可以再试一次。"
        if backend == "anthropic":
            yield _sse({"type": "content_block_start", "content_block": {"type": "text", "text": ""}})
            yield _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": message}})
            yield _sse({"type": "content_block_stop"})
            yield _sse({"type": "message_stop"})
        elif backend == "openai":
            yield _sse({"type": "response.output_text.delta", "delta": message})
            yield _sse({"type": "response.completed", "response": {}})
        else:
            yield _sse({"choices": [{"delta": {"content": message}}]})
            yield b"data: [DONE]\n\n"
