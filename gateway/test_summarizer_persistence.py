import gc
import os
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch


os.environ.setdefault(
    "DATABASE_PATH",
    str(Path(tempfile.gettempdir()) / "m_home_summarizer_import.db"),
)

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

if "httpx" not in sys.modules:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.Response = type("Response", (), {})
    httpx_stub.AsyncClient = type("AsyncClient", (), {})
    httpx_stub.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    sys.modules["httpx"] = httpx_stub

from db import Database  # noqa: E402
from summarizer import ConversationHistory, Summarizer  # noqa: E402


class SummarizerPersistenceTests(unittest.TestCase):
    def test_existing_checkpoint_schema_gains_retry_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "old.db"
            import sqlite3
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("""CREATE TABLE instant_summary_state (
                    id INTEGER PRIMARY KEY, summary TEXT NOT NULL,
                    rounds_since_summary INTEGER NOT NULL, last_summary_time REAL NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                conn.execute("INSERT INTO instant_summary_state (id, summary, rounds_since_summary, last_summary_time) VALUES (1, '旧摘要', 25, 1)")
                conn.commit()
            database = Database(str(db_path))
            state = database.get_instant_summary_state()
            self.assertEqual(state["summary"], "旧摘要")
            self.assertEqual(state["summary_failure_count"], 0)
            self.assertEqual(state["summary_retry_after"], 0)
            del database
            gc.collect()

    def test_latest_summary_overwrites_single_global_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "summary.db"))
            self.assertTrue(database.save_instant_summary_state("第一版", 2, 10.0))
            self.assertTrue(database.save_instant_summary_state("第二版", 0, 20.0))

            state = database.get_instant_summary_state()
            self.assertEqual(state["summary"], "第二版")
            self.assertEqual(state["rounds_since_summary"], 0)
            with closing(database._get_connection()) as conn:
                count = conn.execute("SELECT COUNT(*) FROM instant_summary_state").fetchone()[0]
            self.assertEqual(count, 1)
            del database
            gc.collect()

    def test_restores_one_timeline_across_conversations_and_ignores_half_round(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "timeline.db"))
            self.assertTrue(database.create_conversation("room-a"))
            self.assertTrue(database.create_conversation("room-b"))
            for message_id, room, role, content in [
                ("a-user", "room-a", "user", "第一间房的问题"),
                ("a-assistant", "room-a", "assistant", "第一间房的回答[[mav_note_saved]]"),
                ("b-user", "room-b", "user", "换房后的问题"),
                ("b-assistant", "room-b", "assistant", "换房后的回答"),
                ("pending-user", "room-a", "user", "尚未回答，不应恢复"),
            ]:
                self.assertTrue(database.add_message(
                    id=message_id,
                    conversation_id=room,
                    role=role,
                    content=content,
                ))

            self.assertTrue(database.save_instant_summary_state("保存下来的摘要", 3, 42.0))
            history = ConversationHistory(max_rounds=10, database=database)

            self.assertEqual(history.current_summary, "保存下来的摘要")
            self.assertEqual(history._rounds_since_summary, 3)
            self.assertEqual(len(history.history), 2)
            self.assertEqual(history.history[0]["user"], "第一间房的问题")
            self.assertEqual(history.history[0]["assistant"], "第一间房的回答")
            self.assertEqual(history.history[1]["user"], "换房后的问题")
            del history
            del database
            gc.collect()

    def test_new_round_and_summary_are_persisted_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "checkpoint.db"))
            history = ConversationHistory(max_rounds=10, database=database)

            history.add_round("新问题", "新回答")
            self.assertEqual(
                database.get_instant_summary_state()["rounds_since_summary"],
                1,
            )

            history.update_summary("更新后的即时摘要")
            restored = ConversationHistory(max_rounds=10, database=database)
            self.assertEqual(restored.current_summary, "更新后的即时摘要")
            self.assertEqual(restored._rounds_since_summary, 0)
            del restored
            del history
            del database
            gc.collect()

    def test_explicit_clear_removes_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "reset.db"))
            self.assertTrue(database.save_instant_summary_state("会被清除", 4, 12.0))
            self.assertTrue(database.clear_instant_summary_state())
            self.assertEqual(database.get_instant_summary_state(), {})
            del database
            gc.collect()

    def test_existing_history_without_checkpoint_requests_first_turn_rebuild(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "migration.db"))
            self.assertTrue(database.create_conversation("room"))
            for index in range(11):
                self.assertTrue(database.add_message(
                    id=f"user-{index}",
                    conversation_id="room",
                    role="user",
                    content=f"问题{index}",
                ))
                self.assertTrue(database.add_message(
                    id=f"assistant-{index}",
                    conversation_id="room",
                    role="assistant",
                    content=f"回答{index}",
                ))

            history = ConversationHistory(max_rounds=30, database=database)
            self.assertTrue(history.needs_initial_summary_rebuild)
            self.assertTrue(history.should_summarize())
            del history
            del database
            gc.collect()


class SummaryRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_delay_grows_but_is_capped(self):
        history = ConversationHistory(database=None)
        with patch("summarizer.time.time", return_value=1000.0):
            for expected_delay in (900, 1800, 3600, 3600):
                history.mark_summary_failure()
                self.assertEqual(history.summary_retry_after, 1000 + expected_delay)

    async def test_failure_waits_and_survives_restart_then_success_clears_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "retry.db"))
            self.assertTrue(database.save_instant_summary_state("旧摘要", 25, 1.0))
            summarizer = Summarizer(database=database)
            with patch("summarizer.time.time", return_value=1000.0), patch.object(
                summarizer, "_generate_summary", new_callable=AsyncMock, return_value=None
            ) as generate:
                self.assertIsNone(await summarizer.summarize_if_needed())
                self.assertEqual(generate.await_count, 1)
                self.assertFalse(summarizer.should_summarize())
                self.assertIsNone(await summarizer.summarize_if_needed())
                self.assertEqual(generate.await_count, 1)

            restored = Summarizer(database=database)
            self.assertEqual(restored.get_summary(), "旧摘要")
            self.assertEqual(restored.conversation_history.summary_failure_count, 1)
            with patch("summarizer.time.time", return_value=1900.0):
                self.assertTrue(restored.should_summarize())
            with patch("summarizer.time.time", return_value=1900.0), patch.object(
                restored, "_generate_summary", new_callable=AsyncMock, return_value="新摘要"
            ):
                self.assertEqual(await restored.summarize_if_needed(), "新摘要")
            state = database.get_instant_summary_state()
            self.assertEqual(state["summary"], "新摘要")
            self.assertEqual(state["summary_failure_count"], 0)
            self.assertEqual(state["summary_retry_after"], 0)
            del restored
            del summarizer
            del database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
