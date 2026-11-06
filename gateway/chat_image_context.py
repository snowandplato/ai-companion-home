"""图片上下文的纯文本处理；保持无第三方依赖，方便独立测试。"""
import json
import re
from typing import Any, Dict, List


def context_layer_for_rounds(rounds_since: int) -> str:
    if rounds_since <= 2:
        return "detail"
    if rounds_since <= 10:
        return "summary"
    return "none"


def truncate_sentence(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if limit <= 0:
        return ""
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    last = max(cut.rfind(mark) for mark in "。！？.!?")
    if last >= limit // 2:
        return cut[:last + 1]
    return clean[:max(0, limit - 1)].rstrip() + "…"


def parse_vl_result(raw: str) -> Dict[str, str]:
    clean = (raw or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        image_type = str(data.get("type") or "other")[:40]
        summary = truncate_sentence(str(data.get("summary") or ""), 220)
        detail_limit = 1000 if image_type in {"chat_screenshot", "code_screenshot", "document"} else 600
        detail = truncate_sentence(str(data.get("detail") or ""), detail_limit)
    except Exception:
        image_type = "other"
        detail = truncate_sentence(clean, 600)
        summary = truncate_sentence(detail, 220)
    if not summary:
        summary = truncate_sentence(detail, 220) or "图片内容暂时无法描述"
    if not detail:
        detail = summary
    return {"type": image_type, "summary": summary, "detail": detail}


def build_image_context(images: List[Dict[str, Any]], use_detail: bool, limit: int) -> str:
    parts = []
    used = 0
    field = "vision_detail" if use_detail else "vision_summary"
    for index, image in enumerate(images, start=1):
        text = (image.get(field) or image.get("vision_summary") or "").strip()
        if not text:
            text = "图片描述暂时不可用"
        line = f"图片{index}：{text}"
        separator_length = 1 if parts else 0
        remaining = limit - used - separator_length
        if remaining <= 0:
            break
        line = truncate_sentence(line, remaining)
        parts.append(line)
        used += separator_length + len(line)
    return "\n".join(parts)
