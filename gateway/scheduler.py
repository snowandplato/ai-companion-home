"""
定时任务模块 - Background Task Scheduler
负责管理后台任务，如定时总结触发和每日归档
"""

import asyncio
import logging
import time
from typing import Optional
from datetime import date, timedelta

from config import config
from summarizer import summarizer
from db import db

logger = logging.getLogger(__name__)
ARCHIVE_LOOKBACK_DAYS = 7
ARCHIVE_CHECK_SECONDS = 60
ARCHIVE_RETRY_SECONDS = 30 * 60


class TaskScheduler:
    """任务调度器"""

    def __init__(self):
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._check_interval = 1  # 每秒检查一次
        self._next_archive_check = 0.0
        self._archive_lock = asyncio.Lock()

    async def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return

        self._next_archive_check = 0.0
        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        logger.info("任务调度器已启动")

    async def stop(self):
        """停止调度器"""
        self._running = False

        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        logger.info("任务调度器已停止")

    async def _check_loop(self):
        """检查循环"""
        try:
            while self._running:
                await self._process_tasks()
                await asyncio.sleep(self._check_interval)

        except asyncio.CancelledError:
            logger.info("检查循环被取消")
            raise
        except Exception as e:
            logger.error(f"检查循环异常: {e}")

    async def _process_tasks(self):
        """处理任务"""
        try:
            # 检查是否需要触发总结
            if summarizer.should_summarize():
                logger.info(
                    f"触发定时总结 (距上次 {summarizer.get_state()['rounds_since_summary']} 轮)"
                )
                await summarizer.summarize_if_needed()

            if time.monotonic() >= self._next_archive_check:
                self._next_archive_check = time.monotonic() + ARCHIVE_CHECK_SECONDS
                await self.daily_archive()

        except Exception as e:
            logger.error(f"处理任务时出错: {e}")

    async def daily_archive(self, today: Optional[date] = None):
        """补齐最近已结束的日期，独立恢复未完成的快照任务。"""
        if not config.get_summary_config().get("api_key"):
            return
        async with self._archive_lock:
            today = today or date.today()
            start = today - timedelta(days=ARCHIVE_LOOKBACK_DAYS)
            jobs = db.list_daily_archive_jobs(start.isoformat(), today.isoformat())
            for offset in range(ARCHIVE_LOOKBACK_DAYS):
                day = start + timedelta(days=offset)
                key = day.isoformat()
                job = jobs.get(key)
                if job and (job["status"] != "retry" or job["retry_after"] > time.time()):
                    continue
                try:
                    rounds = db.list_daily_archive_rounds(day)
                    if not rounds:
                        db.save_daily_archive_job(key, "empty")
                        continue
                    summary = await summarizer.generate_daily_summary(key, rounds)
                    if not summary or not summary.strip():
                        raise RuntimeError("未生成有效日总结")
                    if db.complete_daily_archive(key, summary):
                        logger.info("每日归档完成 date=%s", key)
                except Exception as exc:
                    db.save_daily_archive_job(key, "retry", time.time() + ARCHIVE_RETRY_SECONDS)
                    logger.warning("每日归档待重试 date=%s error_type=%s", key, type(exc).__name__)

            jobs = db.list_daily_archive_jobs(start.isoformat(), today.isoformat())
            pending = {key: job for key, job in jobs.items() if job["status"] == "snapshot"}
            if not pending or any(job["retry_after"] > time.time() for job in pending.values()):
                return
            try:
                snapshot = await summarizer.generate_snapshot(db.list_daily_summaries(limit=15))
                if not snapshot or not snapshot.strip():
                    raise RuntimeError("未生成有效近况快照")
                db.update_daily_snapshot(list(pending), snapshot)
                logger.info("近况快照已更新")
            except Exception as exc:
                db.update_daily_snapshot(list(pending), None, time.time() + ARCHIVE_RETRY_SECONDS)
                logger.warning("近况快照待重试 error_type=%s", type(exc).__name__)

    async def trigger_summary(self):
        """手动触发总结"""
        try:
            logger.info("手动触发总结")
            summary = await summarizer.summarize_if_needed()
            return summary
        except Exception as e:
            logger.error(f"手动触发总结失败: {e}")
            return None

    def get_status(self) -> dict:
        """
        获取调度器状态

        Returns:
            状态信息字典
        """
        return {
            'running': self._running,
            'summarizer_state': summarizer.get_state()
        }


# 全局调度器实例
task_scheduler = TaskScheduler()
