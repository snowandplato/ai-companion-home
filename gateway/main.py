"""
AI Gateway Service - 主程序
统一网关服务，支持多格式请求转发和记忆注入
"""
import asyncio
import logging
import json
import codecs
import re
import secrets
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
import httpx

from config import config
from formatter import RequestFormatter
from injector import injector_service
from summarizer import summarizer
from router import router_service
from db import db
from app_identity import get_identity
from embedding_service import embedding_service
from reading import router as reading_router
from notes import router as notes_router, interrupt_stale_comment_jobs
from chat_images import (
    router as chat_images_router,
    attach_images_to_message_result,
    collect_message_image_paths,
    enrich_messages_with_image_context,
    prepare_message_images,
    remove_image_files,
    serialize_message_images,
)
from portrait import (
    InternalBlockFilter,
    PORTRAIT_BY_KEY,
    parse_portrait_update,
    parse_portrait_update_with_repair,
)
from internal_capabilities import (
    MAV_ART_INSTRUCTION,
    OPENAI_NATIVE_ART_INSTRUCTION,
    build_internal_instructions,
)
from art_generation import (
    router as art_generation_router,
    attach_art_jobs_to_message_result,
    collect_art_image_paths,
    create_art_job,
    create_native_art_job,
    create_native_art_placeholder,
    complete_native_art_job,
    fail_native_art_job,
    create_art_recovery_job,
    remove_art_image_files,
    resume_art_jobs,
)

# 配置日志
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _filter_sse_event(event: str, note_filter: InternalBlockFilter) -> Optional[bytes]:
    """过滤单个SSE事件里的可见文本，隐藏内部指令块。"""
    data_lines = []
    for line in event.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if not data_lines:
        return (event + "\n\n").encode("utf-8") if event.strip() else None

    data_str = "\n".join(data_lines)
    if data_str == "[DONE]":
        flushed = note_filter.flush_visible()
        prefix = f"data: {json.dumps({'choices': [{'delta': {'content': flushed}}]}, ensure_ascii=False)}\n\n" if flushed else ""
        return (prefix + "data: [DONE]\n\n").encode("utf-8")

    try:
        payload = json.loads(data_str)
    except json.JSONDecodeError:
        return (event + "\n\n").encode("utf-8")

    had_text = False
    visible_text = False

    # OpenAI/zenmux格式
    for choice in payload.get("choices", []) or []:
        delta = choice.get("delta", {})
        if isinstance(delta.get("content"), str):
            had_text = True
            filtered = note_filter.feed(delta["content"])
            delta["content"] = filtered
            if filtered:
                visible_text = True

    # Claude格式
    if payload.get("type") == "content_block_delta":
        delta = payload.get("delta", {})
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            had_text = True
            filtered = note_filter.feed(delta["text"])
            delta["text"] = filtered
            if filtered:
                visible_text = True

    if had_text and not visible_text:
        return None

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _summarize_sse_event(event: str) -> str:
    data_lines = [line[5:].strip() for line in event.splitlines() if line.startswith("data:")]
    if not data_lines:
        return "no-data"
    data_str = "\n".join(data_lines)
    if not data_str or data_str == "[DONE]":
        return data_str or "empty"
    try:
        payload = json.loads(data_str)
    except json.JSONDecodeError:
        return "non-json"

    content_block = payload.get("content_block") or {}
    delta = payload.get("delta") or {}
    return (
        f"type={payload.get('type')} "
        f"block_type={content_block.get('type')} "
        f"block_name={content_block.get('name')} "
        f"delta_type={delta.get('type')}"
    )


def _is_search_tool_event(event: str) -> bool:
    """????????????????"""
    data_lines = []
    for line in event.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return False

    data_str = "\n".join(data_lines)
    if not data_str or data_str == "[DONE]":
        return False
    try:
        payload = json.loads(data_str)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False

    event_type = payload.get("type")
    if event_type in (
        "response.web_search_call.in_progress",
        "response.web_search_call.searching",
        "response.web_search_call.completed",
    ):
        return True

    content_block = payload.get("content_block") or payload
    block_type = content_block.get("type")
    if block_type == "web_search_tool_result":
        return True
    if block_type == "server_tool_use":
        return content_block.get("name") == "web_search"

    for choice in payload.get("choices", []) or []:
        delta = choice.get("delta") or {}
        for tool_call in delta.get("tool_calls", []) or []:
            function_name = (tool_call.get("function") or {}).get("name")
            if function_name == "web_search":
                return True

    return False


class TokenUsageTracker:
    """浠庝笉鍚屾ā鍨嬬殑SSE浜嬩欢閲屾彁鍙杢oken usage銆?"""

    def __init__(self):
        self.completed_usage = {name: 0 for name in (
            "input_tokens", "output_tokens", "total_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens",
        )}
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_5m_input_tokens = 0
        self.cache_creation_1h_input_tokens = 0

    def feed_event(self, event: str):
        data_lines = []
        for line in event.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return

        data_str = "\n".join(data_lines)
        if not data_str or data_str == "[DONE]":
            return

        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return

        if payload.get("type") == "memory_recall_next_request":
            for name in self.completed_usage:
                value = getattr(self, name)
                if name == "total_tokens" and not value:
                    value = self.input_tokens + self.output_tokens
                self.completed_usage[name] += value
                setattr(self, name, 0)
            return

        usage = payload.get("usage")
        if isinstance(usage, dict):
            self._merge_usage(usage)
        message = payload.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            self._merge_usage(message["usage"])

    def _merge_usage(self, usage: Dict[str, Any]):
        input_tokens = self._to_int(usage.get("prompt_tokens"))
        if input_tokens is None:
            input_tokens = self._to_int(usage.get("input_tokens"))

        cache_creation = self._to_int(usage.get("cache_creation_input_tokens")) or 0
        cache_read = self._to_int(usage.get("cache_read_input_tokens")) or 0
        input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        openai_cached_tokens = False
        if isinstance(input_details, dict):
            cached_tokens = self._to_int(input_details.get("cached_tokens")) or 0
            cache_write_tokens = self._to_int(input_details.get("cache_write_tokens")) or 0
            if cached_tokens or cache_write_tokens:
                cache_read = max(cache_read, cached_tokens)
                cache_creation = max(cache_creation, cache_write_tokens)
                openai_cached_tokens = True
        cache_creation_detail = usage.get("cache_creation") or {}
        if isinstance(cache_creation_detail, dict):
            cache_creation_5m = self._to_int(
                cache_creation_detail.get("ephemeral_5m_input_tokens")
            ) or 0
            cache_creation_1h = self._to_int(
                cache_creation_detail.get("ephemeral_1h_input_tokens")
            ) or 0
            self.cache_creation_5m_input_tokens = max(
                self.cache_creation_5m_input_tokens,
                cache_creation_5m
            )
            self.cache_creation_1h_input_tokens = max(
                self.cache_creation_1h_input_tokens,
                cache_creation_1h
            )
        self.cache_creation_input_tokens = max(
            self.cache_creation_input_tokens,
            cache_creation
        )
        self.cache_read_input_tokens = max(
            self.cache_read_input_tokens,
            cache_read
        )
        if input_tokens is not None:
            # Anthropic reports normal/cache-write/cache-read input separately;
            # OpenAI's input_tokens already includes input_tokens_details.cached_tokens.
            effective_input = input_tokens if openai_cached_tokens else input_tokens + cache_creation + cache_read
            self.input_tokens = max(self.input_tokens, effective_input)

        output_tokens = self._to_int(usage.get("completion_tokens"))
        if output_tokens is None:
            output_tokens = self._to_int(usage.get("output_tokens"))
        if output_tokens is not None:
            self.output_tokens = max(self.output_tokens, output_tokens)

        total_tokens = self._to_int(usage.get("total_tokens"))
        if total_tokens is not None:
            self.total_tokens = max(self.total_tokens, total_tokens)

    def as_dict(self) -> Optional[Dict[str, int]]:
        input_tokens = self.completed_usage["input_tokens"] + self.input_tokens
        output_tokens = self.completed_usage["output_tokens"] + self.output_tokens
        total = self.completed_usage["total_tokens"] + (self.total_tokens or self.input_tokens + self.output_tokens)
        if not total:
            return None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
            "cache_creation_input_tokens": self.completed_usage["cache_creation_input_tokens"] + self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.completed_usage["cache_read_input_tokens"] + self.cache_read_input_tokens,
            "cache_creation_5m_input_tokens": self.completed_usage["cache_creation_5m_input_tokens"] + self.cache_creation_5m_input_tokens,
            "cache_creation_1h_input_tokens": self.completed_usage["cache_creation_1h_input_tokens"] + self.cache_creation_1h_input_tokens
        }

    @staticmethod
    def _to_int(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


async def filter_internal_blocks_stream(
    stream_gen,
    note_filter: InternalBlockFilter,
    usage_tracker: TokenUsageTracker = None,
    art_context: Dict[str, Any] = None,
    portrait_context: Dict[str, Any] = None,
):
    """过滤SSE流，隐藏内部块，并在流结束前发送写入成功事件。"""
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    notes_saved = False
    usage_sent = False
    search_notice_sent = False
    portrait_saved = False
    art_job_created = False
    native_images = []
    native_job_id = None
    art_finish_reason = ""

    def build_native_art_started_event() -> Optional[bytes]:
        nonlocal native_job_id
        if native_job_id or not art_context or art_context.get("mode") != "native":
            return None
        native_job_id, error = create_native_art_placeholder(art_context)
        if not native_job_id:
            payload = {"type": "mav_art_job_failed", "error": error or "native_art_start_failed"}
        else:
            payload = {
                "type": "mav_art_job_started",
                "job_id": native_job_id,
                "status": "generating",
                "mode": "native",
            }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    def build_note_saved_event() -> Optional[bytes]:
        nonlocal notes_saved
        if notes_saved:
            return None
        notes_saved = True
        saved_count = save_mav_notes(note_filter.notes)
        if not saved_count:
            return None
        return f"data: {json.dumps({'type': 'mav_note_saved', 'count': saved_count}, ensure_ascii=False)}\n\n".encode("utf-8")

    def build_token_usage_event() -> Optional[bytes]:
        nonlocal usage_sent
        if usage_sent:
            return None
        if not usage_tracker:
            return None
        usage = usage_tracker.as_dict()
        if not usage:
            return None
        usage_sent = True
        return f"data: {json.dumps({'type': 'token_usage', 'usage': usage}, ensure_ascii=False)}\n\n".encode("utf-8")

    async def build_portrait_updated_event() -> Optional[bytes]:
        nonlocal portrait_saved
        if portrait_saved:
            return None
        portrait_saved = True
        note_filter.recover_incomplete_portrait()
        outcome = await save_mav_portrait_updates_with_recovery(
            note_filter.portraits,
            portrait_context,
        )
        if outcome.get("status") == "none":
            return None
        if outcome.get("status") == "failed":
            payload = {"type": "mav_portrait_update_failed"}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        update = outcome.get("result") or {}
        payload = {
            "type": "mav_portrait_updated",
            "section_name": update.get("section_name", ""),
            "action": update.get("action", "")
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    def build_search_tool_event() -> Optional[bytes]:
        nonlocal search_notice_sent
        if search_notice_sent:
            return None
        search_notice_sent = True
        return f"data: {json.dumps({'type': 'search_tool_used'}, ensure_ascii=False)}\n\n".encode("utf-8")

    def build_art_job_event() -> Optional[bytes]:
        nonlocal art_job_created
        if art_job_created:
            return None
        art_job_created = True
        if not art_context or not art_context.get("enabled"):
            return None

        if art_context.get("mode") == "native":
            if not native_images:
                return None
            if native_job_id:
                job_id, error, status = complete_native_art_job(
                    native_job_id, native_images[:3], art_context
                )
            else:
                job_id, error, status = create_native_art_job(native_images[:3], art_context)
            if not job_id:
                payload = {"type": "mav_art_job_failed", "error": error or "native_image_save_failed"}
            else:
                payload = {
                    "type": "mav_art_job_started",
                    "job_id": job_id,
                    "status": status or "generating",
                    "mode": "native",
                }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

        # 模型若只漏了结束标签，先把隐藏缓冲区里的完整JSON交给解析器抢救。
        unclosed_block = note_filter.active_tag or "none"
        recovered_unclosed_art = note_filter.recover_incomplete_art()
        logger.info(
            "art response boundary finish_reason=%s unclosed_block=%s art_blocks=%s art_start_visible_chars=%s visible_chars=%s recovered_tail_chars=%s",
            art_finish_reason or "unknown",
            unclosed_block,
            len(note_filter.arts),
            note_filter.art_start_visible_chars,
            len(note_filter.visible_text),
            len(note_filter.buffer) if recovered_unclosed_art else 0,
        )
        job_id = None
        error = None
        status = None
        if note_filter.arts:
            job_id, error, status = create_art_job(note_filter.arts[0], art_context)

        # 没有块或块无法解析时，不把“忘记写指令”误判成Mav主动不画；
        # 创建一个很小的后台补写任务，让他明确选择create或decline。
        if not job_id:
            recovery_context = {
                **art_context,
                "assistant_response": note_filter.visible_text,
            }
            job_id, recovery_error = create_art_recovery_job(recovery_context)
            error = recovery_error or error
            status = "drafting" if job_id else None
        if not job_id:
            payload = {"type": "mav_art_job_failed", "error": error or "invalid_art_request"}
        else:
            payload = {"type": "mav_art_job_started", "job_id": job_id, "status": status or "queued"}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    async for chunk in stream_gen:
        buffer += decoder.decode(chunk).replace("\r\n", "\n")
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            if usage_tracker:
                usage_tracker.feed_event(event)
            try:
                event_data = "\n".join(
                    line[5:].strip() for line in event.splitlines() if line.startswith("data:")
                )
                event_payload = json.loads(event_data) if event_data and event_data != "[DONE]" else {}
            except json.JSONDecodeError:
                event_payload = {}
            if art_context and art_context.get("mode") == "studio":
                if event_payload.get("type") == "message_delta":
                    art_finish_reason = str((event_payload.get("delta") or {}).get("stop_reason") or art_finish_reason)
                for choice in event_payload.get("choices", []) or []:
                    art_finish_reason = str(choice.get("finish_reason") or art_finish_reason)
            if event_payload.get("type") == "mhome_native_images":
                native_images.extend(
                    item for item in (event_payload.get("images") or [])
                    if isinstance(item, dict) and item.get("b64_json")
                )
                continue
            if event_payload.get("type") == "mhome_native_image_started":
                started_event = build_native_art_started_event()
                if started_event:
                    yield started_event
                continue
            if _is_search_tool_event(event):
                logger.info(f"search_notice: detected tool event {_summarize_sse_event(event)}")
                search_event = build_search_tool_event()
                if search_event:
                    yield search_event
            if event.strip() == "data: [DONE]":
                saved_event = build_note_saved_event()
                if saved_event:
                    yield saved_event
                portrait_event = await build_portrait_updated_event()
                if portrait_event:
                    yield portrait_event
                art_event = build_art_job_event()
                if art_event:
                    yield art_event
                usage_event = build_token_usage_event()
                if usage_event:
                    yield usage_event
            filtered = _filter_sse_event(event, note_filter)
            if filtered:
                yield filtered

    buffer += decoder.decode(b"", final=True).replace("\r\n", "\n")
    if buffer.strip():
        if usage_tracker:
            usage_tracker.feed_event(buffer)
        try:
            buffered_data = "\n".join(
                line[5:].strip() for line in buffer.splitlines() if line.startswith("data:")
            )
            buffered_payload = json.loads(buffered_data) if buffered_data and buffered_data != "[DONE]" else {}
        except json.JSONDecodeError:
            buffered_payload = {}
        if buffered_payload.get("type") == "mhome_native_images":
            native_images.extend(
                item for item in (buffered_payload.get("images") or [])
                if isinstance(item, dict) and item.get("b64_json")
            )
            buffer = ""
        if _is_search_tool_event(buffer):
            logger.info(f"search_notice: detected buffered tool event {_summarize_sse_event(buffer)}")
            search_event = build_search_tool_event()
            if search_event:
                yield search_event
        if buffer.strip() == "data: [DONE]":
            saved_event = build_note_saved_event()
            if saved_event:
                yield saved_event
            portrait_event = await build_portrait_updated_event()
            if portrait_event:
                yield portrait_event
            art_event = build_art_job_event()
            if art_event:
                yield art_event
            usage_event = build_token_usage_event()
            if usage_event:
                yield usage_event
        filtered = _filter_sse_event(buffer, note_filter)
        if filtered:
            yield filtered

    saved_event = build_note_saved_event()
    if saved_event:
        yield saved_event
    portrait_event = await build_portrait_updated_event()
    if portrait_event:
        yield portrait_event
    art_event = build_art_job_event()
    # Anthropic streams can end at message_stop without a literal [DONE].
    # Flush any prose recovered after an unclosed but valid mav_art JSON block.
    recovered_visible = note_filter.flush_visible()
    if recovered_visible:
        yield f"data: {json.dumps({'choices': [{'delta': {'content': recovered_visible}}]}, ensure_ascii=False)}\n\n".encode("utf-8")
    if art_event:
        yield art_event
    elif native_job_id and not native_images:
        fail_native_art_job(native_job_id, "OpenAI没有返回最终图片")
    usage_event = build_token_usage_event()
    if usage_event:
        yield usage_event


def _normalize_note_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _extract_note_content(raw_note: str) -> Optional[str]:
    clean = raw_note.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)

    try:
        data = json.loads(clean)
        content = data.get("content", "") if isinstance(data, dict) else ""
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            content = clean
        else:
            try:
                data = json.loads(match.group())
                content = data.get("content", "") if isinstance(data, dict) else ""
            except json.JSONDecodeError:
                content = clean

    content = re.sub(r"</?mav_note>", "", content).strip()
    if not content:
        return None
    return content[:1000]


def save_mav_notes(raw_notes: list) -> int:
    """保存主模型自主生成的Mav便签，做基础去重和格式保护。"""
    if not raw_notes:
        return 0

    existing = db.get_mav_notes(limit=30)
    existing_norm = {_normalize_note_text(note.get("content", "")) for note in existing}
    saved_count = 0

    for raw_note in raw_notes[:1]:
        content = _extract_note_content(raw_note)
        if not content:
            continue

        normalized = _normalize_note_text(content)
        if not normalized:
            continue
        if normalized in existing_norm:
            logger.info("跳过重复Mav便签")
            continue
        if any(normalized in old or old in normalized for old in existing_norm if old):
            logger.info("跳过相似Mav便签")
            continue

        if db.add_note(content):
            existing_norm.add(normalized)
            saved_count += 1
            logger.info("已保存Mav自主便签")

    return saved_count


PORTRAIT_REPAIR_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": list(PORTRAIT_BY_KEY)},
                "action": {"type": "string", "enum": ["revise", "append"]},
                "content": {"type": "string"},
            },
            "required": ["section", "action", "content"],
            "additionalProperties": False,
        },
    }
}


def _apply_mav_portrait_update(update: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Write an already validated update without ever logging its prose."""
    result = db.apply_portrait_update(
        update["section"],
        update["action"],
        update["content"]
    )
    if not result.get("updated"):
        logger.warning(
            f"画像更新未写入 section={update['section']} action={update['action']} "
            f"reason={result.get('reason', 'unknown')}"
        )
        return None

    if result.get("over_limit"):
        logger.warning(
            f"画像更新超出软上限 section={result['section_key']} "
            f"chars={result['char_count']}"
        )
    else:
        logger.info(
            f"画像已更新 section={result['section_key']} action={result['action']} "
            f"chars={result['char_count']}"
        )
    return result


def save_mav_portrait_updates(raw_updates: list) -> Optional[Dict[str, Any]]:
    """解析并保存第一条合法画像更新；日志永远不输出画像正文。"""
    if not raw_updates:
        return None
    if not config.PORTRAIT_UPDATES_ENABLED:
        logger.warning("收到mav_portrait块，但画像更新开关未启用")
        return None

    update, error, method = parse_portrait_update_with_repair(raw_updates[0])
    if error:
        logger.error(f"画像更新解析失败 reason={error}")
        return None
    if method == "mechanical":
        logger.info("画像更新已通过机械格式修复")
    return _apply_mav_portrait_update(update)


def _extract_portrait_repair_text(payload: Dict[str, Any]) -> str:
    """Read text from normalized OpenAI, Anthropic, or compatible responses."""
    for choice in payload.get("choices", []) or []:
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    parts = []
    for block in payload.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


async def _repair_portrait_update_with_main_model(
    raw_block: str,
    context: Dict[str, Any],
) -> tuple[Optional[Dict[str, str]], Optional[str]]:
    """Ask the same main model to repair structure only, with no conversation replay."""
    model = str((context or {}).get("main_model") or "").strip()
    if not model:
        return None, "missing_main_model"

    prompt = (
        "下面是一条由你刚才输出、但系统无法解析的画像更新。"
        "请只修复JSON结构，不要概括、润色、补充或改变content的意思。"
        "section只能取既定枚举，action只能是revise或append。"
        "严格只返回结构化JSON对象，不要Markdown、标签或解释。\n\n"
        "待修复内容：\n" + (raw_block or "")
    )
    parsed = {
        "format": "openai",
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "system": "你正在修复自己上一条画像更新的序列化格式；只修结构，保留原意。",
        "stream": False,
        "max_tokens": 800,
        "output_config": PORTRAIT_REPAIR_OUTPUT_CONFIG,
        "extra": {},
    }
    try:
        _, response = await router_service.route_request(
            parsed,
            api_key=str((context or {}).get("api_key") or "").strip() or None,
        )
        repaired_text = _extract_portrait_repair_text(response.json())
        if not repaired_text:
            return None, "empty_repair_response"
        update, error = parse_portrait_update(repaired_text)
        return update, error
    except Exception as exc:
        logger.error(f"画像更新主模型结构修复失败 error_type={type(exc).__name__}")
        return None, "repair_request_failed"


async def save_mav_portrait_updates_with_recovery(
    raw_updates: list,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Strict parse -> mechanical repair -> same-model repair -> visible failure."""
    if not raw_updates:
        return {"status": "none"}
    if not config.PORTRAIT_UPDATES_ENABLED:
        logger.warning("收到mav_portrait块，但画像更新开关未启用")
        return {"status": "none"}

    raw_block = raw_updates[0]
    update, error, method = parse_portrait_update_with_repair(raw_block)
    if update and method == "mechanical":
        logger.info("画像更新已通过机械格式修复")

    if not update and context:
        logger.warning(f"画像更新本地解析失败，交回主模型修复 reason={error}")
        update, error = await _repair_portrait_update_with_main_model(raw_block, context)
        if update:
            logger.info("画像更新已通过主模型结构修复")

    if not update:
        logger.error(f"画像更新解析失败 reason={error or 'unknown'}")
        return {"status": "failed", "reason": error or "invalid_update"}

    result = _apply_mav_portrait_update(update)
    if not result:
        return {"status": "failed", "reason": "database_rejected"}
    return {"status": "updated", "result": result}


def strip_internal_blocks_from_text(text: str) -> tuple[str, list, list, list]:
    """从非流式回复中剥离全部内部块，并分别返回便签与画像更新。"""
    note_filter = InternalBlockFilter()
    visible = note_filter.feed(text or "") + note_filter.flush_visible()
    return visible.strip(), note_filter.notes, note_filter.portraits, note_filter.arts


async def call_vision_model(image_base64: str, mime_type: str) -> str:
    """
    调用视觉模型获取图片描述（中间层模式使用）
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{config.VL_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.VL_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.VL_MODEL,
                    "max_tokens": 500,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_base64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": "请先判断这张图片的类型，再按以下方式描述：\n\n【代码/技术文档截图】\n\n- 完整提取代码或文字内容，保持原有格式\n\n- 简要说明排版结构（几列、标题位置等）\n\n- 无需描述颜色、字体、背景等视觉细节\n\n【聊天记录截图】\n\n- 仔细辨认说话方（注意如果是LLM对话，左侧通常是助手，右侧通常是用户）\n\n- 按顺序提取完整对话内容，标注发言者\n\n- 简要说明对话氛围（轻松/严肃/调侃等）\n\n- 无需描述UI细节\n\n【日常照片/场景图】\n\n- 描述主体内容（人物、物体、动作、表情）\n\n- 说明整体氛围和环境背景\n\n- 注意空间关系和视觉重点\n\n【其他类型】\n\n- 描述主要内容和信息，避免过度细节\n\n请直接输出描述，无需说明你在使用哪种模式。"
                            }
                        ]
                    }]
                }
            )
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"视觉模型调用失败: {e}")
        return "[图片描述获取失败]"


def build_image_middleware_context(description: str, original_text: str = "") -> str:
    """Build a clear text wrapper for VL-middle-layer image messages."""
    from app_identity import names_in_template
    image_context = (
        "【用户上传了一张图片】\n"
        "下面不是用户手写的原文，而是系统先用视觉模型读取这张图片后生成的描述。"
        + names_in_template("请把它当作图片内容来理解，可以自然回应图片本身；不要把【图片描述】当成岚亲自输入的一段文字。\n\n")
        + f"【图片描述】\n{description or '图片描述为空'}"
    )
    original_text = (original_text or "").strip()
    if not original_text:
        return image_context
    return f"{image_context}\n\n【用户随图片附带的文字】\n{original_text}"


async def extract_search_query(user_message: str) -> str:
    """把用户消息提炼成一个简洁的搜索查询词"""
    try:
        from config import config
        import httpx
        summary_config = config.get_summary_config()
        if not summary_config.get("api_key"):
            return user_message[:50]

        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                f"{summary_config['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {summary_config['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": summary_config["model"],
                    "max_tokens": 30,
                    "messages": [{
                        "role": "user",
                        "content": f"把以下问题提炼成一个简洁的搜索引擎查询词（10字以内，不要标点）：\n{user_message}\n\n查询词："
                    }]
                }
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return user_message[:50]


async def call_search_api(query: str) -> str:
    """
    调用Tavily搜索API，返回格式化的搜索结果文本
    """
    if not config.TAVILY_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": config.TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True
                }
            )
            if response.status_code == 200:
                result = response.json()
                parts = []
                if result.get("answer"):
                    parts.append(f"搜索摘要：{result['answer']}")
                for r in result.get("results", [])[:5]:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    content = r.get("content", "")[:300]
                    parts.append(f"- {title}\n  {url}\n  {content}")
                return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"搜索API调用失败: {e}")
    return ""


def parse_sse_stream(content: str) -> list:
    """
    解析SSE格式的流式响应，提取所有data字段的JSON内容

    Args:
        content: SSE格式的原始文本

    Returns:
        list: 解析后的JSON对象列表
    """
    results = []
    lines = content.split('\n')
    current_data = []

    for line in lines:
        if line.startswith('data:'):
            # 提取data:后面的内容
            data_content = line[5:].strip()
            if data_content and data_content != '[DONE]':
                try:
                    results.append(json.loads(data_content))
                except json.JSONDecodeError:
                    pass
        elif line.strip() == '':
            # 空行，可能表示一个完整的数据块结束
            pass

    return results


def extract_openai_content(sse_data: list) -> str:
    """
    从OpenAI SSE数据中提取完整的assistant回复

    Args:
        sse_data: 解析后的SSE数据列表

    Returns:
        str: 完整的assistant回复内容
    """
    content = ""
    for data in sse_data:
        if "choices" in data:
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content += delta.get("content", "")
    return content


def extract_claude_content(sse_data: list) -> str:
    """
    从Claude SSE数据中提取完整的assistant回复

    Args:
        sse_data: 解析后的SSE数据列表

    Returns:
        str: 完整的assistant回复内容
    """
    content = ""
    for data in sse_data:
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                content += delta.get("text", "")
    return content


def extract_google_content(sse_data: list) -> str:
    """
    从Google SSE数据中提取完整的assistant回复

    Args:
        sse_data: 解析后的SSE数据列表

    Returns:
        str: 完整的assistant回复内容
    """
    content = ""
    for data in sse_data:
        if "candidates" in data:
            for candidate in data.get("candidates", []):
                result_content = candidate.get("content", {})
                parts = result_content.get("parts", [])
                for part in parts:
                    if "text" in part:
                        content += part["text"]
    return content


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("网关服务启动中...")
    logger.info(f"配置: 数据库={config.DATABASE_PATH}")
    logger.info(f"总结模型: {config.SUMMARY_MODEL} @ {config.SUMMARY_API_PROVIDER}")
    try:
        interrupt_stale_comment_jobs()
    except Exception:
        logger.exception("恢复笔记评论任务状态失败")

    # 启动调度器
    from scheduler import task_scheduler
    await task_scheduler.start()

    # 恢复中断的阅读任务
    from reading import resume_interrupted_books
    await resume_interrupted_books()
    await resume_art_jobs()

    yield

    # 关闭调度器
    await task_scheduler.stop()
    logger.info("网关服务关闭中...")


app = FastAPI(
    title="AI Gateway Service",
    description="统一AI网关服务，支持记忆注入和智能路由",
    version="2.2.0",
    lifespan=lifespan
)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in config.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reading_router)
app.include_router(notes_router)
app.include_router(chat_images_router)
app.include_router(art_generation_router)


# ========== 对话持久化 API 路由 ==========

api_router = APIRouter(prefix="/api")


@api_router.get("/identity")
async def get_app_identity():
    return get_identity()


@api_router.put("/identity")
async def put_app_identity(request: Request):
    data = await request.json()
    names = {}
    for key in ("assistant_name", "user_name"):
        value = data.get(key)
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail="请填写两个称呼")
        value = value.strip()
        if not value or len(value) > 32 or any(ord(ch) < 32 for ch in value):
            raise HTTPException(status_code=400, detail="称呼需为 1–32 个字符，不能包含换行或控制字符")
        names[key] = value
    return db.update_app_identity(**names)


@api_router.get("/conversations")
async def list_conversations(limit: int = 50, offset: int = 0):
    """列出所有对话"""
    try:
        result = db.list_conversations(limit=limit, offset=offset)
        return result
    except Exception as e:
        logger.error(f"列出对话失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.post("/conversations")
async def create_conversation(request: Request):
    """创建对话"""
    try:
        data = await request.json()
        conversation_id = data.get("id")
        title = data.get("title", "新对话")

        if not conversation_id:
            raise HTTPException(status_code=400, detail="缺少 id 字段")

        if db.create_conversation(id=conversation_id, title=title):
            return {"status": "success", "id": conversation_id}
        else:
            raise HTTPException(status_code=500, detail="创建对话失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建对话失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """获取单个对话"""
    try:
        conversation = db.get_conversation(conversation_id)
        if conversation:
            return conversation
        else:
            raise HTTPException(status_code=404, detail="对话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取对话失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, request: Request):
    """更新对话标题或历史折叠状态"""
    try:
        data = await request.json()
        if "is_folded" in data and type(data["is_folded"]) is bool:
            success = db.set_conversation_folded(conversation_id, data["is_folded"])
        elif isinstance(data.get("title"), str) and data["title"].strip():
            success = db.update_conversation(id=conversation_id, title=data["title"].strip())
        else:
            raise HTTPException(status_code=400, detail="需要有效的 title 或 is_folded 字段")

        if success:
            return {"status": "success"}
        else:
            raise HTTPException(status_code=404, detail="对话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新对话失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除对话（级联删除消息）"""
    try:
        image_paths = collect_message_image_paths(conversation_id)
        art_paths = collect_art_image_paths(conversation_id)
        if db.delete_conversation(conversation_id):
            remove_image_files(image_paths)
            remove_art_image_files(art_paths)
            return {"status": "success"}
        else:
            raise HTTPException(status_code=404, detail="对话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除对话失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, response: Response, limit: int = 50, offset: int = 0, order: str = "asc"):
    """获取对话的所有消息"""
    try:
        # 先检查对话是否存在
        conversation = db.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        response.headers["Cache-Control"] = "no-store"
        result = db.list_messages(conversation_id=conversation_id, limit=limit, offset=offset, order=order)
        return attach_art_jobs_to_message_result(attach_images_to_message_result(result))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"列出消息失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.get("/conversations/{conversation_id}/message-map")
async def get_conversation_message_map(conversation_id: str, response: Response):
    """获取对话地图与搜索使用的全量轻量消息。"""
    if not db.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    response.headers["Cache-Control"] = "no-store"
    return db.list_message_map(conversation_id)


@api_router.post("/conversations/{conversation_id}/messages")
async def add_message(conversation_id: str, request: Request):
    """保存单条消息"""
    try:
        # 先检查对话是否存在
        conversation = db.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        data = await request.json()
        message_id = data.get("id")
        role = data.get("role")
        content = data.get("content")
        raw_images = data.get("images") or []

        if not message_id:
            raise HTTPException(status_code=400, detail="缺少 id 字段")
        if not role:
            raise HTTPException(status_code=400, detail="缺少 role 字段")
        if not content and not raw_images and not (data.get("imagePreview") or data.get("image_preview")):
            raise HTTPException(status_code=400, detail="缺少 content 字段")

        prepared_images = prepare_message_images(message_id, raw_images)
        prepared_paths = [
            path
            for image in prepared_images
            for path in (image["file_path"], image["thumb_path"])
        ]
        if db.add_message(
            id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content or "",
            thinking=data.get("thinking"),
            model=data.get("model"),
            image_preview=data.get("imagePreview") or data.get("image_preview"),
            file_name=data.get("fileName") or data.get("file_name"),
            input_tokens=data.get("inputTokens") or data.get("input_tokens"),
            output_tokens=data.get("outputTokens") or data.get("output_tokens"),
            total_tokens=data.get("totalTokens") or data.get("total_tokens"),
            cache_creation_tokens=data.get("cacheCreationTokens") or data.get("cache_creation_tokens"),
            cache_read_tokens=data.get("cacheReadTokens") or data.get("cache_read_tokens"),
            cache_creation_1h_tokens=data.get("cacheCreation1hTokens") or data.get("cache_creation_1h_tokens"),
            message_images=prepared_images,
        ):
            return {
                "status": "success",
                "id": message_id,
                "images": serialize_message_images(prepared_images),
            }
        else:
            remove_image_files(prepared_paths)
            raise HTTPException(status_code=500, detail="保存消息失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存消息失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.post("/conversations/{conversation_id}/messages/batch")
async def add_messages_batch(conversation_id: str, request: Request):
    """批量保存消息"""
    try:
        # 先检查对话是否存在
        conversation = db.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")

        data = await request.json()
        messages = data.get("messages", [])

        if not messages:
            raise HTTPException(status_code=400, detail="缺少 messages 字段")

        # 验证每条消息
        for i, msg in enumerate(messages):
            if not msg.get("id"):
                raise HTTPException(status_code=400, detail=f"第 {i+1} 条消息缺少 id 字段")
            if not msg.get("role"):
                raise HTTPException(status_code=400, detail=f"第 {i+1} 条消息缺少 role 字段")
            if not msg.get("content"):
                raise HTTPException(status_code=400, detail=f"第 {i+1} 条消息缺少 content 字段")

        if db.add_messages_batch(conversation_id=conversation_id, messages=messages):
            return {"status": "success", "count": len(messages)}
        else:
            raise HTTPException(status_code=500, detail="批量保存消息失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量保存消息失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@api_router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(conversation_id: str, message_id: str):
    """删除消息"""
    try:
        image_paths = collect_message_image_paths(conversation_id, message_id=message_id)
        art_paths = collect_art_image_paths(conversation_id, message_id=message_id)
        if db.delete_message(conversation_id=conversation_id, message_id=message_id):
            remove_image_files(image_paths)
            remove_art_image_files(art_paths)
            return {"status": "success"}
        else:
            raise HTTPException(status_code=404, detail="消息不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除消息失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


# 注册 API 路由
app.include_router(api_router)


@app.post("/api/provider-models")
async def list_provider_models(request: Request):
    """使用浏览器提交的 API Key 查询对应服务商的可用模型，不保存 Key。"""
    try:
        data = await request.json()
        api_key = str(data.get("api_key", "")).strip()
        if not api_key:
            raise HTTPException(status_code=400, detail="请先填写 API Key")

        provider, models = await router_service.list_available_models(api_key)
        return {"provider": provider, "models": models}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.warning("读取模型列表失败: provider_status=%s", status_code)
        if status_code in {401, 403}:
            raise HTTPException(status_code=401, detail="API Key 未通过服务商验证")
        raise HTTPException(
            status_code=502,
            detail=f"服务商暂时无法返回模型列表（HTTP {status_code}）"
        )
    except httpx.RequestError as exc:
        logger.warning("连接模型列表接口失败: %s", exc)
        raise HTTPException(status_code=502, detail="暂时连接不到模型列表接口")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("读取模型列表失败: %s", exc)
        raise HTTPException(status_code=500, detail="读取模型列表失败")


def get_api_key(request: Request) -> Optional[str]:
    """从请求中提取API Key"""
    # 从Authorization头获取
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]

    # 从x-api-key头获取（Claude格式）
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        return api_key

    return None


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


@app.get("/status")
async def get_status():
    """获取服务状态"""
    return {
        "service": "AI Gateway",
        "version": "2.0.0",
        "scheduler": {
            "running": True,
            "summarizer_state": summarizer.get_state()
        },
        "config": {
            "model": "dynamic",
            "summary_model": config.SUMMARY_MODEL,
            "summary_trigger_rounds": config.SUMMARY_TRIGGER_ROUNDS,
            "context_window_rounds": config.CONTEXT_WINDOW_ROUNDS,
            "memory_top_n": config.MEMORY_TOP_N,
            "memory_random_n": config.MEMORY_RANDOM_N,
            "short_term_memory_days": config.SHORT_TERM_MEMORY_DAYS,
            "mav_notes_limit": config.MAV_NOTES_LIMIT
        }
    }


@app.post("/reset")
async def reset_state():
    """重置服务状态"""
    summarizer.reset()
    return {"status": "reset"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI格式聊天完成接口
    支持Kelivo OpenAI类型
    """
    try:
        # 解析请求
        data = await request.json()
        parsed = RequestFormatter.parse_request(data, "/v1/chat/completions", "POST")

        if not parsed:
            raise HTTPException(status_code=400, detail="无效的请求格式")

        # 处理图片（中间层模式：调视觉模型获取描述，拼入消息）
        image_data = data.get("image")
        direct_send = data.get("direct_send", False)
        parsed["enable_thinking"] = data.get("enable_thinking", False)
        api_key = get_api_key(request)
        detected_backend = router_service.router.identify_backend_by_api_key(api_key)
        # OpenAI Responses accepts native image inputs. Keep the legacy Anthropic
        # fallback only for other direct-send providers.
        parsed["force_anthropic"] = bool(direct_send and detected_backend != "openai")
        parsed["enable_search"] = data.get("enable_search", False)
        parsed["enable_memory_recall"] = bool(data.get("stream", False))
        if "enable_prompt_cache" in data:
            cache_enabled = bool(data.get("enable_prompt_cache"))
            parsed["enable_prompt_cache"] = cache_enabled
            parsed["prompt_cache_ttl"] = "1h" if cache_enabled else "off"
        if data.get("conversation_id"):
            parsed["prompt_cache_key"] = f"mhome:{data['conversation_id']}"
        enable_image_generation = bool(data.get("enable_image_generation", False))
        native_image_generation = enable_image_generation and detected_backend == "openai"
        parsed["enable_native_image_generation"] = native_image_generation
        if native_image_generation:
            parsed["native_image_model"] = config.ART_IMAGE_MODEL
        current_message_id = data.get("message_id", "")

        if image_data and not direct_send:
            description = await call_vision_model(
                image_data.get("base64", ""),
                image_data.get("mimeType", "image/jpeg")
            )
            # 将描述拼入最后一条user消息
            messages = parsed.get("messages", [])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    original = messages[i].get("content", "")
                    messages[i]["content"] = build_image_middleware_context(description, original)
                    break
            parsed["messages"] = messages

        if current_message_id:
            parsed = await enrich_messages_with_image_context(
                parsed,
                current_message_id=current_message_id,
                direct_send=direct_send,
            )

        # 从parsed中提取用户消息
        user_message = ""
        for msg in reversed(parsed.get("messages", [])):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    user_message = content
                elif isinstance(content, list):
                    # 多模态消息，只提取文字部分
                    text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
                    user_message = "\n".join(text_parts)
                else:
                    user_message = str(content)
                break

        # 处理联网搜索（非Claude模型走中间层，Claude走原生tool）
        enable_search = data.get("enable_search", False)
        search_tool_used = False
        if enable_search and user_message:
            # 判断backend类型
            logger.info(f"search_notice: enabled backend={detected_backend} user_message_len={len(user_message)}")
            if detected_backend not in {"anthropic", "openai"}:
                # 非Claude：先用Haiku提炼查询词，再调Tavily
                search_query = await extract_search_query(user_message)
                search_result = await call_search_api(search_query)
                if search_result:
                    search_tool_used = True
                    from datetime import datetime
                    current_date = datetime.now().strftime("%Y年%m月%d日")
                    messages = parsed.get("messages", [])
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "user":
                            original = messages[i].get("content", "")
                            injected = f"[当前日期：{current_date}]\n[以下是联网搜索结果，请基于这些信息回答用户的问题]\n{search_result}\n\n[用户消息]\n{original}" if original else f"[当前日期：{current_date}]\n[以下是联网搜索结果，请基于这些信息回答用户的问题]\n{search_result}"
                            messages[i]["content"] = injected
                            break
                    parsed["messages"] = messages

        # 首次升级时先从数据库历史重建检查点；普通请求只是一次轻量状态检查。
        await summarizer.restore_summary_if_needed()
        # 获取即时对话总结（在记忆注入前调用）
        instant_summary = summarizer.get_summary()

        # 注入记忆（包含即时总结）
        parsed = await injector_service.process_request(
            parsed,
            instant_summary=instant_summary,
            internal_instructions=build_internal_instructions(),
            dynamic_instructions=(
                [OPENAI_NATIVE_ART_INSTRUCTION]
                if native_image_generation
                else [MAV_ART_INSTRUCTION] if enable_image_generation else None
            ),
        )

        # 获取API Key
        # 判断是否流式（从原始请求中获取）
        is_stream = data.get("stream", False)

        # 流式响应处理
        if is_stream:
            # 流式路由
            backend_type, stream_gen = await router_service.route_request_stream(
                parsed, api_key=api_key
            )

            collected_chunks = []
            note_filter = InternalBlockFilter()
            usage_tracker = TokenUsageTracker()
            recent_context_parts = []
            for item in (data.get("messages") or [])[-4:]:
                content = item.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        block.get("text", "") for block in content if block.get("type") == "text"
                    )
                recent_context_parts.append(f"{item.get('role', 'unknown')}：{str(content)[:1500]}")
            art_context = {
                "enabled": enable_image_generation,
                "mode": "native" if native_image_generation else "studio",
                "main_model": parsed.get("model", ""),
                "conversation_id": data.get("conversation_id", ""),
                "user_message_id": current_message_id,
                "assistant_message_id": data.get("assistant_message_id", ""),
                "user_request": user_message,
                "recent_context": "\n\n".join(recent_context_parts),
            }

            async def stream_with_collect():
                if search_tool_used:
                    logger.info(f"search_notice: emitting tavily notice backend={backend_type}")
                    yield f"data: {json.dumps({'type': 'search_tool_used'}, ensure_ascii=False)}\n\n".encode("utf-8")
                async for chunk in filter_internal_blocks_stream(
                    stream_gen,
                    note_filter,
                    usage_tracker,
                    art_context=art_context,
                    portrait_context={
                        "main_model": parsed.get("model", ""),
                        "api_key": api_key,
                    },
                ):
                    collected_chunks.append(chunk)
                    yield chunk

            async def on_stream_complete():
                """流结束后提取内容，喂给总结器"""
                try:
                    full_content = b"".join(collected_chunks).decode("utf-8", errors="ignore")
                    sse_data = parse_sse_stream(full_content)
                    assistant_message = extract_openai_content(sse_data) or extract_claude_content(sse_data)

                    if user_message and assistant_message:
                        summarizer.add_conversation_round(user_message, assistant_message)
                        if summarizer.should_summarize():
                            conversation_text = summarizer.get_conversation_for_summary()
                            recent_text = summarizer.get_recent_conversation_for_memory()
                            await summarizer.summarize_if_needed()
                            await injector_service.judge_memory(conversation_text, recent_text)
                        else:
                            await summarizer.summarize_if_needed()
                except Exception as e:
                    logger.error(f"流式响应后处理失败: {e}")

            async def stream_wrapper():
                async for chunk in stream_with_collect():
                    yield chunk
                # 流结束后处理
                asyncio.create_task(on_stream_complete())

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Request-Backend": backend_type
                }
            )
        else:
            # 非流式响应
            backend_type, response = await router_service.route_request(
                parsed,
                api_key=api_key
            )
            result = response.json()

            # 提取助手回复并添加到总结器
            assistant_message = ""
            if "choices" in result:
                for choice in result.get("choices", []):
                    message = choice.get("message", {})
                    if message.get("content"):
                        visible_content, raw_notes, raw_portraits, _ = strip_internal_blocks_from_text(
                            message.get("content", "")
                        )
                        message["content"] = visible_content
                        save_mav_notes(raw_notes)
                        save_mav_portrait_updates(raw_portraits)
                        assistant_message = visible_content
                        break

            if user_message and assistant_message:
                summarizer.add_conversation_round(user_message, assistant_message)
                if summarizer.should_summarize():
                    conversation_text = summarizer.get_conversation_for_summary()
                    recent_text = summarizer.get_recent_conversation_for_memory()
                    asyncio.create_task(summarizer.summarize_if_needed())
                    asyncio.create_task(injector_service.judge_memory(conversation_text, recent_text))
                else:
                    asyncio.create_task(summarizer.summarize_if_needed())

            return Response(
                content=json.dumps(result),
                media_type="application/json",
                headers={"X-Request-Backend": backend_type}
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"处理请求失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/messages")
async def claude_messages(request: Request):
    """
    Claude格式消息接口
    支持Kelivo Claude类型
    """
    try:
        # 解析请求
        data = await request.json()
        parsed = RequestFormatter.parse_request(data, "/messages", "POST")

        if not parsed:
            raise HTTPException(status_code=400, detail="无效的请求格式")

        # 从parsed中提取用户消息
        user_message = ""
        for msg in reversed(parsed.get("messages", [])):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
                    user_message = "\n".join(text_parts)
                else:
                    user_message = content if isinstance(content, str) else ""
                break

        # 获取即时对话总结（在记忆注入前调用）
        await summarizer.restore_summary_if_needed()
        instant_summary = summarizer.get_summary()

        # 注入记忆（包含即时总结）
        parsed = await injector_service.process_request(
            parsed,
            instant_summary=instant_summary,
            internal_instructions=build_internal_instructions()
        )

        # 获取API Key
        api_key = get_api_key(request)

        # 判断是否流式（从原始请求中获取）
        is_stream = data.get("stream", True)

        # 流式响应处理
        if is_stream:
            # 流式路由
            backend_type, stream_gen = await router_service.route_request_stream(
                parsed, api_key=api_key
            )

            collected_chunks = []
            note_filter = InternalBlockFilter()
            usage_tracker = TokenUsageTracker()

            async def stream_with_collect():
                async for chunk in filter_internal_blocks_stream(
                    stream_gen,
                    note_filter,
                    usage_tracker,
                    portrait_context={
                        "main_model": parsed.get("model", ""),
                        "api_key": api_key,
                    },
                ):
                    collected_chunks.append(chunk)
                    yield chunk

            async def on_stream_complete():
                """流结束后提取内容，喂给总结器"""
                try:
                    full_content = b"".join(collected_chunks).decode("utf-8", errors="ignore")
                    sse_data = parse_sse_stream(full_content)
                    assistant_message = extract_claude_content(sse_data)

                    if user_message and assistant_message:
                        summarizer.add_conversation_round(user_message, assistant_message)
                        if summarizer.should_summarize():
                            conversation_text = summarizer.get_conversation_for_summary()
                            recent_text = summarizer.get_recent_conversation_for_memory()
                            await summarizer.summarize_if_needed()
                            await injector_service.judge_memory(conversation_text, recent_text)
                        else:
                            await summarizer.summarize_if_needed()
                except Exception as e:
                    logger.error(f"流式响应后处理失败: {e}")

            async def stream_wrapper():
                async for chunk in stream_with_collect():
                    yield chunk
                # 流结束后处理
                asyncio.create_task(on_stream_complete())

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Request-Backend": backend_type
                }
            )
        else:
            # 非流式路由
            backend_type, response = await router_service.route_request(
                parsed, api_key=api_key
            )
            result = response.json()

            # 提取助手回复并添加到总结器
            assistant_message = ""
            response_notes = []
            response_portraits = []
            if "content" in result:
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        visible_text, raw_notes, raw_portraits, _ = strip_internal_blocks_from_text(
                            block.get("text", "")
                        )
                        block["text"] = visible_text
                        response_notes.extend(raw_notes)
                        response_portraits.extend(raw_portraits)
                        assistant_message += visible_text
            save_mav_notes(response_notes)
            save_mav_portrait_updates(response_portraits)

            if user_message and assistant_message:
                summarizer.add_conversation_round(user_message, assistant_message)
                if summarizer.should_summarize():
                    conversation_text = summarizer.get_conversation_for_summary()
                    recent_text = summarizer.get_recent_conversation_for_memory()
                    asyncio.create_task(summarizer.summarize_if_needed())
                    asyncio.create_task(injector_service.judge_memory(conversation_text, recent_text))
                else:
                    asyncio.create_task(summarizer.summarize_if_needed())

            return Response(
                content=json.dumps(result),
                media_type="application/json",
                headers={"X-Request-Backend": backend_type}
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"处理请求失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1beta/models/{model}:generateContent")
async def google_generate_content(model: str, request: Request):
    """
    Google格式生成内容接口
    支持Kelivo Google类型
    """
    try:
        # 解析请求
        data = await request.json()
        path = f"/v1beta/models/{model}:generateContent"
        parsed = RequestFormatter.parse_request(data, path, "POST")

        if not parsed:
            raise HTTPException(status_code=400, detail="无效的请求格式")

        # 更新模型名称
        parsed["model"] = model

        # 从parsed中提取用户消息（从contents中提取）
        user_message = ""
        if "contents" in parsed:
            for content in reversed(parsed.get("contents", [])):
                parts = content.get("parts", [])
                for part in reversed(parts):
                    if "text" in part:
                        user_message = part["text"]
                        break
                if user_message:
                    break

        # 获取即时对话总结
        await summarizer.restore_summary_if_needed()
        instant_summary = summarizer.get_summary()

        # 注入记忆
        parsed = await injector_service.process_request(parsed, instant_summary=instant_summary)

        # 获取API Key
        api_key = get_api_key(request)

        # 路由并转发
        backend_type, response = await router_service.route_request(
            parsed,
            api_key=api_key
        )

        # 流式响应处理
        async def stream_wrapper():
            try:
                # 尝试流式迭代
                async for chunk in response.aiter_bytes():
                    yield chunk
            except Exception as e:
                logger.warning(f"流式读取失败，回退到普通模式: {e}")
                # 回退：一次性读取
                yield response.content

        return StreamingResponse(
            stream_wrapper(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Request-Backend": backend_type
            }
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"处理请求失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========== 对话管理接口 ==========

@app.get("/api/conversations")
async def list_conversations(limit: int = 50, offset: int = 0):
    """获取对话列表"""
    conversations = db.list_conversations(limit=limit, offset=offset)
    return {"conversations": conversations, "total": len(conversations)}

@app.post("/api/conversations")
async def create_conversation(request: Request):
    """创建对话"""
    data = await request.json()
    success = db.create_conversation(
        id=data.get("id"),
        title=data.get("title", "新对话")
    )
    if success:
        return {"status": "success", "id": data.get("id")}
    raise HTTPException(status_code=500, detail="创建失败")

@app.patch("/api/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, request: Request):
    """更新对话标题或历史折叠状态"""
    data = await request.json()
    if "is_folded" in data and type(data["is_folded"]) is bool:
        success = db.set_conversation_folded(conversation_id, data["is_folded"])
    elif isinstance(data.get("title"), str) and data["title"].strip():
        success = db.update_conversation(id=conversation_id, title=data["title"].strip())
    else:
        raise HTTPException(status_code=400, detail="需要有效的 title 或 is_folded 字段")
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="对话不存在")

@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除对话"""
    image_paths = collect_message_image_paths(conversation_id)
    art_paths = collect_art_image_paths(conversation_id)
    if db.delete_conversation(id=conversation_id):
        remove_image_files(image_paths)
        remove_art_image_files(art_paths)
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="对话不存在")

@app.get("/api/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, response: Response, limit: int = 100, offset: int = 0, order: str = "asc"):
    """获取对话消息"""
    response.headers["Cache-Control"] = "no-store"
    result = db.list_messages(conversation_id=conversation_id, limit=limit, offset=offset, order=order)
    return attach_art_jobs_to_message_result(attach_images_to_message_result(result))

@app.post("/api/conversations/{conversation_id}/messages")
async def save_message(conversation_id: str, request: Request):
    """保存单条消息"""
    data = await request.json()
    raw_images = data.get("images") or []
    prepared_images = prepare_message_images(data.get("id"), raw_images)
    prepared_paths = [
        path
        for image in prepared_images
        for path in (image["file_path"], image["thumb_path"])
    ]
    success = db.add_message(
        id=data.get("id"),
        conversation_id=conversation_id,
        role=data.get("role"),
        content=data.get("content") or "",
        thinking=data.get("thinking"),
        model=data.get("model"),
        image_preview=data.get("imagePreview") or data.get("image_preview"),
        file_name=data.get("fileName") or data.get("file_name"),
        input_tokens=data.get("inputTokens") or data.get("input_tokens"),
        output_tokens=data.get("outputTokens") or data.get("output_tokens"),
        total_tokens=data.get("totalTokens") or data.get("total_tokens"),
        cache_creation_tokens=data.get("cacheCreationTokens") or data.get("cache_creation_tokens"),
        cache_read_tokens=data.get("cacheReadTokens") or data.get("cache_read_tokens"),
        cache_creation_1h_tokens=data.get("cacheCreation1hTokens") or data.get("cache_creation_1h_tokens"),
        message_images=prepared_images,
    )
    if success:
        return {"status": "success", "images": serialize_message_images(prepared_images)}
    remove_image_files(prepared_paths)
    raise HTTPException(status_code=500, detail="保存失败")

@app.post("/api/conversations/{conversation_id}/messages/batch")
async def save_messages_batch(conversation_id: str, request: Request):
    """批量保存消息"""
    data = await request.json()
    messages = data.get("messages", [])
    success = db.add_messages_batch(conversation_id=conversation_id, messages=messages)
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="批量保存失败")

@app.delete("/api/conversations/{conversation_id}/messages/{message_id}/after")
async def delete_messages_after(conversation_id: str, message_id: str, include_anchor: bool = False):
    """删除某条消息之后的历史；编辑历史消息时可同时删除锚点消息。"""
    image_paths = collect_message_image_paths(
        conversation_id,
        after_message_id=message_id,
        include_anchor=include_anchor,
    )
    art_paths = collect_art_image_paths(
        conversation_id,
        after_message_id=message_id,
        include_anchor=include_anchor,
    )
    if db.delete_messages_after(
        conversation_id=conversation_id,
        message_id=message_id,
        include_anchor=include_anchor
    ):
        remove_image_files(image_paths)
        remove_art_image_files(art_paths)
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="消息不存在")

# ========== 记忆管理接口 ==========

@app.post("/memory/add")
async def add_memory(request: Request):
    """添加记忆"""
    try:
        data = await request.json()
        memory_id = db.add_memory(
            content=data.get("content", ""),
            recall_text=data.get("recall_text"),
            keywords=data.get("keywords"),
            event_date=data.get("event_date"),
            room=data.get("room"),
            title=data.get("title"),
            emotion_weight=data.get("emotion_weight", 3)
        )

        if memory_id:
            return {"status": "success", "id": memory_id}
        else:
            raise HTTPException(status_code=500, detail="添加失败")

    except Exception as e:
        logger.error(f"添加记忆失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memory/list")
async def list_memories(limit: int = 50, offset: int = 0):
    """列出记忆"""
    memories = db.list_memories(limit=limit, offset=offset)
    return {"memories": memories, "count": len(memories)}


@app.delete("/memory/delete/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆"""
    if db.delete_memory(memory_id):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=404, detail="记忆不存在")


@app.get("/memory/pending")
async def check_pending_memory():
    """前端轮询：是否有新记忆生成"""
    has_pending = getattr(injector_service, '_pending_memory_notice', False)
    if has_pending:
        injector_service._pending_memory_notice = False
    return {"has_new_memory": has_pending}


# ========== 管理面板接口 ==========

def require_portrait_admin_password(request: Request) -> None:
    """画像正文接口使用独立密码；密码不复用模型 API key。"""
    expected = config.PORTRAIT_ADMIN_PASSWORD
    if not expected:
        raise HTTPException(status_code=503, detail="画像管理密码尚未配置")
    supplied = request.headers.get("X-Portrait-Password", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="画像管理密码错误")

@app.get("/admin/status")
async def admin_status():
    """详细状态，包含今天已入库的完整对话轮数。"""
    state = summarizer.get_state()
    state["daily_history_rounds"] = len(db.list_daily_archive_rounds())
    state["current_summary"] = summarizer.get_summary()
    return state

@app.get("/admin/daily-summaries")
async def admin_list_daily_summaries(limit: int = 30):
    """列出每日总结"""
    return db.list_daily_summaries(limit=limit)


@app.get("/admin/portrait/status")
async def admin_portrait_status():
    """不含正文的画像状态，可用于默认 Admin 视图。"""
    status = db.get_portrait_status()
    status.update({
        "updates_enabled": config.PORTRAIT_UPDATES_ENABLED,
        "password_configured": bool(config.PORTRAIT_ADMIN_PASSWORD)
    })
    return status


@app.get("/admin/portrait/logs")
async def admin_portrait_logs(limit: int = 100):
    """只返回 Mav 更新画像的元数据。"""
    return db.list_portrait_update_logs(limit=limit)


@app.get("/admin/portrait")
async def admin_get_portrait(request: Request):
    """读取完整画像，必须提供独立管理密码。"""
    require_portrait_admin_password(request)
    return {"sections": db.get_portrait_sections()}


@app.put("/admin/portrait")
async def admin_update_portrait(request: Request):
    """手动导入或修正完整画像，必须提供独立管理密码。"""
    require_portrait_admin_password(request)
    data = await request.json()
    sections = data.get("sections")
    if not isinstance(sections, dict):
        raise HTTPException(status_code=400, detail="sections 必须是对象")
    if db.replace_portrait_sections(sections):
        return {"status": "success"}
    raise HTTPException(status_code=400, detail="画像内容或section不合法")

@app.get("/admin/system-core")
async def admin_get_system_core():
    """获取system core"""
    return {"content": db.get_system_core() or ""}

@app.put("/admin/system-core")
async def admin_update_system_core(request: Request):
    """更新system core"""
    data = await request.json()
    success = db.update_system_core(data.get("content", ""))
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="更新失败")

@app.get("/admin/profiles")
async def admin_get_profiles():
    """获取所有profiles"""
    return db.get_all_profiles()

@app.put("/admin/profiles")
async def admin_upsert_profile(request: Request):
    """新增或更新profile"""
    data = await request.json()
    success = db.upsert_profile(data.get("key", ""), data.get("value", ""))
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="更新失败")

@app.delete("/admin/profiles")
async def admin_delete_profile(request: Request):
    """删除profile"""
    data = await request.json()
    key = data.get("key", "")
    success = db.delete_profile(key)
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="不存在")

@app.get("/admin/notes")
async def admin_get_notes():
    """获取Mav备忘"""
    return db.get_mav_notes()

@app.post("/admin/notes")
async def admin_add_note(request: Request):
    """添加Mav备忘"""
    data = await request.json()
    success = db.add_note(data.get("content", ""))
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="添加失败")

@app.delete("/admin/notes/{note_id}")
async def admin_delete_note(note_id: int):
    """删除Mav备忘"""
    success = db.delete_note(note_id)
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="备忘不存在")

@app.get("/admin/memories")
async def admin_list_memories(limit: int = 50, offset: int = 0):
    """列出长期记忆"""
    return db.list_memories(limit=limit, offset=offset)

@app.get("/admin/memory-v2/stats")
async def admin_memory_v2_stats():
    """查看二期记忆库规模。"""
    return {
        "threads": db.count_memory_threads(),
        "events": db.count_memory_events()
    }

@app.get("/admin/memory-threads")
async def admin_list_memory_threads(
    limit: int = 30,
    offset: int = 0,
    q: str = ""
):
    """列出二期记忆 Threads。"""
    query = q.strip() if q else None
    return {
        "threads": db.list_memory_threads(limit=limit, offset=offset, q=query),
        "total": db.count_memory_threads(q=query)
    }

@app.get("/admin/memory-events")
async def admin_list_memory_events(
    event_key: str = "",
    limit: int = 50,
    offset: int = 0,
    q: str = ""
):
    """列出二期记忆 Events，可按 event_key 和关键词筛选。"""
    thread_key = event_key.strip() if event_key else None
    query = q.strip() if q else None
    return {
        "events": db.list_memory_events(
            event_key=thread_key,
            limit=limit,
            offset=offset,
            q=query
        ),
        "total": db.count_memory_events(event_key=thread_key, q=query)
    }

@app.delete("/admin/memory-events/{event_id}")
async def admin_delete_memory_event(event_id: int):
    """删除单条二期记忆 Event。"""
    if db.delete_memory_event_v2(event_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Event不存在")

@app.delete("/admin/memory-threads/{event_key}")
async def admin_delete_memory_thread(event_key: str):
    """删除二期记忆 Thread，并删除其下所有 Events。"""
    if db.delete_memory_thread_v2(event_key):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Thread不存在")

@app.get("/admin/memory-write-debug")
async def admin_memory_write_debug():
    """查看最近几次长期记忆写入判断。"""
    return injector_service.get_memory_write_debug()

@app.get("/admin/memory-injection-debug")
async def admin_memory_injection_debug():
    """查看最近几次长期记忆读取/注入结果。"""
    return injector_service.get_memory_injection_debug()

@app.get("/admin/memory-embedding-status")
async def admin_memory_embedding_status():
    """查看记忆embedding配置与回填进度。"""
    missing = db.count_missing_embeddings(config.EMBEDDING_MODEL)
    return {
        "enabled": embedding_service.is_enabled(),
        "model": config.EMBEDDING_MODEL,
        "missing_threads": missing["threads"],
        "missing_events": missing["events"]
    }

@app.post("/admin/memory-embedding-backfill")
async def admin_memory_embedding_backfill(limit: int = 20):
    """小批量回填Thread/Event embeddings。"""
    if not embedding_service.is_enabled():
        return {
            "status": "disabled",
            "message": "Embedding is not enabled or not configured.",
            "written_threads": 0,
            "written_events": 0
        }

    limit = max(1, min(limit, 50))
    items = db.list_items_for_embedding(limit=limit, model=config.EMBEDDING_MODEL)
    written_threads = 0
    written_events = 0

    for thread in items.get("threads", []):
        text = db.build_thread_embedding_text(thread)
        vector = await embedding_service.embed_text(text)
        if vector and db.update_thread_embedding(
            thread.get("event_key", ""),
            vector,
            config.EMBEDDING_MODEL
        ):
            written_threads += 1

    for event in items.get("events", []):
        text = db.build_event_embedding_text(event)
        vector = await embedding_service.embed_text(text)
        if vector and db.update_event_embedding(
            int(event.get("id")),
            vector,
            config.EMBEDDING_MODEL
        ):
            written_events += 1

    missing = db.count_missing_embeddings(config.EMBEDDING_MODEL)
    return {
        "status": "ok",
        "written_threads": written_threads,
        "written_events": written_events,
        "missing_threads": missing["threads"],
        "missing_events": missing["events"]
    }


# ========== 主程序入口 ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,
        log_level=config.LOG_LEVEL.lower()
    )
