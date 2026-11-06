import asyncio
import base64
import io
import json
import logging
import os
import re
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps

from config import config
from db import db
from portrait import load_portrait_injection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])
_comment_tasks: Dict[int, asyncio.Task] = {}

NOTE_DIR = Path(config.NOTE_IMAGE_DIR)
NOTE_DIR.mkdir(parents=True, exist_ok=True)


def _now_title() -> str:
    return datetime.now().strftime("%m/%d %H:%M")


def _parse_tags(raw: str = "") -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except Exception:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def _json_tags(tags: List[str]) -> str:
    seen = []
    for tag in tags:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return json.dumps(seen, ensure_ascii=False)


def _row_to_dict(row) -> Dict:
    item = dict(row)
    try:
        item["tags"] = json.loads(item.get("tags") or "[]")
    except Exception:
        item["tags"] = []
    return item


def _image_url(image_id: int, thumb: bool = False) -> str:
    suffix = "/thumb" if thumb else ""
    return f"/api/notes/images/{image_id}{suffix}"


def _normalize_image(data: bytes, max_width: int, quality: int) -> tuple[bytes, int, int]:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image)

    if image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "RGBA":
            background.paste(image, mask=image.split()[-1])
        else:
            background.paste(image)
        image = background
    elif image.mode == "L":
        image = image.convert("RGB")

    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue(), image.width, image.height


async def _save_image(note_id: int, upload: UploadFile, sort_order: int) -> Optional[int]:
    data = await upload.read()
    if not data:
        return None

    try:
        display_bytes, width, height = _normalize_image(data, max_width=1600, quality=88)
        thumb_bytes, _, _ = _normalize_image(data, max_width=640, quality=82)
    except Exception as exc:
        logger.warning("note image normalization failed: %s", exc)
        raise HTTPException(status_code=400, detail="图片无法读取")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    file_path = NOTE_DIR / f"note_{note_id}_{stamp}.jpg"
    thumb_path = NOTE_DIR / f"note_{note_id}_{stamp}_thumb.jpg"
    file_path.write_bytes(display_bytes)
    thumb_path.write_bytes(thumb_bytes)

    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO image_note_images
            (note_id, file_path, thumb_path, original_filename, mime_type, width, height, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                str(file_path),
                str(thumb_path),
                upload.filename,
                "image/jpeg",
                width,
                height,
                sort_order,
            ),
        )
        conn.commit()
        image_id = cursor.lastrowid

    asyncio.create_task(_ensure_ocr_text(image_id))
    return image_id


def _get_note(note_id: int) -> Optional[Dict]:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM image_notes WHERE id=?", (note_id,))
        row = cursor.fetchone()
        if not row:
            return None
        note = _row_to_dict(row)
        cursor.execute(
            "SELECT * FROM image_note_images WHERE note_id=? ORDER BY sort_order ASC, id ASC",
            (note_id,),
        )
        images = []
        for img in cursor.fetchall():
            item = dict(img)
            item["url"] = _image_url(item["id"])
            item["thumb_url"] = _image_url(item["id"], thumb=True)
            images.append(item)
        cursor.execute(
            "SELECT id, author, content, created_at FROM image_note_comments WHERE note_id=? ORDER BY created_at ASC, id ASC",
            (note_id,),
        )
        comments = [dict(item) for item in cursor.fetchall()]
        note["images"] = images
        note["comments"] = comments
        return note


def _list_notes(tag: str = "", limit: int = 80, offset: int = 0) -> List[Dict]:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM image_notes ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        notes = [_row_to_dict(row) for row in cursor.fetchall()]
        if tag:
            notes = [item for item in notes if tag in item.get("tags", [])]
        for note in notes:
            cursor.execute(
                "SELECT id, thumb_path, width, height FROM image_note_images WHERE note_id=? ORDER BY sort_order ASC, id ASC LIMIT 1",
                (note["id"],),
            )
            image = cursor.fetchone()
            note["cover"] = _image_url(image["id"], thumb=True) if image else None
            note["cover_width"] = image["width"] if image else None
            note["cover_height"] = image["height"] if image else None
        return notes


async def _call_vl(image_path: str, prompt: str, max_tokens: int = 900) -> str:
    if not config.VL_API_KEY:
        return ""
    data = Path(image_path).read_bytes()
    b64 = base64.b64encode(data).decode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{config.VL_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.VL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.VL_MODEL,
                    "max_tokens": max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("VL call failed: %s", exc)
        return ""


async def _ensure_ocr_text(image_id: int) -> str:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, file_path, ocr_text FROM image_note_images WHERE id=?", (image_id,))
        row = cursor.fetchone()
        if not row:
            return ""
        if row["ocr_text"]:
            return row["ocr_text"]
        file_path = row["file_path"]

    prompt = (
        "请只提取图片中真实可见、适合搜索的文字。\n"
        "不要描述版式、颜色、背景、人物、物体或界面。\n"
        "如果几乎没有可读文字，请直接返回空字符串。"
    )
    text = await _call_vl(file_path, prompt, max_tokens=700)
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE image_note_images SET ocr_text=? WHERE id=?", (text, image_id))
        conn.commit()
    return text


async def _ensure_vision_text(image_id: int) -> str:
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, file_path, vision_text FROM image_note_images WHERE id=?", (image_id,))
        row = cursor.fetchone()
        if not row:
            return ""
        if row["vision_text"]:
            return row["vision_text"]
        file_path = row["file_path"]

    from app_identity import names_in_template
    prompt = names_in_template(
        "这张图片会作为岚的一篇轻量笔记材料交给 Mav 评论。\n"
        "请完整描述图片内容，包含：可见文字、主体、关系、场景、梗图/截图语境、情绪或有趣之处。\n"
        "如果是文字截图，请保留关键原文；如果是照片或梗图，请说明画面和可能的含义。\n"
        "避免无意义的颜色编号、字体名、UI装饰细节。直接输出描述。"
    )
    text = await _call_vl(file_path, prompt, max_tokens=1200)
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE image_note_images SET vision_text=? WHERE id=?", (text, image_id))
        conn.commit()
    return text


async def _ensure_note_vision(note: Dict) -> List[str]:
    descriptions = []
    for image in note.get("images", []):
        text = await _ensure_vision_text(image["id"])
        if text:
            descriptions.append(text)
    return descriptions


def _build_note_system_prompt(extra: str = "") -> str:
    from app_identity import get_identity, names_in_template
    identity = get_identity()
    system_core = db.get_system_core() or ""
    portrait = load_portrait_injection(db)
    mav_notes = db.get_mav_notes(limit=5)
    notes_text = "\n".join(item["content"] for item in mav_notes) if mav_notes else ""
    snapshot = db.get_profile("short_term_snapshot") or ""

    parts = []
    if system_core:
        parts.append(system_core)
    parts.append(f"当前称呼：助手是{identity['assistant_name']}，用户是{identity['user_name']}。旧记录中的 Mav、Maverick、岚分别指向同一位助手与用户。")
    if portrait:
        parts.append(portrait)
    if notes_text:
        parts.append(f"【{identity['assistant_name']} notes】\n{notes_text}")
    if snapshot:
        parts.append(f"【Short term snapshot】\n{snapshot}")
    parts.append(names_in_template(
        "这是 M-Home 的轻量笔记模块。岚会保存外面看到的截图、图片、经验、推书推剧、梗图或片段，"
        "不是正式知识库。你要像平时陪她一起看东西那样回应：自然、具体、有一点主观感受，"
        "可以指出你觉得有用/有趣/值得留意的地方，但不要写成干巴巴的总结。"
    ))
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def _build_note_user_payload(note: Dict, image_descriptions: List[str], mode: str, user_reply: str = "") -> str:
    from app_identity import get_identity
    identity = get_identity()
    tags = "、".join(note.get("tags", [])) or "无"
    images = "\n\n".join(
        f"【图片{i + 1}描述】\n{text}" for i, text in enumerate(image_descriptions) if text
    ) or "无图片描述"
    comments = "\n".join(
        f"{identity['assistant_name'] if c['author'] == 'mav' else identity['user_name']}：{c['content']}"
        for c in note.get("comments", [])
    ) or "暂无"

    base = (
        f"【笔记标题】\n{note.get('title') or ''}\n\n"
        f"【标签】\n{tags}\n\n"
        f"【正文】\n{note.get('body') or '（空）'}\n\n"
        f"{images}\n\n"
        f"【已有评论】\n{comments}"
    )
    if mode == "reply":
        base += f"\n\n【{identity['user_name']}刚刚回复】\n{user_reply}"
    elif mode == "supplement":
        base += "\n\n请基于这篇笔记当前的全部内容和已有评论，补充一条新的回应，不要推翻前文。"
    else:
        base += "\n\n请给这篇笔记留一条完整的新评论。"
    return base


async def _stream_main_model(messages: List[Dict], system_prompt: str, max_tokens: int = 700):
    main_cfg = config.get_main_config()
    provider = (main_cfg.get("provider") or "zenmux").lower()
    if provider == "anthropic":
        url = f"{main_cfg['base_url'].rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": main_cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": main_cfg["model"],
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
            "stream": True,
        }
    else:
        base = main_cfg["base_url"].rstrip("/")
        url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {main_cfg['api_key']}",
            "Content-Type": "application/json",
        }
        body = {
            "model": main_cfg["model"],
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
        }

    # Note comments run as background jobs. A slow first token must not inherit
    # the old browser-facing 120-second deadline and discard a paid response.
    timeout = httpx.Timeout(300.0, connect=20.0, read=300.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await response.aread()
                detail = exc.response.text[:500] if exc.response is not None else ""
                raise RuntimeError(
                    f"Main model request failed: {exc.response.status_code} {detail}"
                ) from exc
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                    if provider == "anthropic":
                        delta = data.get("delta", {})
                        text = delta.get("text", "")
                    else:
                        text = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if text:
                        yield text
                except Exception:
                    continue


@router.get("")
async def list_notes(tag: str = "", limit: int = 80, offset: int = 0):
    return {"notes": _list_notes(tag=tag, limit=limit, offset=offset)}


@router.get("/tags")
async def list_tags():
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM image_notes ORDER BY updated_at DESC")
        tags = []
        for row in cursor.fetchall():
            for tag in _parse_tags(row["tags"]):
                if tag not in tags:
                    tags.append(tag)
    return {"tags": tags}


@router.post("")
async def create_note(
    title: str = Form(""),
    body: str = Form(""),
    tags: str = Form("[]"),
    images: List[UploadFile] = File(default=[]),
):
    clean_tags = _parse_tags(tags)
    note_title = title.strip() or _now_title()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO image_notes (title, body, tags) VALUES (?, ?, ?)",
            (note_title, body.strip(), _json_tags(clean_tags)),
        )
        conn.commit()
        note_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO image_note_updates (note_id, action, summary) VALUES (?, ?, ?)",
            (note_id, "create", "创建笔记"),
        )
        conn.commit()

    for idx, upload in enumerate(images):
        await _save_image(note_id, upload, idx)
    return _get_note(note_id)


@router.get("/search")
async def search_notes(q: str):
    query = (q or "").strip()
    if not query:
        return {"results": []}

    def match_sentence(text: str) -> Optional[str]:
        if not text:
            return None
        compact = re.sub(r"\s+", " ", text)
        lowered = compact.lower()
        pos = lowered.find(query.lower())
        if pos < 0:
            return None
        start = max(0, compact.rfind("。", 0, pos), compact.rfind("！", 0, pos), compact.rfind("？", 0, pos), compact.rfind("\n", 0, pos))
        if start > 0:
            start += 1
        end_candidates = [i for i in [compact.find("。", pos), compact.find("！", pos), compact.find("？", pos), compact.find("\n", pos)] if i >= 0]
        end = min(end_candidates) + 1 if end_candidates else min(len(compact), pos + 80)
        snippet = compact[start:end].strip()
        if len(snippet) > 120:
            local = compact[max(0, pos - 45): min(len(compact), pos + 75)].strip()
            snippet = local
        return f"...{snippet}..."

    results = []
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM image_notes ORDER BY updated_at DESC, id DESC")
        notes = [_row_to_dict(row) for row in cursor.fetchall()]
        for note in notes:
            cursor.execute("SELECT ocr_text FROM image_note_images WHERE note_id=?", (note["id"],))
            ocr_text = "\n".join(row["ocr_text"] or "" for row in cursor.fetchall())
            cursor.execute("SELECT content FROM image_note_comments WHERE note_id=?", (note["id"],))
            comment_text = "\n".join(row["content"] or "" for row in cursor.fetchall())
            fields = [note.get("title", ""), note.get("body", ""), ocr_text, comment_text]
            snippet = next((match_sentence(text) for text in fields if match_sentence(text)), None)
            if snippet:
                cursor.execute(
                    "SELECT id FROM image_note_images WHERE note_id=? ORDER BY sort_order ASC, id ASC LIMIT 1",
                    (note["id"],),
                )
                image = cursor.fetchone()
                results.append({
                    "id": note["id"],
                    "title": note["title"],
                    "created_at": note["created_at"],
                    "snippet": snippet,
                    "cover": _image_url(image["id"], thumb=True) if image else None,
                })
    return {"results": results}


@router.get("/{note_id}")
async def get_note(note_id: int):
    note = _get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.post("/{note_id}/update")
async def update_note(
    note_id: int,
    title: str = Form(""),
    body: str = Form(""),
    tags: str = Form("[]"),
    images: List[UploadFile] = File(default=[]),
):
    note = _get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    clean_tags = _parse_tags(tags)
    note_title = title.strip() or note.get("title") or _now_title()
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE image_notes SET title=?, body=?, tags=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (note_title, body.strip(), _json_tags(clean_tags), note_id),
        )
        cursor.execute("SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM image_note_images WHERE note_id=?", (note_id,))
        max_order_value = cursor.fetchone()["max_order"]
        max_order = max_order_value if max_order_value is not None else -1
        cursor.execute(
            "INSERT INTO image_note_updates (note_id, action, summary) VALUES (?, ?, ?)",
            (note_id, "update", f"更新笔记；新增图片 {len(images)} 张"),
        )
        conn.commit()

    for idx, upload in enumerate(images):
        await _save_image(note_id, upload, max_order + idx + 1)
    return _get_note(note_id)


@router.delete("/{note_id}")
async def delete_note(note_id: int):
    note = _get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM image_notes WHERE id=?", (note_id,))
        conn.commit()
    return {"status": "success"}


@router.get("/images/{image_id}")
async def get_image(image_id: int):
    return _serve_image(image_id, thumb=False)


@router.get("/images/{image_id}/thumb")
async def get_thumb(image_id: int):
    return _serve_image(image_id, thumb=True)


def _serve_image(image_id: int, thumb: bool):
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, thumb_path FROM image_note_images WHERE id=?", (image_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="图片不存在")
    path = Path(row["thumb_path"] if thumb else row["file_path"]).resolve()
    root = NOTE_DIR.resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=403, detail="路径不可访问")
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(path, media_type="image/jpeg")


def _get_comment_job(note_id: int, job_id: Optional[int] = None) -> Optional[Dict]:
    with closing(db._get_connection()) as conn:
        if job_id is None:
            row = conn.execute(
                "SELECT * FROM image_note_comment_jobs WHERE note_id=? ORDER BY id DESC LIMIT 1",
                (note_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM image_note_comment_jobs WHERE note_id=? AND id=?",
                (note_id, job_id),
            ).fetchone()
    if not row:
        return None
    return {key: row[key] for key in (
        "id", "note_id", "mode", "user_reply", "status", "draft_text", "comment_id", "error", "created_at", "updated_at"
    )}


def interrupt_stale_comment_jobs():
    """A restart must not silently repeat a possibly billed model request."""
    with closing(db._get_connection()) as conn:
        conn.execute(
            """UPDATE image_note_comment_jobs
               SET status='interrupted', error='服务重启中断了创作，请确认后手动重试',
                   updated_at=CURRENT_TIMESTAMP
               WHERE status IN ('pending', 'running')"""
        )
        conn.commit()


def _update_comment_progress(job_id: int, text: str):
    with closing(db._get_connection()) as conn:
        conn.execute(
            """UPDATE image_note_comment_jobs SET status='running', draft_text=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (text, job_id),
        )
        conn.commit()


def _fail_comment_job(job_id: int, status: str, error: str):
    with closing(db._get_connection()) as conn:
        conn.execute(
            """UPDATE image_note_comment_jobs SET status=?, error=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('pending', 'running')""",
            (status, error, job_id),
        )
        conn.commit()


async def _run_comment_job(job_id: int, note_id: int, mode: str, user_reply: str):
    full = ""
    try:
        _update_comment_progress(job_id, full)
        note = _get_note(note_id)
        if not note:
            raise RuntimeError("笔记已删除")
        descriptions = await _ensure_note_vision(note)
        system_prompt = _build_note_system_prompt()
        user_payload = _build_note_user_payload(note, descriptions, mode=mode, user_reply=user_reply)
        messages = [{"role": "user", "content": user_payload}]
        last_saved = time.monotonic()
        async for text in _stream_main_model(messages, system_prompt):
            full += text
            if time.monotonic() - last_saved >= 0.5:
                _update_comment_progress(job_id, full)
                last_saved = time.monotonic()
        full = full.strip()
        if not full:
            raise RuntimeError("模型没有返回可保存的评论")
        _update_comment_progress(job_id, full)
        with closing(db._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            if mode == "regenerate":
                cursor.execute("DELETE FROM image_note_comments WHERE note_id=?", (note_id,))
                cursor.execute(
                    "INSERT INTO image_note_updates (note_id, action, summary) VALUES (?, ?, ?)",
                    (note_id, "regenerate_comment", "重新生成Mav评论"),
                )
            elif mode == "reply":
                cursor.execute(
                    "INSERT INTO image_note_comments (note_id, author, content) VALUES (?, ?, ?)",
                    (note_id, "user", user_reply),
                )
            cursor.execute(
                "INSERT INTO image_note_comments (note_id, author, content) VALUES (?, ?, ?)",
                (note_id, "mav", full),
            )
            comment_id = cursor.lastrowid
            cursor.execute(
                """UPDATE image_note_comment_jobs SET status='completed', draft_text=?,
                   comment_id=?, error='', updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (full, comment_id, job_id),
            )
            conn.commit()
        logger.info("note comment completed job_id=%s note_id=%s", job_id, note_id)
    except asyncio.CancelledError:
        _fail_comment_job(job_id, "interrupted", "服务中断了创作，请确认后手动重试")
        raise
    except httpx.ReadTimeout:
        logger.exception("note comment upstream read timeout job_id=%s note_id=%s", job_id, note_id)
        _fail_comment_job(
            job_id,
            "failed",
            "等待模型回复超时；这次请求可能已计费，请先查看服务商记录，再决定是否重试",
        )
    except Exception:
        logger.exception("note comment failed job_id=%s note_id=%s", job_id, note_id)
        _fail_comment_job(job_id, "failed", "评论生成失败，请检查服务后重试")


def _start_comment_task(job_id: int, note_id: int, mode: str, user_reply: str):
    task = asyncio.create_task(_run_comment_job(job_id, note_id, mode, user_reply))
    _comment_tasks[job_id] = task

    def cleanup(done: asyncio.Task):
        if _comment_tasks.get(job_id) is done:
            _comment_tasks.pop(job_id, None)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(cleanup)


@router.post("/{note_id}/comment-jobs")
async def create_mav_comment_job(note_id: int, request: Request):
    data = await request.json()
    mode = data.get("mode", "new")
    user_reply = (data.get("user_reply") or "").strip()
    request_id = str(data.get("request_id") or uuid4().hex)
    if mode not in {"new", "regenerate", "supplement", "reply"}:
        raise HTTPException(status_code=400, detail="无效的评论模式")
    if mode == "reply" and not user_reply:
        raise HTTPException(status_code=400, detail="回复不能为空")
    if not 8 <= len(request_id) <= 128:
        raise HTTPException(status_code=400, detail="无效的请求编号")
    if not _get_note(note_id):
        raise HTTPException(status_code=404, detail="笔记不存在")

    with closing(db._get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        existing = cursor.execute(
            "SELECT id, note_id, mode, user_reply FROM image_note_comment_jobs WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if existing and (existing["note_id"] != note_id or existing["mode"] != mode or existing["user_reply"] != user_reply):
            raise HTTPException(status_code=409, detail="请求编号已用于另一条评论")
        if not existing:
            existing = cursor.execute(
                """SELECT id FROM image_note_comment_jobs
                   WHERE note_id=? AND status IN ('pending', 'running') ORDER BY id DESC LIMIT 1""",
                (note_id,),
            ).fetchone()
        if existing:
            job_id = existing["id"]
        else:
            cursor.execute(
                """INSERT INTO image_note_comment_jobs
                   (request_id, note_id, mode, user_reply) VALUES (?, ?, ?, ?)""",
                (request_id, note_id, mode, user_reply),
            )
            job_id = cursor.lastrowid
        conn.commit()
    if not existing:
        _start_comment_task(job_id, note_id, mode, user_reply)
    return _get_comment_job(note_id, job_id)


@router.post("/{note_id}/comments")
async def create_mav_comment(note_id: int, request: Request):
    """Keep older frontends usable while they are replaced by job polling."""
    job = await create_mav_comment_job(note_id, request)

    async def stream_job():
        sent_length = 0
        while True:
            current = _get_comment_job(note_id, job["id"])
            if not current:
                yield f"data: {json.dumps({'type': 'error', 'message': '笔记已删除'}, ensure_ascii=False)}\n\n"
                return
            draft = current["draft_text"] or ""
            if len(draft) > sent_length:
                yield f"data: {json.dumps({'type': 'text', 'text': draft[sent_length:]}, ensure_ascii=False)}\n\n"
                sent_length = len(draft)
            if current["status"] == "completed":
                yield f"data: {json.dumps({'type': 'done', 'comment_id': current['comment_id']}, ensure_ascii=False)}\n\n"
                return
            if current["status"] in {"failed", "interrupted"}:
                yield f"data: {json.dumps({'type': 'error', 'message': current['error']}, ensure_ascii=False)}\n\n"
                return
            yield ": waiting\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream_job(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{note_id}/comment-job")
async def get_latest_mav_comment_job(note_id: int):
    if not _get_note(note_id):
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"job": _get_comment_job(note_id)}


@router.get("/{note_id}/comment-jobs/{job_id}")
async def get_mav_comment_job(note_id: int, job_id: int):
    job = _get_comment_job(note_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    return job
