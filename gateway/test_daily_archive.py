import gc
import os
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_archive_test_import.db"))

import scheduler
from db import Database


class DailyArchiveTests(unittest.IsolatedAsyncioTestCase):
    today = date(2026, 11, 5)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(gc.collect)  # Release connections from legacy DB helpers before Windows deletes the file.
        self.path = Path(temp.name) / "archive.db"
        self.db = Database(str(self.path))
        self.start_patch("scheduler.db", self.db)
        self.start_patch("scheduler.config.get_summary_config", return_value={"api_key": "test-only"})
        self.clock = self.start_patch("scheduler.time.time", return_value=10000)
        self.daily = AsyncMock(return_value="日总结")
        self.snapshot = AsyncMock(return_value="快照")
        self.start_patch("scheduler.summarizer.generate_daily_summary", self.daily)
        self.start_patch("scheduler.summarizer.generate_snapshot", self.snapshot)

    def start_patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def add_message(self, conversation, role, content, timestamp):
        utc = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
        with closing(self.db._get_connection()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)", (conversation, "测试"))
            conn.execute("""
                INSERT INTO messages (id, conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (content, conversation, role, content, utc.strftime("%Y-%m-%d %H:%M:%S")))

    def add_round(self, day="2026-11-04"):
        self.add_message("chat", "user", f"{day} 问", f"{day}T12:00:00")
        self.add_message("chat", "assistant", f"{day} 答", f"{day}T12:01:00")

    async def test_catches_up_from_saved_messages_across_midnight_and_windows(self):
        self.add_round("2026-11-02")
        self.add_message("first", "user", "午夜前提问", "2026-11-03T23:59:00")
        self.add_message("second", "user", "另一个窗口提问", "2026-11-04T00:00:00")
        self.add_message("first", "assistant", "午夜后回答", "2026-11-04T00:00:00")
        self.add_message("second", "assistant", "另一个窗口回答", "2026-11-04T00:01:00")
        self.add_message("first", "assistant", "无对应问题", "2026-11-04T00:02:00")
        self.add_message("first", "user", "尚未回答", "2026-11-04T00:03:00")
        self.add_round("2026-11-05")
        self.add_round("2026-10-20")
        with patch("scheduler.db", Database(str(self.path))):
            await scheduler.TaskScheduler().daily_archive(self.today)
            await scheduler.TaskScheduler().daily_archive(self.today)
        self.assertEqual([call.args[0] for call in self.daily.await_args_list], ["2026-11-02", "2026-11-04"])
        self.assertEqual(self.daily.await_args.args[1], [
            {"user": "午夜前提问", "assistant": "午夜后回答"},
            {"user": "另一个窗口提问", "assistant": "另一个窗口回答"},
        ])
        self.assertEqual([row["date"] for row in self.db.list_daily_summaries()], ["2026-11-04", "2026-11-02"])
        self.snapshot.assert_awaited_once()

    async def test_summary_and_database_failures_retry_after_restart(self):
        self.add_round()
        self.daily.side_effect = [None, "日总结", "日总结"]
        await scheduler.TaskScheduler().daily_archive(self.today)
        await scheduler.TaskScheduler().daily_archive(self.today)
        self.daily.assert_awaited_once()
        self.assertEqual(self.db.list_daily_summaries(), [])
        self.clock.return_value += scheduler.ARCHIVE_RETRY_SECONDS
        with patch.object(self.db, "complete_daily_archive", side_effect=OSError):
            await scheduler.TaskScheduler().daily_archive(self.today)
        self.assertEqual(self.db.list_daily_summaries(), [])
        self.clock.return_value += scheduler.ARCHIVE_RETRY_SECONDS
        await scheduler.TaskScheduler().daily_archive(self.today)
        self.assertEqual(self.daily.await_count, 3)
        self.assertEqual(len(self.db.list_daily_summaries()), 1)

    async def test_snapshot_retry_does_not_repeat_daily_summary(self):
        self.add_round()
        self.snapshot.side_effect = [None, "快照"]
        await scheduler.TaskScheduler().daily_archive(self.today)
        self.assertEqual(len(self.db.list_daily_summaries()), 1)
        self.assertIsNone(self.db.get_profile("short_term_snapshot"))
        await scheduler.TaskScheduler().daily_archive(self.today)
        self.snapshot.assert_awaited_once()
        self.clock.return_value += scheduler.ARCHIVE_RETRY_SECONDS
        with patch("scheduler.db", Database(str(self.path))):
            await scheduler.TaskScheduler().daily_archive(self.today)
        self.daily.assert_awaited_once()
        self.assertEqual(self.db.get_profile("short_term_snapshot"), "快照")

    async def test_existing_summary_is_preserved(self):
        self.add_round()
        self.db.add_daily_summary("2026-11-04", "旧总结")
        await scheduler.TaskScheduler().daily_archive(self.today)
        self.daily.assert_not_awaited()
        self.assertEqual([row["summary"] for row in self.db.list_daily_summaries()], ["旧总结"])
