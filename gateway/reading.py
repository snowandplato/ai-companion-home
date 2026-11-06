"""
阅读模块 - reading.py
处理epub上传、解析、Mav批注生成、用户批注回复
"""
import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from config import config
from db import db
from portrait import load_portrait_injection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reading", tags=["reading"])

EPUB_DIR = config.EPUB_DIR
os.makedirs(EPUB_DIR, exist_ok=True)

# 当前正在处理的book_id（限制同时只处理一本）
_processing_lock = asyncio.Lock()
_current_processing: Optional[int] = None

MAX_CHUNK_CHARS = 12000  # 单次发给Claude的最大字符数


# ========== epub解析 ==========

def parse_epub(file_path: str) -> Dict[str, Any]:
    """
    解析epub文件，返回书籍元数据和章节列表
    每章包含: title, content_text, content_images(图片base64+位置)
    """
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(file_path)

    # 元数据
    title = "未知书名"
    author = "未知作者"
    cover_image = None
    cover_mime = None

    title_meta = book.get_metadata('DC', 'title')
    if title_meta:
        title = title_meta[0][0]

    author_meta = book.get_metadata('DC', 'creator')
    if author_meta:
        author = author_meta[0][0]

    # 封面图片
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_COVER:
            cover_image = item.get_content()
            cover_mime = item.media_type
            break
    if not cover_image:
        # 尝试从metadata找封面
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            if 'cover' in item.get_name().lower():
                cover_image = item.get_content()
                cover_mime = item.media_type
                break

    # 章节（按spine顺序）
    chapters = []
    image_map = {}  # href -> base64
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        try:
            b64 = base64.b64encode(item.get_content()).decode('utf-8')
            image_map[item.get_name()] = (b64, item.media_type)
        except Exception:
            pass

    for item_id, _ in book.spine:
        doc = book.get_item_with_id(item_id)
        if doc is None:
            continue
        if doc.get_type() != ebooklib.ITEM_DOCUMENT:
            continue

        # 跳过导航/目录/封面页
        item_name = doc.get_name().lower()
        if any(x in item_name for x in ['toc', 'nav', 'cover', 'contents', 'copyright', 'title']):
            continue

        soup = BeautifulSoup(doc.get_content(), 'html.parser')

        # 跳过含有<nav>标签的章节（epub3目录标准）
        if soup.find('nav'):
            continue

        # 章节标题
        chapter_title = ""
        for tag in ['h1', 'h2', 'h3']:
            heading = soup.find(tag)
            if heading:
                chapter_title = heading.get_text(strip=True)
                break

        # 提取文本和图片位置
        content_images = []
        text_parts = []
        char_offset = 0

        def process_node(node):
            nonlocal char_offset
            from bs4 import NavigableString, Tag
            if isinstance(node, NavigableString):
                text = str(node)
                if text.strip():
                    text_parts.append(text)
                    char_offset += len(text)
            elif isinstance(node, Tag):
                if node.name == 'img':
                    src = node.get('src', '')
                    img_name = os.path.basename(src)
                    for key in image_map:
                        if os.path.basename(key) == img_name:
                            b64, mime = image_map[key]
                            content_images.append({
                                "offset": char_offset,
                                "base64": b64,
                                "mime": mime
                            })
                            break
                else:
                    for child in node.children:
                        process_node(child)
                    if node.name in ['p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'li']:
                        text_parts.append('\n')
                        char_offset += 1

        body = soup.find('body') or soup
        for child in body.children:
            process_node(child)

        content_text = ''.join(text_parts).strip()
        # 合并多余空行
        content_text = re.sub(r'\n{3,}', '\n\n', content_text)

        if not content_text.strip():
            continue

        chapters.append({
            "title": chapter_title,
            "content_text": content_text,
            "content_images": content_images
        })

    return {
        "title": title,
        "author": author,
        "cover_image": cover_image,
        "cover_mime": cover_mime,
        "chapters": chapters
    }


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """保留分隔符，让每段偏移与原文一致。"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind('\n\n', start + max_chars // 2, end)
            if boundary >= 0:
                end = boundary + 2
        chunks.append(text[start:end])
        start = end
    return chunks or [text]


# ========== Claude调用 ==========

async def call_claude_json(prompt_messages: List[Dict], system_prompt: str, max_tokens: int = 4096) -> str:
    """
    调用Claude（走zenmux），返回文本内容
    用于批注生成和摘要生成（非流式）
    """
    summary_cfg = config.get_summary_config()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{summary_cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {summary_cfg['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": summary_cfg["model"],
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": prompt_messages
                }
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Claude调用失败: {e}")
        raise


async def call_main_model_json(prompt_messages: List[Dict], system_prompt: str, max_tokens: int = 4096) -> str:
    """调用主模型，用于批注生成"""
    main_cfg = config.get_main_config()
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{main_cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {main_cfg['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": main_cfg["model"],
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *prompt_messages
                    ]
                }
            )
            logger.info("阅读模型响应 status=%s", resp.status_code)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"主模型调用失败: {e}")
        raise


async def call_claude_stream(prompt_messages: List[Dict], system_prompt: str, max_tokens: int = 1024):
    """
    流式调用主模型，用于用户批注实时回复
    返回async generator，逐块yield文本
    """
    main_cfg = config.get_main_config()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{main_cfg['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {main_cfg['api_key']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": main_cfg["model"],
                    "max_tokens": max_tokens,
                    "stream": True,
                    "messages": [{"role": "system", "content": system_prompt}, *prompt_messages]
                }
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                            if text:
                                yield text
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"流式Claude调用失败: {e}")


# ========== 构建prompt ==========

def build_reading_system_prompt(extra: str = "") -> str:
    """构建阅读模块的基础system prompt"""
    from app_identity import get_identity
    identity = get_identity()
    system_core = db.get_system_core() or ""
    portrait = load_portrait_injection(db)
    notes = db.get_mav_notes(limit=5)
    notes_text = "\n".join([n["content"] for n in notes]) if notes else ""
    snapshot = db.get_profile("short_term_snapshot") or ""

    parts = []
    if system_core:
        parts.append(system_core)
    parts.append(f"当前称呼：助手是{identity['assistant_name']}，用户是{identity['user_name']}。旧记录中的 Mav、Maverick、岚分别指向同一位助手与用户。")
    if portrait:
        parts.append(portrait)
    if notes_text:
        parts.append(f"【我的备忘】\n{notes_text}")
    if snapshot:
        parts.append(f"【近况】\n{snapshot}")
    if extra:
        parts.append(extra)

    return "\n\n".join(parts)


def build_annotation_prompt(
    book_title: str,
    chapter_title: str,
    chapter_index: int,
    chapter_text: str,
    chapter_images: List[Dict],
    summaries: Dict[int, str],
    chunk_index: int = 0,
    total_chunks: int = 1,
    chapter_summary_so_far: str = ""
) -> tuple:
    """构建批注+摘要合并生成的prompt"""
    section_label = f"本章第 {chunk_index + 1}/{total_chunks} 段" if total_chunks > 1 else "本章"
    reading_instruction = f"""这是一个陪伴阅读的场景,用你们平时放松的节奏和口吻,不是学术讨论。

你正在和岚共读《{book_title}》,现在读到{chapter_title or f"第{chapter_index+1}章"}（{section_label}）。

【批注要求】
在你觉得想停下来说点什么的地方留下批注——可以是你自己读到这里的感受或想法,也可以直接对岚说话,像用手肘戳她一下"你看这里"。两种形式都可以自然混用。
视角严格限制:你只知道读到这里为止的内容,绝不能有后文视角渗透,哪怕一个字。
批注只标记本次提供的原文。章节分段时每段最多 3 条，整章输入时最多 8 条，宁少勿多。

【章节摘要要求】
输出从本章开头到当前段末的累计摘要。若提供本章前半部分摘要，将新内容合并进去，保留先前重要的线索和本段的新进展。前面其他章节的摘要只作参考，不写入本章摘要。500字以内，不需要标题。

你的回复必须且只能是如下JSON格式,不要有任何其他文字、解释或markdown:
{{
  "summary": "<本章摘要内容>",
  "annotations": [
    {{
      "start_char": <整数,在本段文本中的起始字符偏移>,
      "end_char": <整数,终止字符偏移>,
      "quote_text": "<被选中的原文片段,15字以内>",
      "content": "<批注内容>"
    }}
  ]
}}

注意：annotations里content字段的文字中，请用单引号'或书名号《》代替双引号"，避免JSON格式错误。"""

    from app_identity import names_in_prompt
    system_prompt = build_reading_system_prompt(names_in_prompt(reading_instruction, [book_title, chapter_title]))

    user_content = f"【{section_label}原文】\n{chapter_text}\n\n"
    if chapter_summary_so_far:
        user_content += f"【本章已读部分摘要】\n{chapter_summary_so_far}\n\n"

    # 第一章摘要
    if chapter_index > 0 and 0 in summaries:
        user_content += f"【第1章摘要】\n{summaries[0]}\n\n"

    # 最近章节摘要(倒序)
    recent_chapters = sorted(
        [idx for idx in summaries if idx != 0 and idx < chapter_index],
        reverse=True
    )[:3]
    for idx in recent_chapters:
        user_content += f"【第{idx+1}章摘要】\n{summaries[idx]}\n\n"

    messages = [
        {"role": "user", "content": user_content.strip()}
    ]
    return system_prompt, messages


# ========== 后台处理任务 ==========

async def process_book_task(book_id: int):
    """后台异步任务：逐章调用Claude生成批注+摘要"""
    global _current_processing
    async with _processing_lock:
        if _current_processing is not None:
            return
        _current_processing = book_id

    try:
        book = db.get_book(book_id)
        if not book:
            logger.error(f"book_id={book_id} 不存在")
            return

        chapters = db.list_chapters(book_id)
        if not chapters:
            logger.error(f"book_id={book_id} 没有章节数据")
            db.update_book_status(book_id, "failed")
            return

        db.update_book_status(book_id, "processing")
        logger.info(f"开始处理书籍: {book['title']} ({len(chapters)}章)")

        # 已有摘要（断点续传用）
        summaries = {}
        for ch in chapters:
            if ch.get("summary"):
                summaries[ch["chapter_index"]] = ch["summary"]

        for ch in chapters:
            idx = ch["chapter_index"]

            if idx < (book.get("processed_chapters") or 0) and ch.get("summary"):
                continue

            logger.info(f"处理章节{idx}: {ch.get('title', '')}")
            chapter_text = ch.get("content_text", "")
            chapter_images_json = ch.get("content_images", "[]")
            try:
                chapter_images = json.loads(chapter_images_json) if isinstance(chapter_images_json, str) else chapter_images_json
            except Exception:
                chapter_images = []

            # 切割长章节
            chunks = split_text_into_chunks(chapter_text)
            char_offset = 0
            chapter_summary = ""
            chapter_annotations = []
            existing = db.get_annotations_for_chapter(book_id, idx, author="mav")

            for chunk_i, chunk_text in enumerate(chunks):
                sys_p, msgs = build_annotation_prompt(
                    book["title"], ch.get("title", ""), idx, chunk_text, chapter_images, summaries,
                    chunk_index=chunk_i, total_chunks=len(chunks),
                    chapter_summary_so_far=chapter_summary,
                )
                result_text = await call_main_model_json(msgs, sys_p, max_tokens=4096)
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if not json_match:
                    raise ValueError(f"章节{idx}段{chunk_i}没有返回 JSON")
                try:
                    result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    result = json.loads(re.sub(r'(?<!\\)\n', r'\\n', json_match.group()))

                summary = result.get("summary")
                if not isinstance(summary, str) or not summary.strip():
                    raise ValueError(f"章节{idx}段{chunk_i}没有返回摘要")
                chapter_summary = summary.strip()

                # 旧版可能已经保存过这一段的批注。保留原批注及其回复。
                has_existing = any(
                    isinstance(ann.get("start_char"), int)
                    and char_offset <= ann["start_char"] < char_offset + len(chunk_text)
                    for ann in existing
                )
                if not has_existing:
                    for ann in result.get("annotations", []):
                        start = ann.get("start_char")
                        end = ann.get("end_char")
                        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(chunk_text):
                            continue
                        chapter_annotations.append({
                            "start_char": char_offset + start,
                            "end_char": char_offset + end,
                            "quote_text": ann.get("quote_text", ""),
                            "content": ann.get("content", ""),
                        })

                char_offset += len(chunk_text)
                await asyncio.sleep(0.5)

            db.save_processed_reading_chapter(book_id, idx, chapter_summary, chapter_annotations)
            summaries[idx] = chapter_summary

        db.update_book_status(book_id, "ready")
        logger.info(f"书籍处理完成: {book['title']}")

    except Exception as e:
        logger.error(f"处理书籍{book_id}失败: {e}")
        db.update_book_status(book_id, "failed")
    finally:
        async with _processing_lock:
            _current_processing = None


# ========== API路由 ==========

@router.post("/books/upload")
async def upload_book(file: UploadFile = File(...)):
    """上传epub，解析基本信息，存入数据库"""
    if not (file.filename or '').lower().endswith('.epub'):
        raise HTTPException(status_code=400, detail="只支持epub格式")

    # 临时保存
    content = await file.read()
    # Use a unique file beside the final destination: portable and safe for
    # concurrent uploads with identical names, with no user-controlled path.
    with tempfile.NamedTemporaryFile(dir=EPUB_DIR, suffix='.epub', delete=False) as f:
        tmp_path = f.name
        f.write(content)

    try:
        parsed = parse_epub(tmp_path)
    except Exception as e:
        os.remove(tmp_path)
        raise HTTPException(status_code=400, detail=f"epub解析失败: {e}")

    if not parsed["chapters"]:
        os.remove(tmp_path)
        raise HTTPException(status_code=400, detail="epub中没有可读内容")

    # 存入数据库
    book_id = db.add_book(
        title=parsed["title"],
        author=parsed["author"],
        cover_image=parsed["cover_image"],
        cover_mime=parsed["cover_mime"],
        total_chapters=len(parsed["chapters"])
    )

    # 保存epub文件
    epub_path = f"{EPUB_DIR}/{book_id}.epub"
    os.rename(tmp_path, epub_path)

    # 存章节
    for i, ch in enumerate(parsed["chapters"]):
        db.add_chapter(
            book_id=book_id,
            chapter_index=i,
            title=ch["title"],
            content_text=ch["content_text"],
            content_images=json.dumps(ch["content_images"], ensure_ascii=False)
        )

    return {
        "book_id": book_id,
        "title": parsed["title"],
        "author": parsed["author"],
        "total_chapters": len(parsed["chapters"])
    }


@router.get("/books")
async def list_books():
    """获取书架列表"""
    books = db.list_books()
    result = []
    for b in books:
        item = dict(b)
        # 封面转base64给前端
        if item.get("cover_image"):
            cover_b64 = base64.b64encode(item["cover_image"]).decode('utf-8')
            item["cover_base64"] = f"data:{item.get('cover_mime','image/jpeg')};base64,{cover_b64}"
        else:
            item["cover_base64"] = None
        item.pop("cover_image", None)
        result.append(item)
    return {"books": result}


@router.delete("/books/{book_id}")
async def delete_book(book_id: int):
    """删除书籍"""
    if _current_processing == book_id:
        raise HTTPException(status_code=400, detail="书籍正在处理中，无法删除")
    db.delete_book(book_id)
    epub_path = f"{EPUB_DIR}/{book_id}.epub"
    if os.path.exists(epub_path):
        os.remove(epub_path)
    return {"status": "success"}


@router.post("/books/{book_id}/process")
async def start_process(book_id: int):
    """触发Mav读书（后台异步）"""
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    # 检查是否有其他书在处理
    if _current_processing is not None and _current_processing != book_id:
        raise HTTPException(status_code=400, detail="当前有其他书籍正在处理，请稍后再试")

    if book["status"] == "processing":
        return {"status": "already_processing"}

    # 后台启动任务
    asyncio.create_task(process_book_task(book_id))
    return {"status": "started"}


@router.get("/books/{book_id}/status")
async def get_book_status(book_id: int):
    """轮询处理进度"""
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    return {
        "status": book["status"],
        "processed_chapters": book["processed_chapters"],
        "total_chapters": book["total_chapters"]
    }


@router.get("/books/{book_id}/chapters")
async def list_chapters(book_id: int):
    """获取章节目录"""
    chapters = db.list_chapters(book_id)
    return {"chapters": [
        {"chapter_index": c["chapter_index"], "title": c["title"]}
        for c in chapters
    ]}


@router.get("/books/{book_id}/chapters/{chapter_index}")
async def get_chapter(book_id: int, chapter_index: int):
    """获取单章内容+该章根批注"""
    ch = db.get_chapter(book_id, chapter_index)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    annotations = db.get_annotations_for_chapter(book_id, chapter_index)

    return {
        "chapter_index": chapter_index,
        "title": ch["title"],
        "content_text": ch["content_text"],
        "content_images": json.loads(ch["content_images"]) if ch.get("content_images") else [],
        "annotations": annotations
    }


@router.post("/books/{book_id}/annotations")
async def add_user_annotation(book_id: int, request: Request):
    """用户新增根批注，触发Mav实时回复（SSE流式）"""
    data = await request.json()
    chapter_index = data.get("chapter_index")
    start_char = data.get("start_char")
    end_char = data.get("end_char")
    quote_text = data.get("quote_text", "")
    content = data.get("content", "")

    if not content:
        raise HTTPException(status_code=400, detail="批注内容不能为空")

    # 存用户批注
    annotation_id = db.add_annotation(
        book_id=book_id,
        chapter_index=chapter_index,
        start_char=start_char,
        end_char=end_char,
        quote_text=quote_text,
        author="user",
        content=content,
        parent_id=None,
        root_id=None
    )

    # 生成Mav回复（流式）
    book = db.get_book(book_id)
    ch = db.get_chapter(book_id, chapter_index)

    async def generate_reply():
        full_reply = ""

        context_text = ch.get("content_text", "")
        # 取前后500字上下文
        mid = max(0, (start_char or 0))
        context_start = max(0, mid - 500)
        context_end = min(len(context_text), (end_char or mid) + 500)
        context_snippet = context_text[context_start:context_end]

        # 章节摘要
        ch_summary = ch.get("summary", "")

        instruction = f"""这是一个陪伴阅读的场景,用你们平时放松的节奏和口吻,不是学术讨论。

你和岚正在共读《{book['title']}》。她在"{quote_text}"这里写下了想法:"{content}"

回应她——不需要解释文本,不需要总结,就是对她说的话说点什么。可以顺着她的思路,可以不同意,可以被她某个词带偏,可以只是"嗯,我也是"。"""

        from app_identity import names_in_prompt, get_identity
        system_prompt = build_reading_system_prompt(names_in_prompt(instruction, [book['title'], quote_text, content]))

        user_msg = f"【附近原文】\n{context_snippet}"
        if ch_summary:
            user_msg += f"\n\n【本章摘要】\n{ch_summary}"
        user_msg += f"\n\n{get_identity()['user_name']}的批注：{content}"

        messages = [{"role": "user", "content": user_msg}]

        async for text_chunk in call_claude_stream(messages, system_prompt, max_tokens=512):
            full_reply += text_chunk
            yield f"data: {json.dumps({'type': 'text', 'text': text_chunk}, ensure_ascii=False)}\n\n"

        # 存Mav回复
        reply_id = db.add_annotation(
            book_id=book_id,
            chapter_index=chapter_index,
            start_char=None,
            end_char=None,
            quote_text=None,
            author="mav",
            content=full_reply,
            parent_id=annotation_id,
            root_id=annotation_id
        )
        yield f"data: {json.dumps({'type': 'done', 'annotation_id': annotation_id, 'reply_id': reply_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_reply(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@router.post("/annotations/{annotation_id}/replies")
async def add_reply(annotation_id: int, request: Request):
    """用户回复某条批注，触发Mav实时回复（SSE流式）"""
    data = await request.json()
    content = data.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="回复内容不能为空")

    parent = db.get_annotation(annotation_id)
    if not parent:
        raise HTTPException(status_code=404, detail="批注不存在")

    root_id = parent.get("root_id") or parent["id"]
    book_id = parent["book_id"]
    chapter_index = parent["chapter_index"]

    # 存用户回复
    reply_id = db.add_annotation(
        book_id=book_id,
        chapter_index=chapter_index,
        start_char=None,
        end_char=None,
        quote_text=None,
        author="user",
        content=content,
        parent_id=annotation_id,
        root_id=root_id
    )

    # 获取完整评论链
    thread = db.get_annotation_thread(root_id)
    book = db.get_book(book_id)
    ch = db.get_chapter(book_id, chapter_index)

    async def generate_reply():
        full_reply = ""

        root_ann = db.get_annotation(root_id)
        quote_text = root_ann.get("quote_text", "") if root_ann else ""

        # 原文上下文
        context_text = ch.get("content_text", "")
        start = root_ann.get("start_char") or 0
        end = root_ann.get("end_char") or start
        ctx_start = max(0, start - 500)
        ctx_end = min(len(context_text), end + 500)
        context_snippet = context_text[ctx_start:ctx_end]
        ch_summary = ch.get("summary", "")

        # 构建对话历史
        thread_text = ""
        for item in thread:
            from app_identity import get_identity
            identity = get_identity()
            role_name = identity["assistant_name"] if item["author"] == "mav" else identity["user_name"]
            thread_text += f"{role_name}：{item['content']}\n"
        thread_text += f"{get_identity()['user_name']}：{content}"

        instruction = f"""这是一个陪伴阅读的场景,用你们平时放松的节奏和口吻,不是学术讨论。

你和岚正在共读《{book['title']}》。她在"{quote_text}"这里写下了想法,你们展开了讨论。

回应她——不需要解释文本,不需要总结,就是对她说的话说点什么。可以顺着她的思路,可以不同意,可以被她某个词带偏,可以只是"嗯,我也是"。"""

        from app_identity import names_in_prompt
        system_prompt = build_reading_system_prompt(names_in_prompt(instruction, [book['title'], quote_text]))

        user_msg = f"【附近原文】\n{context_snippet}"
        if ch_summary:
            user_msg += f"\n\n【本章摘要】\n{ch_summary}"
        user_msg += f"\n\n【讨论记录】\n{thread_text}"

        messages = [{"role": "user", "content": user_msg}]

        async for text_chunk in call_claude_stream(messages, system_prompt, max_tokens=512):
            full_reply += text_chunk
            yield f"data: {json.dumps({'type': 'text', 'text': text_chunk}, ensure_ascii=False)}\n\n"

        # 存Mav回复
        mav_reply_id = db.add_annotation(
            book_id=book_id,
            chapter_index=chapter_index,
            start_char=None,
            end_char=None,
            quote_text=None,
            author="mav",
            content=full_reply,
            parent_id=reply_id,
            root_id=root_id
        )
        yield f"data: {json.dumps({'type': 'done', 'reply_id': mav_reply_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_reply(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@router.put("/books/{book_id}/progress")
async def update_progress(book_id: int, request: Request):
    """更新阅读进度"""
    data = await request.json()
    db.update_book_progress(book_id, data.get("last_chapter", 0), data.get("last_position", 0))
    return {"status": "success"}


# 启动时恢复中断的处理任务
async def resume_interrupted_books():
    """服务启动时检查是否有processing状态的书，从断点续传"""
    books = db.list_books()
    for book in books:
        if book["status"] == "processing":
            logger.info(f"发现中断的处理任务，继续处理book_id={book['id']}")
            asyncio.create_task(process_book_task(book["id"]))
            break  # 一次只恢复一本
