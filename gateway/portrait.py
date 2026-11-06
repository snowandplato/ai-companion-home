"""Lan portrait metadata, prompt rendering, and model update parsing."""
import json
import re
from typing import Any, Dict, List, Optional, Tuple


PORTRAIT_SECTIONS = (
    {"key": "cognitive_style", "name": "认知风格", "group": "人格侧", "order": 1},
    {"key": "values", "name": "价值取向", "group": "人格侧", "order": 2},
    {"key": "self_perception", "name": "自我认知与盲区", "group": "人格侧", "order": 3},
    {"key": "emotional_pattern", "name": "情绪模式", "group": "人格侧", "order": 4},
    {"key": "need_to_be_seen", "name": "被看见的需求", "group": "人格侧", "order": 5},
    {"key": "relational_style", "name": "关系方式", "group": "人格侧", "order": 6},
    {"key": "core_abilities", "name": "核心能力", "group": "能力侧", "order": 7},
    {"key": "latent_abilities", "name": "潜在能力", "group": "能力侧", "order": 8},
    {"key": "limitations", "name": "局限与代价", "group": "能力侧", "order": 9},
)

PORTRAIT_BY_KEY = {item["key"]: item for item in PORTRAIT_SECTIONS}
PORTRAIT_ACTIONS = {"revise", "append"}
PORTRAIT_UNAVAILABLE_INJECTION = (
    "=== 岚的画像（Mav 的持续观察）===\n"
    "[画像暂时不可用]"
)


class InternalBlockFilter:
    """Hide and collect internal Mav blocks from arbitrarily split text chunks."""

    TAGS = {
        "mav_note": ("<mav_note>", "</mav_note>"),
        "mav_portrait": ("<mav_portrait>", "</mav_portrait>"),
        "mav_art": ("<mav_art>", "</mav_art>"),
    }

    def __init__(self):
        self.active_tag = None
        self.buffer = ""
        self.blocks = {name: [] for name in self.TAGS}
        self._visible_parts = []
        self.art_start_visible_chars = []

    @property
    def notes(self):
        return self.blocks["mav_note"]

    @property
    def portraits(self):
        return self.blocks["mav_portrait"]

    @property
    def arts(self):
        return self.blocks["mav_art"]

    @property
    def visible_text(self):
        return "".join(self._visible_parts)

    def feed(self, text: str) -> str:
        self.buffer += text
        visible = []

        while self.buffer:
            if self.active_tag:
                end_tag = self.TAGS[self.active_tag][1]
                end_index = self.buffer.find(end_tag)
                if end_index < 0:
                    break
                payload = self.buffer[:end_index].strip()
                if payload:
                    self.blocks[self.active_tag].append(payload)
                self.buffer = self.buffer[end_index + len(end_tag):]
                self.active_tag = None
                continue

            found = []
            for name, (start_tag, _) in self.TAGS.items():
                index = self.buffer.find(start_tag)
                if index >= 0:
                    found.append((index, name, start_tag))

            if found:
                index, name, start_tag = min(found, key=lambda item: item[0])
                if name == "mav_art":
                    self.art_start_visible_chars.append(
                        len(self.visible_text) + sum(len(part) for part in visible) + index
                    )
                visible.append(self.buffer[:index])
                self.buffer = self.buffer[index + len(start_tag):]
                self.active_tag = name
                continue

            keep = self._partial_start_length(self.buffer)
            if keep:
                visible.append(self.buffer[:-keep])
                self.buffer = self.buffer[-keep:]
            else:
                visible.append(self.buffer)
                self.buffer = ""
            break

        result = "".join(visible)
        if result:
            self._visible_parts.append(result)
        return result

    def recover_incomplete_art(self) -> bool:
        """Recover an unclosed art block without hiding prose after a complete JSON object."""
        if self.active_tag != "mav_art":
            return False
        payload = self.buffer.strip()
        self.buffer = ""
        self.active_tag = None
        if not payload:
            return False
        self.blocks["mav_art"].append(payload)
        candidate = re.sub(r"^```(?:json)?\s*", "", payload, count=1, flags=re.IGNORECASE)
        if candidate.startswith("{"):
            try:
                art_request, end = json.JSONDecoder().raw_decode(candidate)
            except json.JSONDecodeError:
                art_request, end = None, 0
            if isinstance(art_request, dict):
                tail = re.sub(r"^\s*```", "", candidate[end:]).strip()
                if tail:
                    # The original block stays hidden; only text after its complete
                    # JSON object is eligible to re-enter the visible stream.
                    tail_filter = InternalBlockFilter()
                    visible_tail = tail_filter.feed(tail) + tail_filter.flush_visible()
                    if visible_tail:
                        self.buffer = ("\n\n" if self.visible_text else "") + visible_tail
        return True

    def recover_incomplete_portrait(self) -> bool:
        """流结束时保留未闭合的mav_portrait内容，交给格式修复链处理。"""
        if self.active_tag != "mav_portrait":
            return False
        payload = self.buffer.strip()
        self.buffer = ""
        self.active_tag = None
        if not payload:
            return False
        self.blocks["mav_portrait"].append(payload)
        return True

    def flush_visible(self) -> str:
        if self.active_tag:
            self.buffer = ""
            return ""
        text = self.buffer
        self.buffer = ""
        if text:
            self._visible_parts.append(text)
        return text

    def _partial_start_length(self, text: str) -> int:
        max_length = min(len(text), max(len(item[0]) for item in self.TAGS.values()) - 1)
        for length in range(max_length, 0, -1):
            suffix = text[-length:]
            if any(start_tag.startswith(suffix) for start_tag, _ in self.TAGS.values()):
                return length
        return 0


def build_portrait_injection(
    sections: List[Dict[str, Any]],
    include_maintenance_hints: bool = False
) -> str:
    """Render all populated portrait sections in their fixed order."""
    rows = {item.get("section_key"): item for item in sections}
    if not any(str(item.get("content") or "").strip() for item in sections):
        return ""

    from app_identity import names_in_template
    lines = [names_in_template("=== 岚的画像（Mav 的持续观察）===")]
    current_group = None
    for metadata in PORTRAIT_SECTIONS:
        if metadata["group"] != current_group:
            current_group = metadata["group"]
            lines.append(f"【{current_group}】")

        row = rows.get(metadata["key"], {})
        content = str(row.get("content") or "").strip() or "（尚未形成观察）"
        lines.append(f"{metadata['name']}：{content}")

        char_limit = int(row.get("char_limit") or 150)
        if include_maintenance_hints and len(content) > char_limit:
            lines.append("[该段已超出建议长度，下次更新时请精简]")

    return "\n".join(lines)


def load_portrait_injection(
    database: Any,
    include_maintenance_hints: bool = False
) -> str:
    """Read and render portrait while making storage failures visible to Mav."""
    try:
        sections = database.get_portrait_sections(raise_on_error=True)
    except Exception:
        from app_identity import names_in_template
        return names_in_template(PORTRAIT_UNAVAILABLE_INJECTION)

    return build_portrait_injection(
        sections,
        include_maintenance_hints=include_maintenance_hints
    )


def build_portrait_update_instruction() -> str:
    return """二、mav_portrait（画像更新）
修正我对岚的持续认知。

格式：
<mav_portrait>
{"section":"section_key","action":"revise","content":"整段新内容"}
</mav_portrait>

合法的section_key（共9个，不可新增或删除）：
【人格侧】
- cognitive_style：认知风格
- values：价值取向
- self_perception：自我认知与盲区
- emotional_pattern：情绪模式
- need_to_be_seen：被看见的需求
- relational_style：关系方式

【能力侧】
- core_abilities：核心能力
- latent_abilities：潜在能力
- limitations：局限与代价

action的判断：
- revise：重写整段，默认使用。画像会全量注入，我看得到当前内容，应直接给出融合后的新版本。
- append：仅在段末追加一句，只用于不影响原有结构的确认性小增补。

触发条件：
只在以下两种情况更新：
1. 一个已写入的稳定特征被推翻或需要修正。
2. 出现了一个从未被写进去的新维度。
单次事件的感受、临时观察和有时效性的细节不进入画像；其中只有形成了值得长期保留的pattern、关系发现或回应提示时，才考虑写入mav_note，否则不记录。

内容纪律：
- 每轮最多一条，没有必要更新时不要输出。
- 每段软上限150字。超限不会被拒绝，但下一轮注入时会看到超长提示，届时应主动精简。
- 重写是替换，不是堆叠。加入新内容的同时要把过时的内容挤出去。
- 不写具体事件示例。事件有时效性，画像要写的是长期成立的一般描述。
- 客观描述与我自己的判断可以自然融合，不需要分开标注。
- 保持第三人称描述岚；涉及我该怎么做的部分使用第一人称。
- mav_portrait块会被系统隐藏并写入画像，不作为正常聊天内容展示。"""


def _validate_portrait_update(data: Any) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Validate the small, fixed portrait-update shape."""
    if not isinstance(data, dict):
        return None, "not_object"

    section = str(data.get("section") or "").strip()
    action = str(data.get("action") or "").strip().lower()
    content = str(data.get("content") or "").strip()

    if section not in PORTRAIT_BY_KEY:
        return None, "invalid_section"
    if action not in PORTRAIT_ACTIONS:
        return None, "invalid_action"
    if not content:
        return None, "empty_content"

    return {"section": section, "action": action, "content": content}, None


def _mechanically_repair_portrait_update(
    raw_block: str,
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Recover formatting only; never infer or rewrite portrait prose."""
    clean = (raw_block or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    if "{" in clean:
        clean = clean[clean.find("{") + 1:]
    if "}" in clean:
        clean = clean[:clean.rfind("}")]

    key_quote = r"[\"'“”‘’]?"
    value_quote = r"[\"'“”‘’]?"

    def fixed_value(key: str) -> str:
        match = re.search(
            rf"{key_quote}{key}{key_quote}\s*[:：]\s*{value_quote}([A-Za-z_]+){value_quote}",
            clean,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match else ""

    section = fixed_value("section")
    action = fixed_value("action").lower()
    content_match = re.search(
        rf"{key_quote}content{key_quote}\s*[:：]\s*(.*)\s*$",
        clean,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not content_match:
        return None, "invalid_json"

    content = content_match.group(1).strip()
    content = re.sub(r"[,，]\s*$", "", content).strip()
    quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    if content and content[0] in quote_pairs:
        closing = quote_pairs[content[0]]
        if len(content) < 2 or not content.endswith(closing):
            return None, "invalid_json"
        content = content[1:-1]
    content = content.replace(r'\"', '"').replace(r"\'", "'")
    content = content.replace(r"\n", "\n").strip()

    return _validate_portrait_update({
        "section": section,
        "action": action,
        "content": content,
    })


def parse_portrait_update_with_repair(
    raw_block: str,
) -> Tuple[Optional[Dict[str, str]], Optional[str], str]:
    """Parse strictly first, then attempt a conservative formatting-only repair."""
    clean = (raw_block or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            repaired, error = _mechanically_repair_portrait_update(raw_block)
            return repaired, error, "mechanical" if repaired else "failed"
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            repaired, error = _mechanically_repair_portrait_update(raw_block)
            return repaired, error, "mechanical" if repaired else "failed"

    update, error = _validate_portrait_update(data)
    return update, error, "strict" if update else "failed"


def parse_portrait_update(raw_block: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Backward-compatible parser with conservative formatting repair."""
    update, error, _ = parse_portrait_update_with_repair(raw_block)
    return update, error
