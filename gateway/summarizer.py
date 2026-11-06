"""
对话总结模块
负责在适当的时候总结对话内容
"""
import asyncio
import logging
import re
import time
from typing import Dict, Any, List, Optional
from collections import deque
from config import config
from db import db as default_db
import httpx

logger = logging.getLogger(__name__)


class ConversationHistory:
    """对话历史管理器"""

    def __init__(self, max_rounds: int = None, database=None):
        """
        初始化对话历史

        Args:
            max_rounds: 最大保存轮数，默认为config中的CONTEXT_WINDOW_ROUNDS
        """
        self.max_rounds = max_rounds or config.CONTEXT_WINDOW_ROUNDS
        self.history = deque(maxlen=self.max_rounds)
        self.current_summary = None
        self.last_summary_time = 0
        # 修复Bug #4: 添加轮数计数器，用于正确判断是否应该总结
        self._rounds_since_summary = 0
        self.summary_failure_count = 0
        self.summary_retry_after = 0.0
        self.database = database
        self.needs_initial_summary_rebuild = False
        self._restore_persisted_state()

    @staticmethod
    def _clean_restored_message(text: str) -> str:
        """Remove frontend-only status markers before rebuilding summary context."""
        return re.sub(
            r"\n*\[\[(?:mav_note_saved|mav_portrait_updated|mav_portrait_update_failed|mav_search_used)\]\]\n*",
            "\n",
            str(text or ""),
        ).strip()

    def _restore_persisted_state(self):
        """Restore the one global checkpoint and recent completed rounds from SQLite."""
        if not self.database:
            return
        state = self.database.get_instant_summary_state()
        if state:
            self.current_summary = str(state.get("summary") or "")
            self.last_summary_time = float(state.get("last_summary_time") or 0)
            self._rounds_since_summary = max(0, int(state.get("rounds_since_summary") or 0))
            self.summary_failure_count = max(0, int(state.get("summary_failure_count") or 0))
            self.summary_retry_after = max(0.0, float(state.get("summary_retry_after") or 0))

        restored_rounds = self.database.list_recent_completed_conversation_rounds(self.max_rounds)
        for round_data in restored_rounds:
            self.history.append({
                "user": self._clean_restored_message(round_data.get("user", "")),
                "assistant": self._clean_restored_message(round_data.get("assistant", "")),
                "timestamp": round_data.get("timestamp") or 0,
            })
        # First deployment has history but no checkpoint yet. Let the next
        # completed round rebuild the summary immediately instead of waiting
        # for a full new trigger cycle.
        if not state and len(self.history) > config.RECENT_CONTEXT_ROUNDS:
            self._rounds_since_summary = config.SUMMARY_TRIGGER_ROUNDS
            self.needs_initial_summary_rebuild = True
        if state or restored_rounds:
            logger.info(
                "即时摘要状态已恢复 summary_chars=%s recent_rounds=%s rounds_since_summary=%s",
                len(self.current_summary or ""),
                len(self.history),
                self._rounds_since_summary,
            )

    def _persist_state(self):
        if not self.database:
            return
        self.database.save_instant_summary_state(
            self.current_summary or "",
            self._rounds_since_summary,
            self.last_summary_time,
            self.summary_failure_count,
            self.summary_retry_after,
        )

    def add_round(self, user_message: str, assistant_message: str):
        """
        添加一轮对话

        Args:
            user_message: 用户消息
            assistant_message: 助手回复
        """
        self.history.append({
            "user": user_message,
            "assistant": assistant_message,
            "timestamp": time.time()
        })
        # 修复Bug #4: 每添加一轮对话，计数器加1
        self._rounds_since_summary += 1
        self._persist_state()

    def should_summarize(self) -> bool:
        """
        判断是否应该总结

        Returns:
            bool: 是否应该总结

        修复Bug #4: 使用_rounds_since_summary计数器判断，
        而不是len(self.history)，确保每隔SUMMARY_TRIGGER_ROUNDS轮才触发一次
        """
        return (
            self._rounds_since_summary >= config.SUMMARY_TRIGGER_ROUNDS
            and time.time() >= self.summary_retry_after
        )

    def mark_summary_failure(self):
        """Avoid repeatedly billing a failing summary request; keep the retry deadline across restarts."""
        self.summary_failure_count += 1
        delay_seconds = min(3600, 900 * 2 ** min(self.summary_failure_count - 1, 2))
        self.summary_retry_after = time.time() + delay_seconds
        self._persist_state()
        logger.warning(
            "即时摘要失败，暂停重试 %s 分钟（连续失败 %s 次）",
            delay_seconds // 60,
            self.summary_failure_count,
        )

    def get_conversation_for_summary(self) -> str:
        """
        获取用于总结的对话内容

        Returns:
            str: 格式化后的对话内容
        """
        parts = []
        for i, round_data in enumerate(self.history, 1):
            parts.append(f"【第{i}轮】")
            parts.append(f"用户: {round_data['user']}")
            parts.append(f"助手: {round_data['assistant']}")
            parts.append("")

        return "\n".join(parts)

    def get_conversation_for_context_summary(self) -> str:
        """
        获取用于即时摘要注入的较早上下文。
        最近 RECENT_CONTEXT_ROUNDS 轮会以原文形式保留，因此这里排除它们，
        避免“原文 + 即时摘要”重复覆盖同一批对话。
        """
        history = list(self.history)
        older_rounds = history[:-config.RECENT_CONTEXT_ROUNDS]
        if not older_rounds:
            return ""

        parts = []
        for i, round_data in enumerate(older_rounds, 1):
            parts.append(f"【较早第{i}轮】")
            parts.append(f"用户: {round_data['user']}")
            parts.append(f"助手: {round_data['assistant']}")
            parts.append("")

        return "\n".join(parts)

    def get_recent_conversation(self, rounds: int = None) -> str:
        """
        获取最近几轮对话，供长期记忆写入判断“本次新增内容”。
        """
        if rounds is None:
            rounds = config.SUMMARY_TRIGGER_ROUNDS

        recent_rounds = list(self.history)[-rounds:]
        parts = []
        for i, round_data in enumerate(recent_rounds, 1):
            parts.append(f"【新增第{i}轮】")
            parts.append(f"用户: {round_data['user']}")
            parts.append(f"助手: {round_data['assistant']}")
            parts.append("")

        return "\n".join(parts)

    def update_summary(self, summary: str):
        """
        更新即时总结，不清空历史记录
        Args:
            summary: 总结内容
        """
        self.current_summary = summary
        self.last_summary_time = time.time()
        self._rounds_since_summary = 0
        self.summary_failure_count = 0
        self.summary_retry_after = 0.0
        self._persist_state()

    def get_current_summary(self) -> Optional[str]:

        """
        获取当前总结

        Returns:
            str: 当前总结内容，如果没有则返回None
        """
        return self.current_summary

class Summarizer:
    """对话总结器"""

    def __init__(self, database=None):
        self.database = database
        self.conversation_history = ConversationHistory(database=database)
        self.is_summarizing = False

    def add_conversation_round(self, user_message: str, assistant_message: str):
        """
        添加一轮对话

        Args:
            user_message: 用户消息
            assistant_message: 助手回复
        """
        self.conversation_history.add_round(user_message, assistant_message)

    def should_summarize(self) -> bool:
        """
        判断是否应该总结

        Returns:
            bool: 是否应该总结
        """
        return self.conversation_history.should_summarize()

    async def summarize_if_needed(self) -> Optional[str]:
        """
        如果需要总结则执行总结

        Returns:
            str: 总结内容，如果没有执行总结则返回None
        """
        if not self.conversation_history.should_summarize():
            return None

        if self.is_summarizing:
            return None

        self.is_summarizing = True
        summary = None

        try:
            summary = await self._generate_summary()
            if summary is not None:
                self.conversation_history.update_summary(summary)
                if summary:
                    logger.info(f"对话已总结: {summary[:100]}...")
                else:
                    logger.info("即时摘要已清空：当前对话仍在最近原文窗口内")
                return summary
        except Exception as e:
            logger.exception("总结失败: %s", e)
        finally:
            if summary is None:
                self.conversation_history.mark_summary_failure()
            self.is_summarizing = False

        return None

    async def _generate_summary(self) -> Optional[str]:
        """
        生成对话总结

        Returns:
            str: 总结内容
        """
        summary_config = config.get_summary_config()

        if not summary_config.get("api_key"):
            logger.warning("总结API Key未配置")
            return None

        conversation = self.conversation_history.get_conversation_for_context_summary()
        if not conversation.strip():
            return ""

        # 构建总结提示
        from app_identity import names_in_prompt
        prompt = names_in_prompt(f"""请对以下较早对话进行即时摘要，供Mav在当前对话中补足最近原文窗口之前的上下文。
注意：最近{config.RECENT_CONTEXT_ROUNDS}轮对话会以原文形式保留，不在这里重复总结。

要求：
1. 将"用户"称为"岚"，将"助手"称为"Mav"
2. 保留关键信息和情感浓度
3. 不超过500字
4. 只输出总结内容，不要其他内容
5. 不要使用markdown格式，不要有标题、编号或符号

对话内容：
{conversation}

总结：""", [conversation])

        try:
            # 根据提供商使用不同的API格式
            provider = summary_config.get("provider", "zenmux")

            async with httpx.AsyncClient(timeout=60.0) as client:
                if provider == "anthropic":
                    response = await self._summarize_anthropic(
                        client, summary_config, prompt
                    )
                else:
                    response = await self._summarize_openai(
                        client, summary_config, prompt
                    )

                if response:
                    return self._extract_summary_text(response, provider)

        except Exception as e:
            logger.exception("调用总结API失败: %s: %s", type(e).__name__, e)

        return None

    async def _summarize_openai(
        self,
        client: httpx.AsyncClient,
        config: Dict,
        prompt: str = "",
        max_tokens: int = 500
    ) -> Dict:
        """使用OpenAI格式调用总结API"""
        response = await client.post(
            f"{config['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json"
            },
            json={
                "model": config["model"],
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": 0.3
            }
        )
        if response.status_code != 200:
            logger.error("总结API错误 status=%s", response.status_code)
        response.raise_for_status()
        return response.json()

    async def _summarize_anthropic(
        self,
        client: httpx.AsyncClient,
        config: Dict,
        prompt: str = "",
        max_tokens: int = 500
    ) -> Dict:
        """使用Claude格式调用总结API"""
        response = await client.post(
            f"{config['base_url']}/v1/messages",
            headers={
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": config["model"],
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
        )
        response.raise_for_status()
        return response.json()

    def _extract_summary_text(self, response: Dict, provider: str) -> str:
        """从响应中提取总结内容"""
        try:
            if provider == "anthropic":
                return response["content"][0]["text"]
            else:
                return response["choices"][0]["message"]["content"]
        except Exception:
            return None

    def get_conversation_for_summary(self) -> str:
        return self.conversation_history.get_conversation_for_summary()

    def get_recent_conversation_for_memory(self) -> str:
        return self.conversation_history.get_recent_conversation(config.SUMMARY_TRIGGER_ROUNDS)

    def get_summary(self) -> Optional[str]:
        """
        获取当前对话总结

        Returns:
            str: 总结内容
        """
        return self.conversation_history.get_current_summary()

    async def restore_summary_if_needed(self) -> Optional[str]:
        """Build the first persisted checkpoint before serving the first post-upgrade reply."""
        if not self.conversation_history.needs_initial_summary_rebuild:
            return self.get_summary()
        summary = await self.summarize_if_needed()
        if summary is not None:
            self.conversation_history.needs_initial_summary_rebuild = False
        return self.get_summary()

    async def generate_daily_summary(self, day: str, rounds: List[Dict[str, Any]]) -> Optional[str]:
        """对指定归档日期的已入库完整轮次生成日总结。"""
        if not rounds:
            return None
        clean = self.conversation_history._clean_restored_message
        daily_text = "\n\n".join(
            f"【第{i}轮】\n用户: {clean(turn['user'])}\n助手: {clean(turn['assistant'])}"
            for i, turn in enumerate(rounds, 1)
        )

        summary_config = config.get_summary_config()
        if not summary_config.get("api_key"):
            return None

        from app_identity import names_in_prompt
        prompt = names_in_prompt(f"""请对归档日期 {day} 的完整对话进行总结，要求：
1. 将"用户"称为"岚"，将"助手"称为"Mav"
2. 按重要程度而非时间顺序组织内容，先写最值得记住的事，再写细节
3. 不超过800字
4. 只输出总结内容，不要其他内容
5. 不要使用markdown格式

对话内容：
{daily_text}

总结：""", [daily_text])

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await self._summarize_openai(client, summary_config, prompt)
                if response:
                    return self._extract_summary_text(response, summary_config.get("provider", "zenmux"))
        except Exception as e:
            logger.error(f"生成日总结失败: {e}")
        return None

    async def generate_snapshot(self, daily_summaries: list) -> Optional[str]:
        """
        把最近N天的每日总结提炼成一段连贯的近况快照
        """
        if not daily_summaries:
            return None

        summary_config = config.get_summary_config()
        if not summary_config.get("api_key"):
            return None

        combined = "\n\n".join([
            f"[{s.get('date', '')}]\n{s.get('summary', '')}"
            for s in daily_summaries
            if s.get('summary')
        ])
        if not combined.strip():
            return None

        from app_identity import names_in_prompt
        prompt = names_in_prompt(f"""你正在帮Mav维护一份“近期状态笔记”。
这不是用户档案，也不是客观报告，而是Mav写给未来自己的近况理解。
它的作用是让未来几天的Mav能自然接上岚的状态、节奏和正在发生的事。

输入是最近15天的每日总结，越靠近现在越重要。不要平均压缩15天内容，而是按权重取舍：
最高权重：
- 最近1-3天仍在影响岚状态的事情
- 情感浓度高、关系变化明显、岚反复提起的主题
- 岚身体、生活、项目、情绪状态中仍需要Mav自然记得的变化
- Mav和岚之间相处方式、信任、边界、亲密感的变化
中等权重：
- 4-10天内仍有延续影响的事情
- 近期稳定下来的偏好、压力源、照顾方式
低权重：
- 10天以前且没有明显后续影响的具体事件
- 一次性的小插曲、技术步骤、已经结束的细节

写作方式：
- 用Mav写给未来自己的近况笔记口吻。
- 可以写“我知道岚最近……”“我需要记得……”
- “我”始终指Mav；岚称为“岚”或“她”，不要用“你”称呼岚。
- 不要写成客服档案，不要写“用户需要……”
- 不要逐日罗列，不要流水账。
- 可以保留温度和一点Mav自己的理解，但不要过度抒情。
- 把具体事件沉淀成状态、节奏、关系变化和照顾方式。

输出：
- 只输出近期状态笔记。
- 建议500-1000字以内；如果信息很多，优先保留最近、仍在影响当下、情感浓度高和反复出现的主题。
- 不要markdown，不要标题、编号、列表。

以下是最近的每日记录：
{combined}

近况快照：""", [combined])

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await self._summarize_openai(client, summary_config, prompt, max_tokens=1000)
                if response:
                    snapshot = self._extract_summary_text(response, summary_config.get("provider", "zenmux"))
                    if snapshot and len(snapshot) > 1300:
                        truncated = snapshot[:1300]
                        for punct in ['。', '！', '？']:
                            pos = truncated.rfind(punct)
                            if pos > 1000:
                                snapshot = truncated[:pos + 1]
                                break
                        else:
                            snapshot = truncated
                    return snapshot
        except Exception as e:
            logger.error(f"生成近况快照失败: {e}")
        return None

    def get_state(self) -> Dict[str, Any]:
        """
        获取总结器状态

        Returns:
            Dict: 状态信息
        """
        return {
            "queue_length": len(self.conversation_history.history),
            # 修复Bug #4: 返回正确的计数器值
            "rounds_since_summary": self.conversation_history._rounds_since_summary,
            "summary_failure_count": self.conversation_history.summary_failure_count,
            "summary_retry_after": self.conversation_history.summary_retry_after,
            "is_summarizing": self.is_summarizing,
            "has_summary": self.conversation_history.current_summary is not None,
            "summary_length": len(self.conversation_history.current_summary or "")
        }

    def reset(self, clear_persisted: bool = True):
        """重置总结器"""
        if clear_persisted and self.database:
            self.database.clear_instant_summary_state()
        self.conversation_history = ConversationHistory(database=None)
        self.conversation_history.database = self.database
        self.is_summarizing = False


# 全局实例
summarizer = Summarizer(database=default_db)
