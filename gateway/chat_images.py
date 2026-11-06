"""主对话图片：多图文件存储、VL描述、历史上下文注入与查看接口。"""
import asyncio
import base64
import io
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageOps

from config import config
from db import db
from chat_image_context import build_image_context, context_layer_for_rounds, parse_vl_result


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat-images", tags=["chat-images"])

CHAT_IMAGE_DIR = Path(config.CHAT_IMAGE_DIR)
CHAT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
MAX_IMAGES_PER_MESSAGE = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DETAIL_CONTEXT_LIMIT = 1600
SUMMARY_CONTEXT_LIMIT = 700

_description_locks: Dict[str, asyncio.Lock] = {}
_vl_semaphore = asyncio.Semaphore(2)


VISION_PROMPT = """请读取这张图片，并严格只输出一个JSON对象，不要Markdown代码块，不要解释。

输出格式：
{
  "type": "photo | chat_screenshot | code_screenshot | document | product | meme | other",
  "summary": "适合后续对话继续理解图片的简短摘要",
  "detail": "更完整的图片描述"
}

信息必须按重要性从高到低排列：
1. 先判断图片类型。
2. 先描述最重要的主体，以及主体正在做什么、处于什么状态。
3. 再描述主体之间的关系、互动、空间位置或事件顺序。
4. 再提取会影响理解或回应的重要文字。
5. 最后才补充环境、氛围和次要背景。

不同图片类型的优先规则：
- 聊天截图：先准确区分说话方，再按顺序提取关键对话和语气。
- 代码或报错截图：先提取报错、文件位置和关键代码，再说明界面结构。
- 文档截图：先标题、结论和关键段落，再补充次要正文。
- 普通照片：先主体、动作、表情和关系，再补环境与氛围。

长度纪律：
- summary控制在120至220个中文字符内，必须包含主体和关键事件。
- 普通图片detail控制在600个中文字符内；文字密集截图最多1000个中文字符。
- 接近长度限制时停止补充次要背景，不得把关键主体、动作或重要文字放在结尾。
- 不要为了写满长度而添加无关颜色、字体、装饰或推测。
- 看不清的内容明确写“无法辨认”，不要编造。"""


def _image_url(image_id: str, thumb: bool = False) -> str:
    suffix = "/thumb" if thumb else ""
    return f"/api/chat-images/{image_id}{suffix}"


def _serialize_image(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "sort_order": row.get("sort_order", 0),
        "mime_type": row.get("mime_type", "image/jpeg"),
        "width": row.get("width"),
        "height": row.get("height"),
        "vision_type": row.get("vision_type", ""),
        "vision_summary": row.get("vision_summary", ""),
        "vision_status": row.get("vision_status", "pending"),
        "url": _image_url(row["id"]),
        "thumb_url": _image_url(row["id"], thumb=True),
    }


def _decode_image_payload(raw: Dict[str, Any]) -> tuple[bytes, str]:
    encoded = raw.get("base64", "")
    mime_type = raw.get("mimeType") or raw.get("mime_type") or "image/jpeg"
    if not encoded:
        raise HTTPException(status_code=400, detail="图片内容为空")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片编码无效") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="单张图片不能超过8MB")
    return data, mime_type


def _resize(image: Image.Image, max_edge: int) -> Image.Image:
    if max(image.width, image.height) <= max_edge:
        return image.copy()
    ratio = max_edge / max(image.width, image.height)
    return image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode == "L":
        return image.convert("RGB")
    background = Image.new("RGB", image.size, (255, 255, 255))
    if "A" in image.getbands():
        background.paste(image, mask=image.getchannel("A"))
    else:
        background.paste(image.convert("RGB"))
    return background


def _normalize_image(data: bytes, requested_mime: str) -> tuple[bytes, bytes, str, str, int, int]:
    try:
        source = Image.open(io.BytesIO(data))
        source.seek(0)
        source = ImageOps.exif_transpose(source)
        if source.width * source.height > 40_000_000:
            raise ValueError("image dimensions too large")
        display = _resize(source, 2560)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片无法读取") from exc

    preserve_png = requested_mime == "image/png"
    display_out = io.BytesIO()
    if preserve_png:
        display.save(display_out, format="PNG", optimize=True)
        display_mime = "image/png"
        extension = "png"
    else:
        display = _to_rgb(display)
        display.save(display_out, format="JPEG", quality=90, optimize=True)
        display_mime = "image/jpeg"
        extension = "jpg"

    thumb = _to_rgb(_resize(source, 520))
    thumb_out = io.BytesIO()
    thumb.save(thumb_out, format="JPEG", quality=84, optimize=True)
    return (
        display_out.getvalue(),
        thumb_out.getvalue(),
        display_mime,
        extension,
        display.width,
        display.height,
    )


def prepare_message_images(message_id: str, raw_images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """先完成图片解码与文件写入；数据库记录由消息事务统一提交。"""
    if not raw_images:
        return []
    if len(raw_images) > MAX_IMAGES_PER_MESSAGE:
        raise HTTPException(status_code=400, detail="每条消息最多发送6张图片")

    prepared: List[Dict[str, Any]] = []
    created_paths: List[Path] = []
    try:
        for index, raw in enumerate(raw_images):
            data, requested_mime = _decode_image_payload(raw)
            display_bytes, thumb_bytes, mime_type, extension, width, height = _normalize_image(
                data,
                requested_mime,
            )
            image_id = uuid.uuid4().hex
            file_path = CHAT_IMAGE_DIR / f"{image_id}.{extension}"
            thumb_path = CHAT_IMAGE_DIR / f"{image_id}_thumb.jpg"
            file_path.write_bytes(display_bytes)
            created_paths.append(file_path)
            thumb_path.write_bytes(thumb_bytes)
            created_paths.append(thumb_path)

            prepared.append({
                "id": image_id,
                "message_id": message_id,
                "sort_order": index,
                "file_path": str(file_path),
                "thumb_path": str(thumb_path),
                "mime_type": mime_type,
                "width": width,
                "height": height,
                "vision_status": "pending",
            })
        return prepared
    except Exception:
        remove_image_files(created_paths)
        raise


def serialize_message_images(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_serialize_image(image) for image in images]


def save_generated_message_image(
    message_id: str,
    data: bytes,
    mime_type: str,
    summary: str,
    detail: str,
    sort_order: int = 0,
) -> Dict[str, Any]:
    """把最终生成作品作为正式助手消息图片保存。"""
    display_bytes, thumb_bytes, normalized_mime, extension, width, height = _normalize_image(
        data,
        mime_type,
    )
    image_id = uuid.uuid4().hex
    file_path = CHAT_IMAGE_DIR / f"{image_id}.{extension}"
    thumb_path = CHAT_IMAGE_DIR / f"{image_id}_thumb.jpg"
    try:
        file_path.write_bytes(display_bytes)
        thumb_path.write_bytes(thumb_bytes)
        with db._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO message_images (
                    id, message_id, sort_order, file_path, thumb_path, mime_type,
                    width, height, vision_type, vision_summary, vision_detail, vision_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated_art', ?, ?, 'ready')
                """,
                (
                    image_id,
                    message_id,
                    sort_order,
                    str(file_path),
                    str(thumb_path),
                    normalized_mime,
                    width,
                    height,
                    summary[:220],
                    detail[:1000],
                ),
            )
            conn.commit()
    except Exception:
        remove_image_files([file_path, thumb_path])
        raise
    return _serialize_image({
        "id": image_id,
        "message_id": message_id,
        "sort_order": sort_order,
        "mime_type": normalized_mime,
        "width": width,
        "height": height,
        "vision_type": "generated_art",
        "vision_summary": summary[:220],
        "vision_status": "ready",
    })


def remove_image_files(paths: List[Path | str]) -> None:
    for raw_path in paths:
        try:
            path = Path(raw_path)
            if path.parent.resolve() == CHAT_IMAGE_DIR.resolve():
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("chat image file cleanup failed path=%s: %s", raw_path, exc)


def collect_message_image_paths(
    conversation_id: str,
    message_id: Optional[str] = None,
    after_message_id: Optional[str] = None,
    include_anchor: bool = False,
) -> List[str]:
    clauses = ["m.conversation_id = ?"]
    params: List[Any] = [conversation_id]
    if message_id:
        clauses.append("m.id = ?")
        params.append(message_id)
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
            SELECT mi.file_path, mi.thumb_path
            FROM message_images mi
            JOIN messages m ON m.id = mi.message_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()
    return [path for row in rows for path in (row["file_path"], row["thumb_path"])]


def get_message_images(message_id: str, include_paths: bool = False) -> List[Dict[str, Any]]:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM message_images WHERE message_id=? ORDER BY sort_order ASC, created_at ASC",
            (message_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    return rows if include_paths else [_serialize_image(row) for row in rows]


def attach_images_to_message_result(result: Dict[str, Any]) -> Dict[str, Any]:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    message_ids = [message.get("id") for message in messages if message.get("id")]
    if not message_ids:
        return result
    placeholders = ",".join("?" for _ in message_ids)
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM message_images WHERE message_id IN ({placeholders}) ORDER BY sort_order ASC",
            message_ids,
        )
        rows = [dict(row) for row in cursor.fetchall()]
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(_serialize_image(row))
    for message in messages:
        message["images"] = grouped.get(message.get("id"), [])
    return result


async def _describe_image(row: Dict[str, Any]) -> None:
    image_id = row["id"]
    try:
        data = Path(row["file_path"]).read_bytes()
        encoded = base64.b64encode(data).decode("utf-8")
        async with _vl_semaphore:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{config.VL_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.VL_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.VL_MODEL,
                        "max_tokens": 900,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{row['mime_type']};base64,{encoded}"},
                                },
                                {"type": "text", "text": VISION_PROMPT},
                            ],
                        }],
                    },
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"].strip()
        parsed = parse_vl_result(raw)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE message_images
                SET vision_type=?, vision_summary=?, vision_detail=?, vision_status='ready'
                WHERE id=?
                """,
                (parsed["type"], parsed["summary"], parsed["detail"], image_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("chat image VL failed image_id=%s: %s", image_id, exc)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE message_images SET vision_status='failed' WHERE id=?",
                (image_id,),
            )
            conn.commit()


async def ensure_message_image_descriptions(message_id: str) -> List[Dict[str, Any]]:
    lock = _description_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
        images = get_message_images(message_id, include_paths=True)
        pending = [image for image in images if image.get("vision_status") != "ready"]
        if pending and config.VL_API_KEY:
            await asyncio.gather(*[_describe_image(image) for image in pending])
        return get_message_images(message_id, include_paths=True)


def schedule_message_image_descriptions(message_id: str) -> None:
    async def runner():
        try:
            await ensure_message_image_descriptions(message_id)
        except Exception:
            logger.exception("background chat image description failed message_id=%s", message_id)

    asyncio.create_task(runner())


async def enrich_messages_with_image_context(
    parsed_request: Dict[str, Any],
    current_message_id: str,
    direct_send: bool,
) -> Dict[str, Any]:
    """把历史图片描述注入文字上下文；当前直传图片仍使用原图blocks。"""
    messages = parsed_request.get("messages", [])
    user_positions = [index for index, message in enumerate(messages) if message.get("role") == "user"]
    current_position = user_positions[-1] if user_positions else -1

    historical_ids = []
    for position_index, index in enumerate(user_positions):
        rounds_since = len(user_positions) - position_index - 1
        if context_layer_for_rounds(rounds_since) == "none":
            continue
        message_id = messages[index].get("message_id")
        if message_id and message_id != current_message_id and get_message_images(message_id, include_paths=True):
            historical_ids.append(message_id)
    if historical_ids:
        await asyncio.gather(*[ensure_message_image_descriptions(message_id) for message_id in historical_ids])

    if current_message_id and not direct_send and get_message_images(current_message_id, include_paths=True):
        await ensure_message_image_descriptions(current_message_id)

    for position_index, message_index in enumerate(user_positions):
        message = messages[message_index]
        message_id = message.get("message_id")
        rounds_since = len(user_positions) - position_index - 1
        layer = context_layer_for_rounds(rounds_since)
        if layer == "none":
            message.pop("message_id", None)
            continue
        images = get_message_images(message_id, include_paths=True) if message_id else []
        if not images:
            message.pop("message_id", None)
            continue

        if message_id == current_message_id and direct_send:
            original = message.get("content", "")
            blocks = []
            for image_index, image in enumerate(images, start=1):
                encoded = base64.b64encode(Path(image["file_path"]).read_bytes()).decode("utf-8")
                blocks.append({"type": "text", "text": f"图片{image_index}："})
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image["mime_type"],
                        "data": encoded,
                    },
                })
            blocks.append({
                "type": "text",
                "text": original.strip() or "请自然回应我发送的这些图片。",
            })
            message["content"] = blocks
        else:
            use_detail = layer == "detail"
            limit = DETAIL_CONTEXT_LIMIT if use_detail else SUMMARY_CONTEXT_LIMIT
            context = build_image_context(images, use_detail=use_detail, limit=limit)
            label = "本轮图片内容" if message_id == current_message_id else "先前图片上下文"
            from app_identity import names_in_template
            wrapper = (
                f"【{label}】\n"
                + names_in_template("以下内容由视觉模型读取图片后生成，不是岚手写的原文。\n")
                + f"{context}"
            )
            original = message.get("content", "")
            message["content"] = f"{wrapper}\n\n{names_in_template('【岚随图片附带的文字】')}\n{original}" if original else wrapper
        message.pop("message_id", None)

    # Mav自己生成并发表的作品也需要在后续对话中留下可理解的文字上下文。
    for message_index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        message_id = message.get("message_id")
        if not message_id:
            continue
        images = get_message_images(message_id, include_paths=True)
        generated = [image for image in images if image.get("vision_type") == "generated_art"]
        if not generated:
            continue
        rounds_since = sum(1 for position in user_positions if position > message_index)
        layer = context_layer_for_rounds(rounds_since)
        if layer == "none":
            continue
        use_detail = layer == "detail"
        limit = DETAIL_CONTEXT_LIMIT if use_detail else SUMMARY_CONTEXT_LIMIT
        context = build_image_context(generated, use_detail=use_detail, limit=limit)
        wrapper = (
            "【我先前创作并发出的图片】\n"
            "以下是系统保存的作品描述，用于让我记得自己刚才画了什么。\n"
            f"{context}"
        )
        original = message.get("content", "")
        message["content"] = f"{original}\n\n{wrapper}" if original else wrapper

    for message in messages:
        message.pop("message_id", None)
    parsed_request["messages"] = messages
    if current_message_id and direct_send:
        schedule_message_image_descriptions(current_message_id)
    return parsed_request


@router.get("/{image_id}")
async def get_chat_image(image_id: str):
    images = get_message_images_by_id(image_id)
    if not images:
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(images["file_path"], media_type=images["mime_type"])


@router.get("/{image_id}/thumb")
async def get_chat_image_thumb(image_id: str):
    image = get_message_images_by_id(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(image["thumb_path"], media_type="image/jpeg")


def get_message_images_by_id(image_id: str) -> Optional[Dict[str, Any]]:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM message_images WHERE id=?", (image_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
