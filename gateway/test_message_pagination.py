import gc
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_message_pagination_import.db"))
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from db import Database  # noqa: E402


class MessagePaginationTests(unittest.TestCase):
    def test_message_map_returns_full_lightweight_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "messages.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            for index in range(1, 46):
                self.assertTrue(database.add_message(
                    id=f"message-{index}",
                    conversation_id="conversation-1",
                    role="user" if index % 2 else "assistant",
                    content=f"消息 {index}",
                ))

            result = database.list_message_map("conversation-1")
            self.assertEqual(result["total"], 45)
            self.assertEqual(result["messages"][0]["id"], "message-1")
            self.assertEqual(result["messages"][-1]["id"], "message-45")
            self.assertNotIn("input_tokens", result["messages"][0])

            del result, database
            gc.collect()

    def test_message_and_images_are_committed_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "messages.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            images = [{
                "id": "image-1",
                "sort_order": 0,
                "file_path": "/tmp/image-1.jpg",
                "thumb_path": "/tmp/image-1-thumb.jpg",
                "mime_type": "image/jpeg",
                "width": 100,
                "height": 80,
            }]

            self.assertTrue(database.add_message(
                id="message-with-image",
                conversation_id="conversation-1",
                role="user",
                content="看图",
                message_images=images,
            ))

            with database._get_connection() as conn:
                message_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE id='message-with-image'"
                ).fetchone()["count"]
                image_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM message_images WHERE message_id='message-with-image'"
                ).fetchone()["count"]
            self.assertEqual((message_count, image_count), (1, 1))

            del database
            gc.collect()

    def test_image_failure_rolls_back_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "messages.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            duplicate_images = [{
                "id": "same-image",
                "sort_order": index,
                "file_path": f"/tmp/image-{index}.jpg",
                "thumb_path": f"/tmp/image-{index}-thumb.jpg",
                "mime_type": "image/jpeg",
                "width": 100,
                "height": 80,
            } for index in range(2)]

            self.assertFalse(database.add_message(
                id="rolled-back-message",
                conversation_id="conversation-1",
                role="user",
                content="不应留下半条消息",
                message_images=duplicate_images,
            ))

            with database._get_connection() as conn:
                message_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE id='rolled-back-message'"
                ).fetchone()["count"]
                image_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM message_images WHERE message_id='rolled-back-message'"
                ).fetchone()["count"]
            self.assertEqual((message_count, image_count), (0, 0))

            del database
            gc.collect()

    def test_descending_pages_start_with_newest_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "messages.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            for index in range(1, 6):
                self.assertTrue(database.add_message(
                    id=f"message-{index}",
                    conversation_id="conversation-1",
                    role="user" if index % 2 else "assistant",
                    content=f"消息 {index}",
                ))

            first_page = database.list_messages("conversation-1", limit=2, offset=0, order="desc")
            second_page = database.list_messages("conversation-1", limit=2, offset=2, order="desc")

            self.assertEqual(first_page["total"], 5)
            self.assertEqual([message["id"] for message in first_page["messages"]], ["message-5", "message-4"])
            self.assertEqual([message["id"] for message in second_page["messages"]], ["message-3", "message-2"])

            del first_page, second_page, database
            gc.collect()

    def test_unknown_order_keeps_ascending_compatibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "messages.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            for index in range(1, 4):
                self.assertTrue(database.add_message(
                    id=f"message-{index}",
                    conversation_id="conversation-1",
                    role="user",
                    content=f"消息 {index}",
                ))

            page = database.list_messages("conversation-1", limit=2, order="not-a-direction")
            self.assertEqual([message["id"] for message in page["messages"]], ["message-1", "message-2"])

            del page, database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
