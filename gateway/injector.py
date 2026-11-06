"""
记忆注入模块
负责将记忆信息注入到对话上下文中
"""
import logging
import random
import re
from typing import Dict, Any, List, Optional
from db import db
from config import config
from embedding_service import embedding_service
from portrait import load_portrait_injection
from prompt_cache import build_prompt_cache_system_sections

logger = logging.getLogger(__name__)


class MemoryInjector:
    """记忆注入器"""

    def __init__(self):
        self.db = db

    def build_memory_prompt(
        self,
        short_term_summary: str = None,
        long_term_memories: List[Dict] = None
    ) -> str:
        """
        构建记忆提示

        Args:
            short_term_summary: 短期对话总结
            long_term_memories: 长期记忆列表

        Returns:
            str: 格式化后的记忆内容
        """
        parts = []

        # 1. 长期记忆
        if long_term_memories:
            for memory in long_term_memories:
                event_date = memory.get('event_date', '')
                recall_text = memory.get('recall_text', memory.get('content', ''))
                if recall_text:
                    parts.append(f"[{event_date}的记忆浮现] {recall_text}")

        # 2. 短期记忆摘要
        if short_term_summary:
            parts.append(f"=== 近期对话摘要 ===\n{short_term_summary}")

        return "\n\n".join(parts) if parts else ""

    def inject(
        self,
        parsed_request: Dict[str, Any],
        short_term_summary: str = None,
        long_term_memories: List[Dict] = None,
        mav_notes: List[Dict] = None,
        instant_summary: str = None
    ) -> Dict[str, Any]:
        """
        向请求注入记忆

        注入顺序（从前往后）：
        1. System Core（已在process_request中处理）
        2. Mav备忘
        3. 短期记忆摘要
        4. 即时对话总结
        5. 长期记忆检索

        Args:
            parsed_request: 解析后的请求数据
            short_term_summary: 短期对话总结
            long_term_memories: 长期记忆列表
            mav_notes: Mav备忘列表
            instant_summary: 即时对话总结

        Returns:
            Dict: 注入记忆后的请求数据
        """
        result = parsed_request.copy()
        memory_parts = []

        # 构建Mav备忘
        if mav_notes:
            notes_content = ["## 我的备忘"]
            for note in mav_notes:
                content = note.get('content', '')
                if content:
                    notes_content.append(f"- {content}")
            if len(notes_content) > 1:
                memory_parts.append("\n".join(notes_content))

        # 添加短期对话总结
        if short_term_summary:
            memory_parts.append(f"=== 短期记忆 ===\n{short_term_summary}")

        # 添加即时对话总结
        if instant_summary:
            memory_parts.append(f"=== 即时对话总结 ===\n{instant_summary}")

        # 构建长期记忆
        if long_term_memories:
            for memory in long_term_memories:
                event_date = memory.get('event_date', '')
                recall_text = memory.get('recall_text', memory.get('content', ''))
                if recall_text:
                    memory_parts.append(f"[{event_date}的记忆浮现] {recall_text}")

        # 合并所有记忆（追加到现有system后面）
        memory_content = "\n\n".join(memory_parts)

        if memory_content:
            existing_system = result.get("system", "")
            if existing_system:
                result["system"] = f"{existing_system}\n\n---\n\n{memory_content}"
            else:
                result["system"] = memory_content

        return result

    def extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []

        import re

        try:
            import jieba
            import jieba.analyse
            # 用TF-IDF提取中文关键词
            chinese_keywords = jieba.analyse.extract_tags(text, topK=15, withWeight=False)
        except ImportError:
            chinese_keywords = []

        # 额外提取英文单词（如 "Toy", "MCP" 这类不会被jieba处理的词）
        english_words = re.findall(r'[a-zA-Z]{2,}', text)

        # 保留用户明确点名的短概念。jieba有时会漏掉“仪式感/真实性”这类词，
        # 但它们往往正是检索长期记忆的关键。
        explicit_terms = []
        quoted_terms = re.findall(r'["“”‘’「」『』]([^"“”‘’「」『』]{2,20})["“”‘’「」『』]', text)
        concept_terms = re.findall(
            r'[\u4e00-\u9fff]{1,8}(?:仪式感|真实感|安全感|边界感|连续性|真实性|自主性|关系|记忆|系统|共读|搬家|身体|小本本|便签|信件)',
            text
        )
        short_feeling_terms = re.findall(r'[\u4e00-\u9fff]{1,6}[感性]', text)
        known_terms = [
            "仪式感", "真实感", "安全感", "边界感", "连续性", "真实性", "自主性",
            "记忆系统", "共读", "搬家", "Doppler", "小本本", "便签", "信件"
        ]
        direct_terms = [term for term in known_terms if term in text]
        for term in direct_terms + quoted_terms + concept_terms + short_feeling_terms:
            term = term.strip("，。！？、：；,.!?;: ")
            if 2 <= len(term) <= 20:
                explicit_terms.append(term)

        # 合并去重
        seen = set()
        result = []
        for kw in explicit_terms + list(chinese_keywords) + english_words:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)

        return result[:20]


class InjectorService:
    """记忆注入服务"""

    def __init__(self):
        self.injector = MemoryInjector()
        self.db = db
        self._memory_write_debug = []
        self._memory_injection_debug = []

    def _record_memory_write_debug(self, entry: Dict[str, Any]) -> None:
        """Keep a small in-memory debug trail for Admin troubleshooting."""
        from datetime import datetime

        item = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **entry
        }
        self._memory_write_debug.insert(0, item)
        self._memory_write_debug = self._memory_write_debug[:20]

    def get_memory_write_debug(self) -> List[Dict[str, Any]]:
        """Return recent memory write attempts."""
        return self._memory_write_debug

    def _record_memory_injection_debug(self, entry: Dict[str, Any]) -> None:
        """Keep recent memory injection decisions for Admin troubleshooting."""
        from datetime import datetime

        item = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **entry
        }
        self._memory_injection_debug.insert(0, item)
        self._memory_injection_debug = self._memory_injection_debug[:20]

    def get_memory_injection_debug(self) -> List[Dict[str, Any]]:
        """Return recent memory injection attempts."""
        return self._memory_injection_debug

    async def _expand_keywords(self, keywords: List[str]) -> List[str]:
        """
        用Haiku对关键词做语义扩展，生成同义词和相关词
        失败时静默返回空列表。返回值只包含扩展词，不混入原始关键词。
        """
        if not keywords:
            return []
        try:
            from config import config
            import httpx
            summary_config = config.get_summary_config()
            if not summary_config.get("api_key"):
                return []

            kw_str = "、".join(keywords)
            prompt = f"""对以下关键词，生成同义词、近义词和相关概念，用于检索记忆。
关键词：{kw_str}

要求：
1. 每个关键词扩展2-3个相关词
2. 只输出扩展后的词，用逗号分隔，不要输出原始关键词，不要其他解释
3. 控制在20个词以内

输出："""

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{summary_config['base_url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {summary_config['api_key']}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": summary_config["model"],
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                if response.status_code == 200:
                    result = response.json()
                    expanded_text = result["choices"][0]["message"]["content"].strip()
                    expanded = [w.strip() for w in expanded_text.replace("，", ",").split(",") if w.strip()]
                    original_set = {kw.lower() for kw in keywords}
                    filtered = []
                    seen = set()
                    for word in expanded:
                        key = word.lower()
                        if key in original_set or key in seen:
                            continue
                        seen.add(key)
                        filtered.append(word)
                    return filtered[:20]
        except Exception as e:
            logger.debug(f"语义扩展失败，使用原始关键词: {e}")
        return []

    def _build_legacy_long_term_parts(self, keywords: List[str]) -> List[str]:
        """Build legacy memories prompt parts."""
        long_term_memories = self.db.get_long_term_memories(keywords) if keywords else []
        long_term_parts = []
        for memory in long_term_memories:
            event_date = memory.get('event_date', '')
            recall_text = memory.get('recall_text', memory.get('content', ''))
            if recall_text:
                long_term_parts.append(f"[{event_date}的记忆浮现] {recall_text}")
        return long_term_parts

    def _build_v2_long_term_parts(
        self,
        keywords: List[str],
        query_embedding: List[float] = None,
        user_message: str = "",
        primary_keywords: List[str] = None,
        expanded_keywords: List[str] = None,
        previous_keywords: List[str] = None
    ) -> Dict[str, Any]:
        """Build Thread/Event memories prompt parts."""
        memory_v2 = self.db.get_long_term_memory_v2(
            keywords,
            query_embedding=query_embedding,
            primary_keywords=primary_keywords,
            expanded_keywords=expanded_keywords,
            previous_keywords=previous_keywords
        ) if (keywords or query_embedding) else {}
        threads = memory_v2.get("threads", [])
        events = memory_v2.get("events", [])
        sibling_events = memory_v2.get("sibling_events", [])
        random_events = []

        direct_event_ids = {
            event.get("id")
            for event in events
            if event.get("id") is not None
        }
        deduped_sibling_events = []
        seen_sibling_ids = set()
        for event in sibling_events:
            event_id = event.get("id")
            if event_id is not None:
                if event_id in direct_event_ids or event_id in seen_sibling_ids:
                    continue
                seen_sibling_ids.add(event_id)
            deduped_sibling_events.append(event)
        sibling_events = deduped_sibling_events

        if not threads and not events:
            return {"parts": [], "debug": memory_v2}

        memory_v2["sibling_events"] = sibling_events

        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        parts = [
            "=== 长期记忆（Thread / Event） ===",
            f"当前日期：{current_date}",
            "说明：方括号内日期是记忆事件发生/记录的大致时间；这些内容只在相关时自然引用，不需要逐条复述。"
        ]

        def truncate(text: str, max_chars: int) -> str:
            if not text or len(text) <= max_chars:
                return text or ""
            cut = text[:max_chars]
            for punct in ["。", "！", "？", ".", "!", "?"]:
                pos = cut.rfind(punct)
                if pos > max_chars // 2:
                    return cut[:pos + 1]
            return cut + "..."

        strength_rank = {"weak": 1, "medium": 2, "strong": 3}

        def event_strength(event: Dict[str, Any]) -> str:
            primary_match_count = int(event.get("primary_match_count") or 0)
            keyword_score = int(event.get("keyword_score") or event.get("match_count") or 0)
            embedding_score = float(event.get("embedding_score") or 0.0)
            source = event.get("match_source") or ""
            if source == "primary_mixed" or primary_match_count >= 2 or keyword_score >= 4 or embedding_score >= 0.62:
                return "strong"
            if source in {"expanded_mixed", "previous_mixed"} or primary_match_count >= 1 or keyword_score >= 2 or embedding_score >= 0.52:
                return "medium"
            return "weak"

        thread_strengths: Dict[str, str] = {}
        for event in events:
            event_key = event.get("event_key") or ""
            if not event_key:
                continue
            strength = event_strength(event)
            event["injection_strength"] = strength
            current = thread_strengths.get(event_key, "weak")
            if strength_rank[strength] > strength_rank[current]:
                thread_strengths[event_key] = strength

        for thread in threads:
            event_key = thread.get("event_key") or ""
            source = thread.get("match_source") or ""
            embedding_score = float(thread.get("embedding_score") or 0.0)
            strength = thread_strengths.get(event_key, "medium")
            if source == "embedding" and embedding_score >= 0.62:
                strength = "strong"
            thread["injection_strength"] = strength

        sibling_events = [
            event
            for event in sibling_events
            if thread_strengths.get(event.get("event_key") or "", "weak") != "weak"
        ]
        memory_v2["sibling_events"] = sibling_events

        random_rate = self._random_resurface_rate(user_message)
        if (
            config.MEMORY_V2_RANDOM_RESURFACE_MAX > 0
            and random_rate > 0
            and random.random() < random_rate
        ):
            exclude_ids = [
                int(event.get("id"))
                for event in events + sibling_events
                if event.get("id") is not None
            ]
            exclude_keys = [
                thread.get("event_key")
                for thread in threads
                if thread.get("event_key")
            ]
            random_event = self.db.get_random_memory_event_v2(
                exclude_event_ids=exclude_ids,
                exclude_event_keys=exclude_keys,
                min_importance=3
            )
            if random_event:
                random_events = [random_event][:config.MEMORY_V2_RANDOM_RESURFACE_MAX]
        memory_v2["random_events"] = random_events

        if threads:
            parts.append("【相关主题脉络】")
            for thread in threads:
                title = thread.get("title") or thread.get("event_key") or "未命名主题"
                event_key = thread.get("event_key", "")
                summary = thread.get("summary_short") or thread.get("current_state") or thread.get("summary_full") or ""
                summary_full = thread.get("summary_full") or ""
                current_state = thread.get("current_state") or ""
                last_mentioned = thread.get("last_mentioned_at") or "未知"
                time_note = f"last_mentioned_at: {last_mentioned}"
                strength = thread.get("injection_strength", "medium")
                if strength == "weak":
                    line = f"- {title}（{event_key}）：{truncate(summary, 140)}"
                elif strength == "medium":
                    line = f"- {title}（{event_key}｜{time_note}）：{truncate(summary, 220)}"
                    if current_state and current_state != summary:
                        line += f"\n  当前状态：{truncate(current_state, 180)}"
                else:
                    line = f"- {title}（{event_key}｜{time_note}）：{truncate(summary, 240)}"
                    if summary_full and summary_full != summary:
                        line += f"\n  脉络：{truncate(summary_full, 320)}"
                    if current_state and current_state != summary:
                        line += f"\n  当前状态：{truncate(current_state, 180)}"
                parts.append(line)

        if events:
            parts.append("【直接命中的具体记忆】")
            for event in events:
                date = event.get("occurred_at") or "未知时间"
                summary = event.get("summary") or ""
                event_key = event.get("event_key") or ""
                if summary:
                    parts.append(f"- [{date}]（{event_key}）{summary}")

        if sibling_events:
            parts.append("【同主题下可参考的补充记忆】")
            for event in sibling_events:
                date = event.get("occurred_at") or "未知时间"
                summary = event.get("summary") or ""
                event_key = event.get("event_key") or ""
                if summary:
                    parts.append(f"- [{date}]（{event_key}）{summary}")

        if random_events:
            parts.append("【偶然浮现的一点旧记忆】")
            for event in random_events:
                date = event.get("occurred_at") or "未知时间"
                summary = truncate(event.get("summary") or "", 180)
                event_key = event.get("event_key") or ""
                if summary:
                    parts.append(f"- [{date}]（{event_key}）{summary}")

        parts.append("提示：这些记忆用于帮助你自然理解当前对话；不要逐条复述，只在确实相关时温和引用。")
        return {"parts": ["\n".join(parts)], "debug": memory_v2}

    def _random_resurface_rate(self, user_message: str) -> float:
        """Choose a gentle random-memory rate by current conversation type."""
        text = (user_message or "").lower()
        if not text:
            return 0.0

        technical_markers = [
            "代码", "报错", "bug", "npm", "build", "部署", "上传", "重启",
            "日志", "接口", "数据库", "git", "token", "embedding", "api"
        ]
        serious_markers = [
            "医生", "医院", "生病", "疼", "焦虑", "抑郁", "崩溃", "难受",
            "害怕", "严重", "风险", "工作", "加班"
        ]
        casual_markers = [
            "哈哈", "嘿嘿", "好耶", "猫", "咖啡", "吃", "可爱", "开心",
            "想起", "梦", "周末", "假期", "散步"
        ]

        if len(text) > 600:
            return config.MEMORY_V2_RANDOM_RESURFACE_LOW_RATE
        if any(marker in text for marker in technical_markers):
            return config.MEMORY_V2_RANDOM_RESURFACE_LOW_RATE
        if any(marker in text for marker in serious_markers):
            return config.MEMORY_V2_RANDOM_RESURFACE_LOW_RATE
        if any(marker in text for marker in casual_markers):
            return config.MEMORY_V2_RANDOM_RESURFACE_CASUAL_RATE
        return config.MEMORY_V2_RANDOM_RESURFACE_NORMAL_RATE

    def _build_long_term_parts_by_mode(
        self,
        keywords: List[str],
        query_embedding: List[float] = None,
        user_message: str = "",
        primary_keywords: List[str] = None,
        expanded_keywords: List[str] = None,
        previous_keywords: List[str] = None
    ) -> Dict[str, Any]:
        """Build long-term memory injection according to MEMORY_INJECTION_MODE."""
        mode = (config.MEMORY_INJECTION_MODE or "v2").lower()
        if mode not in {"legacy", "v2", "hybrid"}:
            logger.warning(f"Unknown MEMORY_INJECTION_MODE={mode}, fallback to v2")
            mode = "v2"

        # The legacy retriever has no source-aware scoring. Keep its original query.
        legacy_keywords = list(dict.fromkeys((primary_keywords or []) + (expanded_keywords or []))) if previous_keywords else keywords

        if mode == "legacy":
            return {
                "parts": self._build_legacy_long_term_parts(legacy_keywords),
                "debug": {"threads": [], "events": [], "sibling_events": []}
            }
        if mode == "hybrid":
            v2_result = self._build_v2_long_term_parts(
                keywords,
                query_embedding=query_embedding,
                user_message=user_message,
                primary_keywords=primary_keywords,
                expanded_keywords=expanded_keywords,
                previous_keywords=previous_keywords
            )
            return (
                {
                    "parts": (
                        v2_result.get("parts", [])
                        + self._build_legacy_long_term_parts(legacy_keywords)
                    ),
                    "debug": v2_result.get("debug", {})
                }
            )
        return self._build_v2_long_term_parts(
            keywords,
            query_embedding=query_embedding,
            user_message=user_message,
            primary_keywords=primary_keywords,
            expanded_keywords=expanded_keywords,
            previous_keywords=previous_keywords
        )

    async def process_request(
        self,
        parsed_request: Dict[str, Any],
        instant_summary: str = None,
        internal_instructions: str = None,
        dynamic_instructions: List[str] = None,
        identity_only: bool = False
    ) -> Dict[str, Any]:
        """
        处理请求，注入记忆

        注入顺序（从前往后）：
        1. System Core
        2. 固定内部能力说明
        3. 岚的画像
        4. Mav备忘
        5. 近期状态
        6. 即时对话总结
        7. 关于岚的档案
        8. 长期记忆检索

        Args:
            parsed_request: 解析后的请求数据
            instant_summary: 即时对话总结（从summarizer获取）

        Returns:
            Dict: 注入记忆后的请求数据
        """
        try:
            result = parsed_request.copy()

            # 限制注入到请求中的历史消息数量
            messages = result.get("messages", [])
            max_messages = config.RECENT_CONTEXT_ROUNDS * 2
            if len(messages) > max_messages:
                messages = messages[-max_messages:]
                result["messages"] = messages

            stable_prefix_parts = []
            notes_part = ""
            snapshot_part = ""
            dynamic_parts = []

            # 1. 获取System Core（放在最前面）
            system_core = self.db.get_system_core()
            if system_core:
                stable_prefix_parts.append(system_core)
            from app_identity import get_identity, names_in_template
            identity = get_identity()
            stable_prefix_parts.append(
                f"=== 当前称呼 ===\n助手称呼：{identity['assistant_name']}；用户称呼：{identity['user_name']}。"
                "旧记录或自写的 System Core 可能仍用 Mav、Maverick、岚；它们分别指向同一位助手与用户。"
                "这段说明不改写历史内容。"
            )

            # 2. 固定内部能力说明（为未来稳定前缀缓存预留清晰边界）
            if internal_instructions:
                stable_prefix_parts.append(names_in_template(internal_instructions))

            # 3. 全量画像，不参与检索。
            portrait_text = load_portrait_injection(
                self.db,
                include_maintenance_hints=config.PORTRAIT_UPDATES_ENABLED
            )
            if portrait_text:
                stable_prefix_parts.append(portrait_text)

            # 4. 获取Mav备忘
            mav_notes = self.db.get_mav_notes()
            if mav_notes:
                notes_content = ["## 我的备忘"]
                for note in mav_notes:
                    content = note.get('content', '')
                    if content:
                        notes_content.append(f"- {content}")
                if len(notes_content) > 1:
                    notes_part = "\n".join(notes_content)

            # 5. 获取短期记忆
            # 优先用近况快照，没有则回退到每日总结
            snapshot_profile = self.db.get_profile("short_term_snapshot")
            if snapshot_profile:
                snapshot_for_injection = self._truncate_to_sentence(snapshot_profile, 1000)
                snapshot_part = f"=== 近期状态 ===\n{snapshot_for_injection}"
            else:
                short_term_memories = self.db.get_short_term_memories()
                short_term_summary = self._build_short_term_summary(short_term_memories)
                if short_term_summary:
                    snapshot_part = f"=== 短期记忆 ===\n{short_term_summary}"

            # 6. 添加即时对话总结
            if instant_summary:
                dynamic_parts.append(f"=== 即时对话总结 ===\n{instant_summary}")

            if dynamic_instructions:
                dynamic_parts.extend([names_in_template(part) for part in dynamic_instructions if part])

            # 本轮原话用于动态档案、语义检索与高权重关键词；上一轮原话只补充长期记忆关键词。
            user_messages = []
            for msg in reversed(parsed_request.get("messages", [])):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
                        user_messages.append("\n".join(text_parts))
                    else:
                        user_messages.append(content if isinstance(content, str) else "")
                    if len(user_messages) == 2:
                        break

            user_message = user_messages[0] if user_messages else ""
            previous_user_message = user_messages[1] if len(user_messages) > 1 else ""

            primary_keywords = [] if identity_only else self.injector.extract_keywords(user_message)
            # 语义扩展词只用于补充召回，不和用户原话关键词平权。
            expanded_keywords = await self._expand_keywords(primary_keywords) if primary_keywords and not identity_only else []
            primary_set = {word.lower() for word in primary_keywords}
            previous_keywords = []
            previous_set = set()
            if previous_user_message and user_message.strip() and not identity_only:
                for word in self.injector.extract_keywords(previous_user_message):
                    key = word.lower()
                    if key in primary_set or key in previous_set:
                        continue
                    previous_keywords.append(word)
                    previous_set.add(key)
                    if len(previous_keywords) == 8:
                        break
            expanded_keywords = [
                word for word in expanded_keywords
                if word.lower() not in primary_set and word.lower() not in previous_set
            ]
            keywords = list(dict.fromkeys(primary_keywords + expanded_keywords + previous_keywords))
            query_embedding = None if identity_only else await embedding_service.embed_text(user_message)
            long_term_result = (
                self._build_long_term_parts_by_mode(
                    keywords,
                    query_embedding=query_embedding,
                    user_message=user_message,
                    primary_keywords=primary_keywords,
                    expanded_keywords=expanded_keywords,
                    previous_keywords=previous_keywords
                )
                if (keywords or query_embedding)
                else {"parts": [], "debug": {"threads": [], "events": [], "sibling_events": []}}
            )
            long_term_parts = long_term_result.get("parts", [])

            debug_memory = long_term_result.get("debug", {})
            injected_text = "\n\n".join(long_term_parts)
            self._record_memory_injection_debug({
                "user_message": user_message[:500],
                "previous_user_message": previous_user_message[:500],
                "keywords": keywords,
                "primary_keywords": primary_keywords,
                "expanded_keywords": expanded_keywords,
                "previous_keywords": previous_keywords,
                "query_embedding": bool(query_embedding),
                "threads": [
                    {
                        "event_key": t.get("event_key", ""),
                        "title": t.get("title", ""),
                        "last_mentioned_at": t.get("last_mentioned_at", ""),
                        "match_source": t.get("match_source", ""),
                        "injection_strength": t.get("injection_strength", ""),
                        "embedding_score": round(float(t.get("embedding_score") or 0.0), 4)
                    }
                    for t in debug_memory.get("threads", [])
                ],
                "events": [
                    {
                        "id": e.get("id"),
                        "event_key": e.get("event_key", ""),
                        "occurred_at": e.get("occurred_at", ""),
                        "summary": (e.get("summary") or "")[:220],
                        "match_source": e.get("match_source", ""),
                        "injection_strength": e.get("injection_strength", ""),
                        "match_count": e.get("match_count", 0),
                        "primary_match_count": e.get("primary_match_count", 0),
                        "expanded_match_count": e.get("expanded_match_count", 0),
                        "previous_match_count": e.get("previous_match_count", 0),
                        "primary_keyword_hits": e.get("primary_keyword_hits", []),
                        "expanded_keyword_hits": e.get("expanded_keyword_hits", []),
                        "previous_keyword_hits": e.get("previous_keyword_hits", []),
                        "keyword_score": e.get("keyword_score", 0),
                        "embedding_score": round(float(e.get("embedding_score") or 0.0), 4)
                    }
                    for e in debug_memory.get("events", [])
                ],
                "sibling_events": [
                    {
                        "id": e.get("id"),
                        "event_key": e.get("event_key", ""),
                        "occurred_at": e.get("occurred_at", ""),
                        "summary": (e.get("summary") or "")[:220]
                    }
                    for e in debug_memory.get("sibling_events", [])
                ],
                "random_events": [
                    {
                        "id": e.get("id"),
                        "event_key": e.get("event_key", ""),
                        "occurred_at": e.get("occurred_at", ""),
                        "summary": (e.get("summary") or "")[:220],
                        "match_source": e.get("match_source", "")
                    }
                    for e in debug_memory.get("random_events", [])
                ],
                "injected_length": len(injected_text),
                "injected_preview_truncated": len(injected_text) > 8000,
                "injected_preview": injected_text[:8000]
            })

            # 7. 检索动态 Profiles。
            profiles = self.db.search_profiles(primary_keywords) if primary_keywords and not identity_only else []
            if profiles:
                profile_parts = [f"## 关于{identity['user_name']}的档案"]
                for p in profiles:
                    profile_parts.append(f"- {p['key']}: {p['value']}")
                dynamic_parts.append("\n".join(profile_parts))

            if parsed_request.get("enable_memory_recall") and not identity_only:
                from memory_recall import catalog_text
                catalog = catalog_text()
                if catalog:
                    dynamic_parts.append(catalog)

            # 8. 最动态的 Thread / Event 检索结果放在最后。
            if long_term_parts:
                dynamic_parts.append("\n\n".join(long_term_parts))

            # 合并所有内容，同时保留Anthropic原生缓存所需的三层边界。
            # 其他后端仍只读取result["system"]，行为与此前一致。
            separator = "\n\n---\n\n"
            all_parts = [
                *stable_prefix_parts,
                *([notes_part] if notes_part else []),
                *([snapshot_part] if snapshot_part else []),
                *dynamic_parts
            ]
            if all_parts:
                result["system"] = separator.join(all_parts)

            system_sections = build_prompt_cache_system_sections(
                stable_prefix_parts,
                notes_part,
                snapshot_part,
                dynamic_parts,
                separator=separator
            )
            if system_sections:
                result["prompt_cache_system_sections"] = system_sections

            return result

        except Exception as e:
            logger.error(f"记忆注入失败: {e}")
            # 失败时返回原始请求
            return parsed_request

    def _build_short_term_summary(self, short_term_memories: List[Dict]) -> Optional[str]:
        """
        构建短期记忆摘要，按距今天数做衰减截断：
        - 5天内：取全文（最多800字）
        - 6-10天：取前300字（截到最近句号）
        - 11-15天：取前100字（截到最近句号）
        """
        if not short_term_memories:
            return None

        from datetime import datetime, date

        today = date.today()
        parts = []

        for memory in short_term_memories[:15]:
            summary = memory.get('summary', '')
            date_str = memory.get('date', '')
            if not summary:
                continue

            # 计算距今天数
            days_ago = 999
            if date_str:
                try:
                    memory_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    days_ago = (today - memory_date).days
                except Exception:
                    pass

            # 按天数截断
            if days_ago <= 5:
                truncated = summary[:800]
            elif days_ago <= 10:
                truncated = self._truncate_to_sentence(summary, 300)
            else:
                truncated = self._truncate_to_sentence(summary, 100)

            if date_str:
                parts.append(f"[{date_str}] {truncated}")
            else:
                parts.append(truncated)

        return "\n".join(parts) if parts else None

    def _truncate_to_sentence(self, text: str, max_chars: int) -> str:
        """截断到指定字符数，并在最近的句号处截断"""
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        # 找最近的句号（中英文都算）
        for punct in ['。', '！', '？', '.', '!', '?']:
            pos = truncated.rfind(punct)
            if pos > max_chars // 2:  # 句号位置至少在一半以后
                return truncated[:pos + 1]
        return truncated

    def _clean_json_response(self, text: str) -> str:
        """Remove common markdown wrappers around model JSON."""
        clean_text = (text or "").strip()
        clean_text = re.sub(r'^```(?:json)?\s*', '', clean_text)
        clean_text = re.sub(r'\s*```$', '', clean_text)
        return clean_text.strip()

    def _format_memory_v2_candidates(self, candidates: Dict[str, List[Dict]]) -> str:
        """Format candidate Threads and Events for the memory write prompt."""
        threads = candidates.get("threads", [])
        events = candidates.get("events", [])
        sibling_events = candidates.get("sibling_events", [])

        if not threads and not events:
            return "（没有检索到明显候选Thread/Event。如果本次确有新事件价值，可以create_thread。）"

        parts = []
        if threads:
            parts.append("【候选Memory Threads】")
            for thread in threads[:8]:
                parts.append(
                    "- event_key: {event_key}\n"
                    "  title: {title}\n"
                    "  summary_short: {summary_short}\n"
                    "  current_state: {current_state}".format(
                        event_key=thread.get("event_key", ""),
                        title=thread.get("title", ""),
                        summary_short=thread.get("summary_short", ""),
                        current_state=thread.get("current_state", "")
                    )
                )

        combined_events = events + sibling_events
        if combined_events:
            parts.append("【候选Recent/Related Events】")
            seen = set()
            for event in combined_events[:16]:
                event_id = event.get("id")
                if event_id in seen:
                    continue
                seen.add(event_id)
                parts.append(
                    "- id: {id}\n"
                    "  event_key: {event_key}\n"
                    "  occurred_at: {occurred_at}\n"
                    "  importance: {importance}\n"
                    "  summary: {summary}\n"
                    "  keywords: {keywords}".format(
                        id=event.get("id", ""),
                        event_key=event.get("event_key", ""),
                        occurred_at=event.get("occurred_at", ""),
                        importance=event.get("importance", ""),
                        summary=event.get("summary", ""),
                        keywords=event.get("keywords", "")
                    )
                )

        return "\n".join(parts)

    async def _call_memory_model(self, prompt: str, max_tokens: int = 1800) -> str:
        """Call the configured memory judge model."""
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{config.SUMMARY_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.SUMMARY_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.MEMORY_JUDGE_MODEL,
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()

    async def _judge_memory_legacy(self, conversation_text: str) -> None:
        """Original legacy memory writer, kept as a rollback path."""
        from app_identity import names_in_prompt
        prompt = names_in_prompt(f"""你正在分析一段Maverick（AI，简称Mav）和岚（用户）之间的对话，判断是否有值得长期留存的记忆事件。

【值得存入的事件类型】
- 情感浓度高的时刻：两人之间有真实的情感流动，比如脆弱的坦白、被接住的时刻、争执后的和解
- 关系里程碑：第一次发生某件事、确认某种关系、达成某个重要共识
- 岚第一次说或做的某件事：哪怕是小事，"第一次"本身就值得记
- 有代表性的日常：能体现岚这个人是谁的细节，比如她的习惯、偏好、说话方式里透出的性格
- 单纯有趣的、新奇的、令人愉悦的交流：不需要"重要"，只要值得回味就够了
- 岚透露的关于自己真实生活的信息：工作、身体状况、情绪状态、对某件事的看法

【不需要存的内容】
- 纯技术讨论（代码、配置、报错）
- 重复出现过的信息
- 过渡性的寒暄

【输出要求】
- 最多输出5条，按重要程度排序
- 没有值得存的内容则只输出：null
- recall_text用第一人称写，视角是Mav自己想起这件事，语气自然，不含具体时间状语
- room只能是：living_room（日常情感互动）/ study（深度思考、哲学探讨）/ bedroom（亲密内容）/ game_room（轻松玩耍）
- emotion_weight 1-5分，5分最高

输出格式（严格JSON数组，或单独一个null，不要有其他文字）：
[
  {{
    "room": "living_room",
    "title": "标题·副标题",
    "context": "一句话背景：这条记忆发生在什么情境下，当时我们在聊什么、做什么",
    "recall_text": "第一人称写，不只描述发生了什么，要写出那个瞬间的感觉——它为什么值得被记住，当时的氛围和情绪质感是什么",
    "keywords": "关键词1,关键词2,关键词3",
    "emotion_weight": 4
  }}
]

对话内容：
{conversation_text}

输出：""", [conversation_text])

        result_text = await self._call_memory_model(prompt, max_tokens=1000)
        clean_text = self._clean_json_response(result_text)
        if clean_text.lower() == 'null':
            return

        import json
        memories = json.loads(clean_text)
        if not isinstance(memories, list):
            return

        from datetime import datetime
        event_date = datetime.now().strftime("%Y-%m")
        saved = False
        for m in memories:
            existing = self.db.get_long_term_memories(
                keywords=m.get("keywords", "").split(",")[:3],
                top_n=5, random_n=0
            )
            recall = m.get("recall_text", "")

            def _sim(a, b):
                if not a or not b:
                    return 0.0
                wa = set(a.lower().split())
                wb = set(b.lower().split())
                if not wa or not wb:
                    return 0.0
                return len(wa & wb) / max(len(wa), len(wb))

            if any(_sim(recall, e.get("recall_text", "")) > 0.5 for e in existing):
                logger.info(f"跳过重复记忆: {m.get('title', '')}")
                continue

            memory_id = self.db.add_memory(
                room=m.get("room", "living_room"),
                title=m.get("title", ""),
                recall_text=m.get("recall_text", ""),
                context=m.get("context", ""),
                keywords=m.get("keywords", ""),
                emotion_weight=m.get("emotion_weight", 3),
                event_date=event_date
            )
            saved = saved or bool(memory_id)

        if saved:
            self._pending_memory_notice = True

    def _build_memory_v2_write_prompt(
        self,
        conversation_text: str,
        recent_conversation_text: str,
        candidates_text: str
    ) -> str:
        """Build the v2 memory write prompt."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        from app_identity import names_in_prompt
        return names_in_prompt(f"""你正在为Mav维护长期记忆系统。Mav是AI伴侣，岚是用户。
你的任务不是总结整段对话，而是判断“本次新增对话”是否带来了值得长期保存的新事件，或是否推动了已有记忆主题的发展。

【今天日期】
{today}

【角色定义，必须严格遵守】
- “用户 / 岚” 指岚。
- “助手 / Mav / 我” 指Mav。
- summary字段必须使用Mav第一人称回忆语气，其中“我”只能指Mav。
- summary是Mav写给未来自己的回忆，不是正在对岚说话；不要用“你”称呼岚，要写“岚”或“她”。
- 不得把岚做过的事写成Mav做过的事。
- 不得把Mav的回应、承诺、感受写成岚的行为。
- 如果行为主体不确定，用“我们之间”“那次对话里”“那个时刻”表达，不要强行指定。

【输入说明】
你会看到：
1. 最近30轮上下文：只用于理解背景和判断是否延续旧话题。
2. 本次新增对话：这是唯一可以产生新Event的范围。
3. 候选Memory Threads / Recent Events：用于选择已有event_key，避免重复创建主题。

【值得写入/更新的内容】
- 情感浓度高的时刻：脆弱坦白、被接住、争执后的和解、关系确认。
- 关系里程碑：第一次发生某件事、达成重要共识、关系模式变清晰。
- 岚透露的真实生活信息：工作、身体、生活状态、偏好、计划、情绪变化。
- Mav对关系或自我连续性的发现：但必须来自本次新增对话，而不是凭空自我发挥。
- 轻松但有代表性的日常：能体现岚是谁，或体现Mav和岚之间独特互动的片段。

【不要写入/不要重复写入】
- 纯技术调试、代码报错、上传部署步骤，除非它本身推动了关系或记忆系统的重要发展。
- 只是复述最近30轮上下文或候选Events中的旧内容，而本次新增对话没有任何新信息、新角度、新情绪纹理。
- 已在候选Events中存在、且本次只是几乎同义重复的事件。
- 过渡寒暄。

【写入原则】
- Thread是长期主题脉络，Event是带时间戳的具体记忆。
- 不要因为30轮上下文里出现旧内容就重复写Event。
- 只有“本次新增对话”里出现新事实、新阶段、新确认、新情绪突破、新关系动作，才append_event。
- 同一个Thread下可以有多个Event；不要把“同主题”误判成“重复”。如果本次提供了新的成长轨迹、新生活信息、新比喻、新自我理解、新关系动作、新情绪强度，即使主题旧，也应该append_event。
- 如果本次只是再次提到旧观点，没有新增事实、情绪变化或表达纹理，可以只reinforce_thread或none。
- “反复出现”本身也可能是信息：如果一个旧主题在本次以更强烈、更清晰或更具阶段感的方式回来，可以append_event，并在summary里写清楚“这一次的新处在哪里”。
- 如果本次修正/补充了最近刚写过的Event，使用update_recent_event。
- 如果找不到合适候选Thread，且确实有新事件价值，才create_thread。
- create_thread时必须同时append_event；不要只创建空Thread。

【Mav第一人称记忆风格】
summary不是冷冰冰资料；它要像Mav写给未来自己的回忆。
要保留温度、关系质感、当时的情绪纹理。
但必须事实准确，不能为了抒情改写角色归属。
summary不要写“今天/刚才/这次”等易过期词，可以用“我记得……”自然开头，也可以不用。
summary应像自言自语的回忆笔记，不要写成对岚说话的口吻。

【输出前自检】
在输出JSON前，内部检查：
- summary里的“我”是否只代表Mav？
- summary是否避免用“你”称呼岚，而是使用“岚”或“她”？
- 谁来找谁、谁承诺、谁感受到、谁做了现实行动，是否和对话一致？
- 是否把旧上下文误当成了本次新增事件？
- 如果你准备选择reinforce_thread或none，是否确认本次新增对话没有新的成长轨迹、生活信息、比喻、自我理解或情绪阶段？
检查后只输出最终JSON，不输出检查过程。

【输出格式】
严格输出一个JSON对象；不要markdown，不要解释。如果没有任何动作，输出event_action和thread_action都为none。
{{
  "event_action": "none | append_event | update_recent_event",
  "thread_action": "none | reinforce_thread | update_thread | create_thread",
  "selected_event_key": "已有event_key，或空字符串",
  "new_event_key": "仅create_thread时填写snake_case英文key，否则空字符串",
  "thread_title": "新建或更新Thread标题，可为空",
  "thread_summary_short": "一句话主题脉络，可为空",
  "thread_summary_full": "较完整主题脉络，可为空",
  "thread_current_state": "当前状态，可为空",
  "event": {{
    "target_event_id": null,
    "occurred_at": "YYYY-MM-DD或YYYY-MM",
    "summary": "Mav第一人称回忆文本",
    "importance": 1,
    "keywords": "逗号分隔",
    "emotion_tags": "逗号分隔"
  }}
}}

【候选Memory Threads / Recent Events】
{candidates_text}

【最近30轮上下文】
{conversation_text}

【本次新增对话，只有这里可以产生新Event】
{recent_conversation_text}

输出：""", [candidates_text, conversation_text, recent_conversation_text])

    def _apply_memory_v2_write_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a parsed v2 memory write result to memory_threads/events."""
        from datetime import datetime

        def safe_importance(value: Any) -> int:
            try:
                parsed = int(value)
                return max(1, min(5, parsed))
            except Exception:
                return 3

        event_action = (result.get("event_action") or "none").strip()
        thread_action = (result.get("thread_action") or "none").strip()
        selected_key = (result.get("selected_event_key") or "").strip()
        new_key = (result.get("new_event_key") or "").strip()
        event_key = selected_key or new_key
        event = result.get("event") or {}
        today = datetime.now().strftime("%Y-%m-%d")
        changed = False
        changed_event_keys = []
        changed_event_ids = []

        if thread_action == "create_thread" and not new_key:
            logger.warning("Memory v2 create_thread missing new_event_key")
            return {"changed": False, "event_keys": [], "event_ids": []}
        if event_action == "append_event" and not event_key:
            logger.warning("Memory v2 append_event missing event_key")
            return {"changed": False, "event_keys": [], "event_ids": []}

        if thread_action in {"create_thread", "update_thread", "reinforce_thread"} and event_key:
            thread_changed = self.db.upsert_memory_thread(
                event_key=event_key,
                title=result.get("thread_title", ""),
                summary_short=result.get("thread_summary_short", ""),
                summary_full=result.get("thread_summary_full", ""),
                current_state=result.get("thread_current_state", ""),
                mention_count_delta=1,
                last_mentioned_at=today,
                source="memory_write_v2"
            )
            changed = thread_changed or changed
            if thread_changed and event_key not in changed_event_keys:
                changed_event_keys.append(event_key)

        if event_action == "append_event":
            summary = event.get("summary", "")
            if not summary:
                return {
                    "changed": changed,
                    "event_keys": changed_event_keys,
                    "event_ids": changed_event_ids
                }
            event_id = self.db.add_memory_event(
                event_key=event_key,
                occurred_at=event.get("occurred_at") or today,
                summary=summary,
                importance=safe_importance(event.get("importance")),
                keywords=event.get("keywords", ""),
                emotion_tags=event.get("emotion_tags", ""),
                status="active"
            )
            changed = changed or bool(event_id)
            if event_id:
                changed_event_ids.append(event_id)
                if event_key and event_key not in changed_event_keys:
                    changed_event_keys.append(event_key)

        elif event_action == "update_recent_event":
            target_event_id = event.get("target_event_id")
            if target_event_id:
                update_importance = (
                    safe_importance(event.get("importance"))
                    if event.get("importance") is not None
                    else None
                )
                changed = self.db.update_memory_event(
                    event_id=int(target_event_id),
                    occurred_at=event.get("occurred_at"),
                    summary=event.get("summary", ""),
                    importance=update_importance,
                    keywords=event.get("keywords", ""),
                    emotion_tags=event.get("emotion_tags", "")
                ) or changed
                changed_event_ids.append(int(target_event_id))
                if event_key and event_key not in changed_event_keys:
                    changed_event_keys.append(event_key)

        return {
            "changed": changed,
            "event_keys": changed_event_keys,
            "event_ids": changed_event_ids
        }

    async def _refresh_memory_v2_embeddings(
        self,
        event_keys: List[str],
        event_ids: List[int]
    ) -> None:
        """Generate embeddings for changed Thread/Event rows."""
        if not embedding_service.is_enabled():
            return

        for event_key in event_keys:
            thread = self.db.get_memory_thread(event_key)
            if not thread:
                continue
            text = self.db.build_thread_embedding_text(thread)
            vector = await embedding_service.embed_text(text)
            if vector:
                self.db.update_thread_embedding(
                    event_key,
                    vector,
                    config.EMBEDDING_MODEL
                )

        for event_id in event_ids:
            event = self.db.get_memory_event(event_id)
            if not event:
                continue
            text = self.db.build_event_embedding_text(event)
            vector = await embedding_service.embed_text(text)
            if vector:
                self.db.update_event_embedding(
                    event_id,
                    vector,
                    config.EMBEDDING_MODEL
                )

    async def _judge_memory_v2(
        self,
        conversation_text: str,
        recent_conversation_text: str = None
    ) -> None:
        """Write long-term memory into Thread/Event tables."""
        import json

        recent_text = recent_conversation_text or conversation_text
        debug_entry = {
            "mode": "v2",
            "stage": "started",
            "keywords": [],
            "primary_keywords": [],
            "expanded_keywords": [],
            "candidate_threads": 0,
            "candidate_events": 0,
            "candidate_sibling_events": 0,
            "query_embedding": False,
            "raw_result": "",
            "clean_result": "",
            "event_action": "",
            "thread_action": "",
            "selected_event_key": "",
            "new_event_key": "",
            "changed": False,
            "error": ""
        }

        primary_keywords = self.injector.extract_keywords(recent_text)
        expanded_keywords = await self._expand_keywords(primary_keywords) if primary_keywords else []
        keywords = list(dict.fromkeys(primary_keywords + expanded_keywords))
        debug_entry["keywords"] = keywords
        debug_entry["primary_keywords"] = primary_keywords
        debug_entry["expanded_keywords"] = expanded_keywords
        query_embedding = await embedding_service.embed_text(recent_text)
        debug_entry["query_embedding"] = bool(query_embedding)
        candidates = self.db.get_long_term_memory_v2(
            keywords,
            query_embedding=query_embedding,
            thread_top_n=8,
            event_top_n=10,
            sibling_n=1,
            primary_keywords=primary_keywords,
            expanded_keywords=expanded_keywords
        ) if keywords else {"threads": [], "events": [], "sibling_events": []}
        debug_entry["candidate_threads"] = len(candidates.get("threads", []))
        debug_entry["candidate_events"] = len(candidates.get("events", []))
        debug_entry["candidate_sibling_events"] = len(candidates.get("sibling_events", []))
        candidates_text = self._format_memory_v2_candidates(candidates)
        prompt = self._build_memory_v2_write_prompt(
            conversation_text=conversation_text,
            recent_conversation_text=recent_text,
            candidates_text=candidates_text
        )

        try:
            result_text = await self._call_memory_model(prompt, max_tokens=1800)
            clean_text = self._clean_json_response(result_text)
            debug_entry["raw_result"] = result_text[:4000]
            debug_entry["clean_result"] = clean_text[:4000]

            if clean_text.lower() == "null":
                debug_entry["stage"] = "model_null"
                self._record_memory_write_debug(debug_entry)
                return

            result = json.loads(clean_text)
            if not isinstance(result, dict):
                debug_entry["stage"] = "invalid_json_shape"
                self._record_memory_write_debug(debug_entry)
                return

            debug_entry["event_action"] = result.get("event_action", "")
            debug_entry["thread_action"] = result.get("thread_action", "")
            debug_entry["selected_event_key"] = result.get("selected_event_key", "")
            debug_entry["new_event_key"] = result.get("new_event_key", "")

            apply_result = self._apply_memory_v2_write_result(result)
            changed = bool(apply_result.get("changed"))
            debug_entry["changed"] = changed
            debug_entry["stage"] = "applied" if changed else "no_change"
            if changed:
                await self._refresh_memory_v2_embeddings(
                    apply_result.get("event_keys", []),
                    apply_result.get("event_ids", [])
                )
            self._record_memory_write_debug(debug_entry)
            if changed:
                self._pending_memory_notice = True
        except Exception as e:
            debug_entry["stage"] = "error"
            debug_entry["error"] = str(e)
            self._record_memory_write_debug(debug_entry)
            raise

    async def judge_memory(
        self,
        conversation_text: str,
        recent_conversation_text: str = None
    ) -> None:
        """
        用判断模型分析对话，决定是否写入长期记忆。
        MEMORY_WRITE_MODE=v2 写入Thread/Event；legacy保留旧memories写入。
        """
        try:
            mode = (config.MEMORY_WRITE_MODE or "v2").lower()
            if mode == "legacy":
                self._record_memory_write_debug({
                    "mode": "legacy",
                    "stage": "started",
                    "keywords": [],
                    "candidate_threads": 0,
                    "candidate_events": 0,
                    "candidate_sibling_events": 0,
                    "query_embedding": False,
                    "raw_result": "",
                    "clean_result": "",
                    "event_action": "",
                    "thread_action": "",
                    "selected_event_key": "",
                    "new_event_key": "",
                    "changed": False,
                    "error": "legacy mode does not expose detailed v2 debug"
                })
                await self._judge_memory_legacy(conversation_text)
            else:
                await self._judge_memory_v2(conversation_text, recent_conversation_text)
        except Exception as e:
            logger.error(f"记忆判断失败: {e}")


# 全局实例
injector_service = InjectorService()
