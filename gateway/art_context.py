"""Mav图片创作内部块与审稿决定的纯解析逻辑。"""
import json
import re
from typing import Any, Dict, Optional, Tuple


ASPECT_RATIOS = {"square", "portrait", "landscape"}


def _json_object(raw: str) -> Optional[Dict[str, Any]]:
    clean = (raw or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        # raw_decode可以从“完整JSON + 漏写结束标签后的普通文字”中安全取出首个对象。
        decoder = json.JSONDecoder()
        data = None
        for match in re.finditer(r"\{", clean):
            try:
                candidate, _ = decoder.raw_decode(clean[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                data = candidate
                break
        if data is None:
            return None
    return data if isinstance(data, dict) else None


def parse_art_request(raw: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = _json_object(raw)
    if not data:
        return None, "invalid_json"
    decision = str(data.get("decision") or ("create" if data.get("prompt") else "")).strip().lower()
    if decision == "decline":
        return {"decision": "decline"}, None
    if decision != "create":
        return None, "invalid_decision"

    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return None, "empty_prompt"
    aspect_ratio = str(data.get("aspect_ratio") or "square").strip().lower()
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = "square"

    def short_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:200] for item in value[:8] if str(item).strip()]

    return {
        "decision": "create",
        "prompt": prompt[:8000],
        "aspect_ratio": aspect_ratio,
        "must_keep": short_list(data.get("must_keep")),
        "avoid": short_list(data.get("avoid")),
    }, None


def parse_art_review(raw: str, candidate_count: int, can_retry: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = _json_object(raw)
    if not data:
        return None, "invalid_json"
    decision = str(data.get("decision") or "").strip().lower()
    if decision == "select":
        try:
            selected_index = int(data.get("selected_index"))
        except (TypeError, ValueError):
            return None, "invalid_selected_index"
        if not 1 <= selected_index <= candidate_count:
            return None, "invalid_selected_index"
        return {
            "decision": "select",
            "selected_index": selected_index,
            "caption": str(data.get("caption") or "").strip()[:1000],
            "review_summary": str(data.get("review_summary") or "").strip()[:500],
        }, None
    if decision == "retry" and can_retry:
        revised_prompt = str(data.get("revised_prompt") or "").strip()
        if not revised_prompt:
            return None, "empty_revised_prompt"
        return {
            "decision": "retry",
            "revised_prompt": revised_prompt[:8000],
            "rejection_summary": str(data.get("rejection_summary") or "").strip()[:500],
        }, None
    if decision == "reject" or (decision == "retry" and not can_retry):
        return {
            "decision": "reject",
            "caption": str(data.get("caption") or "这次没有画到我愿意交给你的程度。").strip()[:1000],
            "rejection_summary": str(data.get("rejection_summary") or "").strip()[:500],
        }, None
    return None, "invalid_decision"


def size_for_aspect_ratio(aspect_ratio: str) -> str:
    return {
        "portrait": "1024x1536",
        "landscape": "1536x1024",
    }.get(aspect_ratio, "1024x1024")


def review_model_for_main_model(main_model: Any, fallback_model: str) -> str:
    """Claude主对话沿用本轮模型；其他模型使用支持看图的固定兜底。"""
    model = str(main_model or "").strip()
    if "claude" in model.lower():
        return model
    return str(fallback_model or "claude-sonnet-4-6").strip()
