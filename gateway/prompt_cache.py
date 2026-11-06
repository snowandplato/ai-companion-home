"""Anthropic原生prompt cache请求体组装。"""
import logging
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


def build_prompt_cache_system_sections(
    stable_prefix_parts: List[str],
    notes_part: str,
    snapshot_part: str,
    dynamic_parts: List[str],
    separator: str = "\n\n---\n\n"
) -> List[Dict[str, Any]]:
    """按稳定程度分组，并保持与原system字符串完全相同的文本。"""
    grouped_sections = []
    stable_prefix = separator.join(stable_prefix_parts)
    if stable_prefix:
        grouped_sections.append({"text": stable_prefix, "cache_boundary": True})
    if notes_part:
        grouped_sections.append({"text": notes_part, "cache_boundary": True})
    if snapshot_part:
        grouped_sections.append({"text": snapshot_part, "cache_boundary": True})
    if dynamic_parts:
        grouped_sections.append({
            "text": separator.join(dynamic_parts),
            "cache_boundary": False
        })

    sections = []
    for index, section in enumerate(grouped_sections):
        text = section["text"]
        if index > 0:
            text = separator + text
        sections.append({
            "text": text,
            "cache_boundary": section["cache_boundary"]
        })
    return sections


def apply_anthropic_prompt_cache(
    formatted_body: Dict[str, Any],
    parsed_request: Dict[str, Any],
    ttl: str
) -> bool:
    """将分层system内容转换为Anthropic原生缓存blocks。"""
    normalized_ttl = (ttl or "off").lower()
    if normalized_ttl == "off":
        return False
    if normalized_ttl not in {"5m", "1h"}:
        logger.warning(
            "PROMPT_CACHE_TTL=%s无效；可用值为off、5m、1h，本次不启用缓存",
            normalized_ttl
        )
        return False

    sections = parsed_request.get("prompt_cache_system_sections") or []
    if not sections:
        return False

    system_blocks = []
    breakpoint_count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        text = section.get("text", "")
        if not text:
            continue
        block = {"type": "text", "text": text}
        if section.get("cache_boundary") and breakpoint_count < 3:
            block["cache_control"] = {
                "type": "ephemeral",
                "ttl": normalized_ttl
            }
            breakpoint_count += 1
        system_blocks.append(block)

    if not breakpoint_count:
        return False

    formatted_body["system"] = system_blocks
    logger.info(
        "Anthropic prompt cache enabled: ttl=%s breakpoints=%s",
        normalized_ttl,
        breakpoint_count
    )
    return True
