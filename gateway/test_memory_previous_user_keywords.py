import asyncio
import gc
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


TEST_DIR = Path(__file__).resolve().parent
IMPORT_DIR = tempfile.TemporaryDirectory(dir=TEST_DIR)
os.environ["DATABASE_PATH"] = str(Path(IMPORT_DIR.name) / "import.db")
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub
if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        sys.modules["httpx"] = types.ModuleType("httpx")

from db import Database  # noqa: E402
from injector import InjectorService  # noqa: E402
import memory_recall  # noqa: E402


class PreviousUserKeywordTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        gc.collect()
        IMPORT_DIR.cleanup()

    def test_previous_turn_recalls_without_displacing_current_candidates(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp_dir:
            database = Database(str(Path(temp_dir) / "memory.db"))
            database.upsert_memory_thread("old", title="搬家")
            database.upsert_memory_thread("current", title="Doppler")
            for index in range(90):
                database.add_memory_event("old", f"搬家旧事件{index}", importance=5)
            current_id = database.add_memory_event("current", "Doppler的新事件", importance=1)

            result = database.get_long_term_memory_v2(
                ["Doppler", "搬家"],
                primary_keywords=["Doppler"],
                expanded_keywords=[],
                previous_keywords=["搬家"],
                event_top_n=5,
            )

            self.assertEqual(result["events"][0]["id"], current_id)
            self.assertEqual(result["events"][0]["keyword_score"], 3)
            self.assertEqual(result["events"][0]["previous_match_count"], 0)
            self.assertTrue(any(event["match_source"] == "previous_keyword" for event in result["events"]))

            del result, database
            gc.collect()

    def test_only_current_message_is_embedded_and_profiles_use_current_terms(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp_dir:
            database = Database(str(Path(temp_dir) / "memory.db"))
            database.upsert_memory_thread("old", title="搬家")
            database.add_memory_event("old", "搬家的记忆")
            database.upsert_profile("moving_topic", "画像内容")
            service = InjectorService()
            service.db = database
            service.injector.db = database
            service.injector.extract_keywords = lambda text: ["搬家"] if "搬家" in text else ["后来"]
            service._expand_keywords = AsyncMock(return_value=[])

            messages = [
                {"role": "user", "content": "之前聊到搬家"},
                {"role": "assistant", "content": "嗯，我们聊到了搬家。"},
                {"role": "user", "content": "那它后来怎么样了？"},
            ]
            with patch("injector.embedding_service.embed_text", new=AsyncMock(return_value=None)) as embed:
                result = asyncio.run(service.process_request({"messages": messages}))

            self.assertIn("搬家的记忆", result.get("system", ""))
            self.assertNotIn("画像内容", result.get("system", ""))
            self.assertEqual(embed.await_args.args[0], "那它后来怎么样了？")
            debug = service.get_memory_injection_debug()[0]
            self.assertEqual(debug["primary_keywords"], ["后来"])
            self.assertEqual(debug["previous_keywords"], ["搬家"])
            self.assertEqual(debug["events"][0]["previous_keyword_hits"], ["搬家"])

            del result, service, database
            gc.collect()

    def test_active_recall_reads_thread_and_omits_inactive_events(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as temp_dir:
            database = Database(str(Path(temp_dir) / "memory.db"))
            database.upsert_memory_thread("home", title="搬家", summary_full="搬家的完整脉络")
            database.add_memory_event("home", "保留的经历", status="active")
            for index in range(30):
                database.add_memory_event("home", f"已归档的经历{index}", status="archived")
            with patch.object(memory_recall, "db", database):
                result = json.loads(asyncio.run(memory_recall.recall_memory({"thread_key": "home"})))
            self.assertEqual(result["thread"]["summary"], "搬家的完整脉络")
            self.assertEqual([event["summary"] for event in result["events"]], ["保留的经历"])

            del result, database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
