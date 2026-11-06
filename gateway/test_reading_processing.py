import gc
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_reading_import.db"))
os.environ.setdefault("EPUB_DIR", str(Path(tempfile.gettempdir()) / "m_home_reading_test_epubs"))

from db import Database
import reading


class ReadingProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_chunk_retries_without_publishing_partial_chapter(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(gc.collect)
        database = Database(str(Path(temp.name) / "reading.db"))
        book_id = database.add_book("Test book", "", None, None, 2)
        first = "First section.\n\n"
        second = "Second section."
        database.add_chapter(book_id, 0, "First", first + second, "[]")
        database.add_chapter(book_id, 1, "Next", "Next chapter.", "[]")

        split = reading.split_text_into_chunks
        result = lambda summary, quote: json.dumps({
            "summary": summary,
            "annotations": [{"start_char": 0, "end_char": len(quote), "quote_text": quote, "content": "Note"}],
        })
        response = AsyncMock(side_effect=[result("First clue", "First"), ValueError("Test failure")])
        with (
            patch.object(reading, "db", database),
            patch.object(reading, "split_text_into_chunks", side_effect=lambda text: split(text, 16)),
            patch.object(reading, "call_main_model_json", response),
            patch.object(reading.asyncio, "sleep", new_callable=AsyncMock),
        ):
            await reading.process_book_task(book_id)
            self.assertEqual(database.get_book(book_id)["status"], "failed")
            self.assertFalse(database.list_chapters(book_id)[0]["summary"])
            self.assertEqual(database.get_annotations_for_chapter(book_id, 0, author="mav"), [])

            response.reset_mock()
            response.side_effect = [
                result("First clue", "First"),
                result("First clue and second clue", "Second"),
                result("Next clue", "Next"),
            ]
            await reading.process_book_task(book_id)
            self.assertIn("First clue", response.await_args_list[1].args[0][0]["content"])
            self.assertIn("First clue and second clue", response.await_args_list[2].args[0][0]["content"])
            self.assertEqual(database.list_chapters(book_id)[0]["summary"], "First clue and second clue")
            self.assertEqual(database.get_book(book_id)["status"], "ready")
            self.assertEqual(database.get_book(book_id)["processed_chapters"], 2)
            annotations = database.get_annotations_for_chapter(book_id, 0, author="mav")
            self.assertEqual([item["start_char"] for item in annotations], [0, len(first)])
            await reading.process_book_task(book_id)
            self.assertEqual(response.await_count, 3)

    def test_chunking_preserves_original_offsets_and_caps_long_paragraphs(self):
        text = "First paragraph.\n\n" + "b" * 50
        chunks = reading.split_text_into_chunks(text, 20)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(0 < len(chunk) <= 20 for chunk in chunks))
