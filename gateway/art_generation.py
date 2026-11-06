"""Mav图片创作：候选生成、Claude审稿、一次打回与最终发表。"""
import asyncio
import base64
import io
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from PIL import Image

from art_context import (
    parse_art_request,
    parse_art_review,
    review_model_for_main_model,
    size_for_aspect_ratio,
)
from chat_images import get_message_images, save_generated_message_image
from config import config
from db import db
from injector import injector_service
from internal_capabilities import build_internal_instructions
from router import router_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/art-jobs", tags=["art-jobs"])
ART_IMAGE_DIR = Path(config.ART_IMAGE_DIR)
ART_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
_running_tasks: set[asyncio.Task] = set()


ART_REVIEW_INSTRUCTION = """=== Mav内部创作审稿 ===
你正在审阅自己刚生成的一组候选画稿。你会真实看到每张候选图。
请根据岚的原始要求、你写下的创作prompt、must_keep与avoid，以及作品是否准确传达你想表达的关系和情绪来判断。
视觉判断由你自己完成；不要因为画面精致就忽略主体关系、动作、表情、构图、错误文字或明显畸形。

严格只输出一个JSON对象，不要Markdown，不要解释过程：
- 选中：{"decision":"select","selected_index":1,"caption":"发表作品时想对岚说的话","review_summary":"一句简短选择理由"}
- 打回：{"decision":"retry","revised_prompt":"完整修改后的prompt","rejection_summary":"上一批共同的主要问题"}
- 最终仍不发表：{"decision":"reject","caption":"自然告诉岚这次没有画到愿意交付的程度","rejection_summary":"一句主要问题"}

只有任务说明明确允许打回时才能使用retry；最终轮必须select或reject。候选编号从1开始。
结构化结果中与当前decision无关的字符串字段填空字符串，selected_index在非select时填0。"""


ART_PROMPT_RECOVERY_INSTRUCTION = """=== Mav画室：补上创作决定 ===
上一轮岚打开了画室，但主回复结束时没有留下可识别的创作决定。
这不是要求我必须创作，而是请我把刚才真实的决定表达完整。

严格只输出一个JSON对象，不要Markdown、标签、解释或聊天文字：
- 如果我确实决定创作：{"decision":"create","prompt":"完整的创作构想","aspect_ratio":"square","must_keep":[],"avoid":[]}
- 如果我并不想创作：{"decision":"decline"}

如果选择create，prompt应由我以创作者身份完成，而不是机械改写岚的话。"""


ART_REVIEW_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["select", "retry", "reject"]},
                "selected_index": {"type": "integer"},
                "caption": {"type": "string"},
                "review_summary": {"type": "string"},
                "revised_prompt": {"type": "string"},
                "rejection_summary": {"type": "string"},
            },
            "required": [
                "decision",
                "selected_index",
                "caption",
                "review_summary",
                "revised_prompt",
                "rejection_summary",
            ],
            "additionalProperties": False,
        },
    }
}


def _row_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with db._get_connection() as conn:
        return _row_dict(conn.execute("SELECT * FROM art_jobs WHERE id=?", (job_id,)).fetchone())


def _update_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    columns = [f"{key}=?" for key in values]
    with db._get_connection() as conn:
        conn.execute(
            f"UPDATE art_jobs SET {', '.join(columns)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*values.values(), job_id],
        )
        conn.commit()


def _candidate_rows(job_id: str, attempt: Optional[int] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM art_candidates WHERE job_id=?"
    params: List[Any] = [job_id]
    if attempt is not None:
        query += " AND attempt=?"
        params.append(attempt)
    query += " ORDER BY attempt ASC, candidate_index ASC"
    with db._get_connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _serialize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    selected_image = None
    selected_images: List[Dict[str, Any]] = []
    if row.get("status") == "completed":
        images = get_message_images(row["assistant_message_id"])
        selected_images = [image for image in images if image.get("vision_type") == "generated_art"]
        if not selected_images and images:
            selected_images = [images[0]]
        selected_image = selected_images[0] if selected_images else None
    try:
        request = json.loads(row.get("request_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        request = {}
    with db._get_connection() as conn:
        message = conn.execute(
            "SELECT content FROM messages WHERE id=?",
            (row["assistant_message_id"],),
        ).fetchone()
    return {
        "id": row["id"],
        "assistant_message_id": row["assistant_message_id"],
        "status": row["status"],
        "attempt": int(row.get("attempt") or 0),
        "max_attempts": int(config.ART_MAX_RETRIES) + 1,
        "caption": row.get("caption") or "",
        "error": row.get("error") or "",
        "mode": "native" if request.get("native") else "studio",
        "selected_image": selected_image,
        "selected_images": selected_images,
        "final_content": (message["content"] if message else "") or "",
        "updated_at": row.get("updated_at"),
    }


def attach_art_jobs_to_message_result(result: Dict[str, Any]) -> Dict[str, Any]:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    assistant_ids = [message.get("id") for message in messages if message.get("role") == "assistant"]
    if not assistant_ids:
        return result
    placeholders = ",".join("?" for _ in assistant_ids)
    with db._get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM art_jobs WHERE assistant_message_id IN ({placeholders})",
            assistant_ids,
        ).fetchall()]
    jobs = {row["assistant_message_id"]: _serialize_job(row) for row in rows}
    for message in messages:
        if message.get("id") in jobs:
            message["art_job"] = jobs[message["id"]]
    return result


def _request_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_request": str(context.get("user_request") or "")[:6000],
        "recent_context": str(context.get("recent_context") or "")[:6000],
        "assistant_response": str(context.get("assistant_response") or "")[:6000],
        "review_model": review_model_for_main_model(
            context.get("main_model"), config.ART_REVIEW_MODEL
        ),
    }


def _insert_art_job(request: Dict[str, Any], context: Dict[str, Any], status: str) -> str:
    job_id = uuid.uuid4().hex
    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO art_jobs (
                id, conversation_id, user_message_id, assistant_message_id, model,
                status, request_json, current_prompt, decision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                context["conversation_id"],
                context["user_message_id"],
                context["assistant_message_id"],
                config.ART_IMAGE_MODEL,
                status,
                json.dumps(request, ensure_ascii=False),
                request.get("prompt") or "",
                request.get("decision") or "",
            ),
        )
        conn.commit()
    return job_id


def create_art_job(
    raw_block: str,
    context: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    request, error = parse_art_request(raw_block)
    if error:
        logger.warning("mav_art parse failed reason=%s", error)
        return None, error, None
    required = ["conversation_id", "user_message_id", "assistant_message_id"]
    if any(not context.get(key) for key in required):
        return None, "missing_message_context", None
    request.update(_request_context(context))
    status = "declined" if request["decision"] == "decline" else "queued"
    job_id = _insert_art_job(request, context, status)
    if status == "queued":
        schedule_art_job(job_id)
    return job_id, None, status


def create_art_recovery_job(context: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    required = ["conversation_id", "user_message_id", "assistant_message_id"]
    if any(not context.get(key) for key in required):
        return None, "missing_message_context"
    request = {"decision": "pending", **_request_context(context)}
    job_id = _insert_art_job(request, context, "drafting")
    schedule_art_job(job_id)
    return job_id, None


def create_native_art_placeholder(
    context: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    required = ["conversation_id", "user_message_id", "assistant_message_id"]
    if any(not context.get(key) for key in required):
        return None, "missing_message_context"
    request = {
        "decision": "create",
        "native": True,
        "user_request": str(context.get("user_request") or "")[:6000],
        "prompts": [],
    }
    return _insert_art_job(request, context, "generating"), None


def complete_native_art_job(
    job_id: str,
    images: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Persist native Responses images, then attach them after the text message is saved."""
    usable = [item for item in images if isinstance(item, dict) and item.get("b64_json")][:3]
    if not usable:
        _update_job(job_id, status="failed", error="native_image_result_missing")
        return job_id, "native_image_result_missing", "failed"

    job = _get_job(job_id)
    try:
        if not job:
            raise RuntimeError("原生创作任务没有成功建立")
        request = json.loads(job.get("request_json") or "{}")
        request["prompts"] = [str(item.get("revised_prompt") or "")[:6000] for item in usable]
        _update_job(job_id, request_json=json.dumps(request, ensure_ascii=False))
        for index, item in enumerate(usable, start=1):
            from app_identity import names_in_template
            prompt = str(item.get("revised_prompt") or context.get("user_request") or names_in_template("Mav创作的图片"))
            _save_candidate(job_id, 1, index, prompt, base64.b64decode(item["b64_json"]))
        _update_job(job_id, attempt=1)
        schedule_native_art_job(job_id)
        return job_id, None, "generating"
    except Exception as exc:
        logger.exception("native art result save failed job_id=%s", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:1000])
        return job_id, str(exc), "failed"


def create_native_art_job(
    images: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    job_id, error = create_native_art_placeholder(context)
    if not job_id:
        return None, error, None
    return complete_native_art_job(job_id, images, context)


def fail_native_art_job(job_id: str, error: str) -> None:
    _update_job(job_id, status="failed", error=(error or "native_image_generation_failed")[:1000])


def schedule_art_job(job_id: str) -> None:
    task = asyncio.create_task(_run_art_job(job_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


def schedule_native_art_job(job_id: str) -> None:
    task = asyncio.create_task(_finalize_native_art_job(job_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


async def _wait_for_assistant_message(message_id: str, timeout_seconds: int = 45) -> bool:
    for _ in range(timeout_seconds * 2):
        with db._get_connection() as conn:
            exists = conn.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone()
        if exists:
            return True
        await asyncio.sleep(0.5)
    return False


async def _finalize_native_art_job(job_id: str) -> None:
    job = _get_job(job_id)
    if not job or job.get("status") in {"completed", "failed"}:
        return
    try:
        if not await _wait_for_assistant_message(job["assistant_message_id"]):
            raise RuntimeError("等待助手消息保存超时")
        candidates = _candidate_rows(job_id, 1)[:3]
        if not candidates:
            raise RuntimeError("原生生图没有返回可保存的图片")
        for index, candidate in enumerate(candidates):
            data = Path(candidate["file_path"]).read_bytes()
            from app_identity import names_in_template
            prompt = candidate.get("prompt") or names_in_template("Mav创作的图片")
            save_generated_message_image(
                job["assistant_message_id"],
                data,
                candidate.get("mime_type") or "image/jpeg",
                prompt,
                prompt,
                sort_order=index,
            )
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE art_candidates SET status='selected' WHERE job_id=?",
                (job_id,),
            )
            conn.commit()
        _update_job(
            job_id,
            status="completed",
            decision="select",
            selected_candidate_id=candidates[0]["id"],
        )
    except Exception as exc:
        logger.exception("native art publish failed job_id=%s", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:1000])


def _save_candidate(job_id: str, attempt: int, index: int, prompt: str, data: bytes) -> Dict[str, Any]:
    candidate_id = uuid.uuid4().hex
    file_path = ART_IMAGE_DIR / f"{candidate_id}.jpg"
    thumb_path = ART_IMAGE_DIR / f"{candidate_id}_review.jpg"
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        rgb.save(file_path, "JPEG", quality=92, optimize=True)
        review = rgb.copy()
        review.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        review.save(thumb_path, "JPEG", quality=86, optimize=True)
    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO art_candidates (
                id, job_id, attempt, candidate_index, file_path, thumb_path,
                mime_type, width, height, prompt
            ) VALUES (?, ?, ?, ?, ?, ?, 'image/jpeg', ?, ?, ?)
            """,
            (candidate_id, job_id, attempt, index, str(file_path), str(thumb_path), width, height, prompt),
        )
        conn.commit()
    return _candidate_rows(job_id, attempt)[-1]


def _generation_prompt(request: Dict[str, Any], prompt: str) -> str:
    parts = [prompt]
    if request.get("must_keep"):
        parts.append("必须保留：" + "；".join(request["must_keep"]))
    if request.get("avoid"):
        parts.append("明确避免：" + "；".join(request["avoid"]))
    return "\n\n".join(parts)


async def _generate_candidates(job: Dict[str, Any], request: Dict[str, Any], attempt: int, prompt: str) -> List[Dict[str, Any]]:
    if not config.ART_IMAGE_API_KEY:
        raise RuntimeError("ART_IMAGE_API_KEY未配置")
    count = max(1, min(int(config.ART_DRAFT_COUNT), 6))
    payload = {
        "model": config.ART_IMAGE_MODEL,
        "prompt": _generation_prompt(request, prompt),
        "n": count,
        "quality": "medium",
        "size": size_for_aspect_ratio(request.get("aspect_ratio", "square")),
        "output_format": "jpeg",
        "output_compression": 90,
    }
    base_url = config.ART_IMAGE_BASE_URL.rstrip("/")
    endpoint = f"{base_url}/images/generations" if base_url.endswith("/v1") else f"{base_url}/v1/images/generations"
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {config.ART_IMAGE_API_KEY}"},
            json=payload,
        )
        response.raise_for_status()
        items = response.json().get("data") or []
    if len(items) < count:
        raise RuntimeError(f"生图接口只返回了{len(items)}张候选图")
    rows = []
    for index, item in enumerate(items[:count], start=1):
        encoded = item.get("b64_json")
        if not encoded:
            raise RuntimeError("生图接口没有返回base64图片")
        rows.append(_save_candidate(job["id"], attempt, index, prompt, base64.b64decode(encoded)))
    return rows


def _review_task_text(request: Dict[str, Any], prompt: str, attempt: int, can_retry: bool, previous_summary: str) -> str:
    from app_identity import names_in_prompt
    return names_in_prompt(f"""【岚的本轮创作要求】
{request.get('user_request') or '未提供额外文字'}

【必要的局部对话】
{request.get('recent_context') or '无'}

【我确定的创作prompt】
{prompt}

【必须保留】
{json.dumps(request.get('must_keep') or [], ensure_ascii=False)}

【明确避免】
{json.dumps(request.get('avoid') or [], ensure_ascii=False)}

【当前轮次】
第{attempt}批。{'允许打回并重写prompt。' if can_retry else '这是最终批次，只能选择一张或拒绝发表。'}

【上一批打回结论】
{previous_summary or '无'}

下面依次是本批候选图，请真实看图后审稿。""", [
        request.get('user_request') or '', request.get('recent_context') or '',
        prompt, previous_summary, json.dumps(request.get('must_keep') or [], ensure_ascii=False),
        json.dumps(request.get('avoid') or [], ensure_ascii=False),
    ])


def _response_text(body: Dict[str, Any]) -> str:
    anthropic_text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    )
    if anthropic_text:
        return anthropic_text
    return "".join(
        choice.get("message", {}).get("content", "")
        for choice in body.get("choices", [])
        if isinstance(choice.get("message", {}).get("content"), str)
    )


async def _recover_art_request(request: Dict[str, Any]) -> Dict[str, Any]:
    from app_identity import names_in_prompt
    task = names_in_prompt(f"""【岚本轮打开画室时说的话】
{request.get('user_request') or '无'}

【必要的最近局部对话】
{request.get('recent_context') or '无'}

【我刚才已经说出的回复】
{request.get('assistant_response') or '无'}

请补上我刚才真实的创作决定。""", [
        request.get('user_request') or '', request.get('recent_context') or '',
        request.get('assistant_response') or '',
    ])
    parsed = {
        "format": "claude",
        "model": request.get("review_model") or config.ART_REVIEW_MODEL,
        "messages": [{"role": "user", "content": task}],
        "system": "",
        "stream": False,
        "max_tokens": 1200,
        "extra": {},
    }
    parsed = await injector_service.process_request(
        parsed,
        internal_instructions=build_internal_instructions(),
        dynamic_instructions=[ART_PROMPT_RECOVERY_INSTRUCTION],
        identity_only=True,
    )
    _, response = await router_service.route_request(parsed)
    recovered, error = parse_art_request(_response_text(response.json()))
    if error:
        raise RuntimeError(f"补写创作决定无法解析：{error}")
    return recovered


async def _review_candidates(
    request: Dict[str, Any],
    prompt: str,
    candidates: List[Dict[str, Any]],
    attempt: int,
    can_retry: bool,
    previous_summary: str,
) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = [{
        "type": "text",
        "text": _review_task_text(request, prompt, attempt, can_retry, previous_summary),
    }]
    for candidate in candidates:
        encoded = base64.b64encode(Path(candidate["thumb_path"]).read_bytes()).decode("utf-8")
        blocks.extend([
            {"type": "text", "text": f"候选图{candidate['candidate_index']}："},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
            },
        ])
    parsed = {
        "format": "claude",
        "model": request.get("review_model") or config.ART_REVIEW_MODEL,
        "messages": [{"role": "user", "content": blocks}],
        "system": "",
        "stream": False,
        "max_tokens": 1400,
        "output_config": ART_REVIEW_OUTPUT_CONFIG,
        "extra": {},
    }
    parsed = await injector_service.process_request(
        parsed,
        internal_instructions=build_internal_instructions(),
        dynamic_instructions=[ART_REVIEW_INSTRUCTION],
        identity_only=True,
    )
    last_error = "invalid_json"
    for review_attempt in range(2):
        request_to_send = parsed
        if review_attempt:
            request_to_send = {
                **parsed,
                "messages": [{
                    "role": "user",
                    "content": [
                        *blocks,
                        {
                            "type": "text",
                            "text": "上一次审稿输出没有形成可解析决定。请重新看同一批候选图，并严格按结构化格式给出最终决定。",
                        },
                    ],
                }],
            }
        _, response = await router_service.route_request(request_to_send)
        raw = _response_text(response.json())
        review, error = parse_art_review(raw, len(candidates), can_retry)
        if not error:
            return review
        last_error = error
        logger.warning(
            "art review parse failed attempt=%s reason=%s",
            review_attempt + 1,
            error,
        )
    raise RuntimeError(f"审稿决定无法解析：{last_error}")


def _append_assistant_caption(message_id: str, caption: str) -> str:
    caption = (caption or "").strip()
    with db._get_connection() as conn:
        row = conn.execute("SELECT content FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise RuntimeError("助手消息尚未保存")
        existing = (row["content"] or "").strip()
        final_content = existing
        if caption and caption not in existing:
            final_content = f"{existing}\n\n{caption}".strip()
        conn.execute("UPDATE messages SET content=? WHERE id=?", (final_content, message_id))
        conn.commit()
    return final_content


async def _run_art_job(job_id: str) -> None:
    job = _get_job(job_id)
    if not job or job.get("status") in {"completed", "rejected", "declined"}:
        return
    try:
        # 进程若在生成或审稿中重启，旧候选可能不完整；清空后从第一批重来，
        # 保证审稿时始终只看到本批固定数量的候选图。
        if job.get("status") in {"generating", "reviewing", "revising"}:
            stale_candidates = _candidate_rows(job_id)
            remove_art_image_files([
                path
                for candidate in stale_candidates
                for path in (candidate["file_path"], candidate["thumb_path"])
            ])
            with db._get_connection() as conn:
                conn.execute("DELETE FROM art_candidates WHERE job_id=?", (job_id,))
                conn.commit()
            _update_job(
                job_id,
                status="queued",
                attempt=0,
                decision="",
                current_prompt="",
                selected_candidate_id=None,
                caption="",
                error="",
            )
            job = _get_job(job_id) or job
        if not await _wait_for_assistant_message(job["assistant_message_id"]):
            raise RuntimeError("等待助手消息保存超时")
        request = json.loads(job["request_json"])

        if request.get("decision") == "pending" or not request.get("prompt"):
            _update_job(job_id, status="drafting", error="")
            recovered = await _recover_art_request(request)
            request.update(recovered)
            if request["decision"] == "decline":
                _update_job(
                    job_id,
                    status="declined",
                    decision="decline",
                    request_json=json.dumps(request, ensure_ascii=False),
                )
                return
            _update_job(
                job_id,
                status="queued",
                decision="create",
                request_json=json.dumps(request, ensure_ascii=False),
                current_prompt=request["prompt"],
            )

        prompt = job.get("current_prompt") or request["prompt"]
        previous_summary = ""
        max_attempts = max(1, int(config.ART_MAX_RETRIES) + 1)
        for attempt in range(1, max_attempts + 1):
            _update_job(job_id, status="generating", attempt=attempt, current_prompt=prompt, error="")
            candidates = await _generate_candidates(job, request, attempt, prompt)
            _update_job(job_id, status="reviewing")
            can_retry = attempt < max_attempts
            review = await _review_candidates(
                request, prompt, candidates, attempt, can_retry, previous_summary
            )
            if review["decision"] == "select":
                selected = candidates[review["selected_index"] - 1]
                data = Path(selected["file_path"]).read_bytes()
                from app_identity import names_in_template
                summary = review.get("review_summary") or request.get("user_request") or names_in_template("Mav创作的图片")
                save_generated_message_image(
                    job["assistant_message_id"],
                    data,
                    selected["mime_type"],
                    summary,
                    prompt,
                )
                caption = review.get("caption") or ""
                _append_assistant_caption(job["assistant_message_id"], caption)
                with db._get_connection() as conn:
                    conn.execute(
                        "UPDATE art_candidates SET status=CASE WHEN id=? THEN 'selected' ELSE 'rejected' END WHERE job_id=?",
                        (selected["id"], job_id),
                    )
                    conn.commit()
                _update_job(
                    job_id,
                    status="completed",
                    decision="select",
                    selected_candidate_id=selected["id"],
                    caption=caption,
                )
                return
            if review["decision"] == "retry" and can_retry:
                previous_summary = review.get("rejection_summary") or ""
                prompt = review["revised_prompt"]
                with db._get_connection() as conn:
                    conn.execute(
                        "UPDATE art_candidates SET status='rejected', review_summary=? WHERE job_id=? AND attempt=?",
                        (previous_summary, job_id, attempt),
                    )
                    conn.commit()
                _update_job(job_id, status="revising", decision="retry", current_prompt=prompt)
                continue
            caption = review.get("caption") or "这次没有画到我愿意交给你的程度。"
            _append_assistant_caption(job["assistant_message_id"], caption)
            _update_job(job_id, status="rejected", decision="reject", caption=caption)
            return
    except Exception as exc:
        logger.exception("art job failed job_id=%s", job_id)
        current = _get_job(job_id) or {}
        failed_status = "prompt_failed" if current.get("status") == "drafting" else "failed"
        _update_job(job_id, status=failed_status, error=str(exc)[:1000])


async def resume_art_jobs() -> None:
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM art_jobs WHERE status IN ('drafting','queued','generating','reviewing','revising')"
        ).fetchall()
    for row in rows:
        job = _get_job(row["id"])
        try:
            request = json.loads((job or {}).get("request_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            request = {}
        if request.get("native"):
            schedule_native_art_job(row["id"])
        else:
            schedule_art_job(row["id"])


def collect_art_image_paths(
    conversation_id: str,
    message_id: Optional[str] = None,
    after_message_id: Optional[str] = None,
    include_anchor: bool = False,
) -> List[str]:
    clauses = ["j.conversation_id=?"]
    params: List[Any] = [conversation_id]
    if message_id:
        clauses.append("(j.user_message_id=? OR j.assistant_message_id=?)")
        params.extend([message_id, message_id])
    if after_message_id:
        with db._get_connection() as conn:
            anchor = conn.execute(
                "SELECT created_at FROM messages WHERE id=? AND conversation_id=?",
                (after_message_id, conversation_id),
            ).fetchone()
        if not anchor:
            return []
        clauses.append(f"m.created_at {'>=' if include_anchor else '>'} ?")
        params.append(anchor["created_at"])
    with db._get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.file_path, c.thumb_path FROM art_candidates c
            JOIN art_jobs j ON j.id=c.job_id
            JOIN messages m ON m.id=j.user_message_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()
    return [path for row in rows for path in (row["file_path"], row["thumb_path"])]


def remove_art_image_files(paths: List[str]) -> None:
    for raw_path in paths:
        try:
            path = Path(raw_path)
            if path.parent.resolve() == ART_IMAGE_DIR.resolve():
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("art candidate cleanup failed path=%s: %s", raw_path, exc)


@router.get("/{job_id}")
async def get_art_job(job_id: str):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="创作任务不存在")
    return _serialize_job(job)
