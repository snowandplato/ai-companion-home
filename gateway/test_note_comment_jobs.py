import asyncio
import gc
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch


os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_note_jobs_import.db"))
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub
if "httpx" not in sys.modules:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.AsyncClient = type("AsyncClient", (), {})
    httpx_stub.Timeout = lambda *args, **kwargs: None
    httpx_stub.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    httpx_stub.ReadTimeout = type("ReadTimeout", (Exception,), {})
    sys.modules["httpx"] = httpx_stub
if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")

    class RouterStub:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return lambda *args, **kwargs: lambda func: func

    class HTTPExceptionStub(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail

    fastapi_stub.APIRouter = RouterStub
    fastapi_stub.File = lambda default=None, **kwargs: default
    fastapi_stub.Form = lambda default=None, **kwargs: default
    fastapi_stub.HTTPException = HTTPExceptionStub
    fastapi_stub.Request = type("Request", (), {})
    fastapi_stub.UploadFile = type("UploadFile", (), {})
    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.FileResponse = type("FileResponse", (), {})
    class StreamingResponseStub:
        def __init__(self, content, *args, **kwargs):
            self.body_iterator = content

    responses_stub.StreamingResponse = StreamingResponseStub
    sys.modules["fastapi"] = fastapi_stub
    sys.modules["fastapi.responses"] = responses_stub

from db import Database  # noqa: E402
import notes  # noqa: E402


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class NoteCommentJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_does_not_cancel_job_and_repeat_request_is_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.commit()
            calls = []

            async def generate(*args, **kwargs):
                calls.append(1)
                yield "Mav看到了"
                await asyncio.sleep(0)
                yield "这篇笔记。"

            payload = {"mode": "new", "request_id": "same-request-123"}
            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=generate
            ):
                first = await notes.create_mav_comment_job(1, FakeRequest(payload))
                # No polling or open browser connection is needed for completion.
                await notes._comment_tasks[first["id"]]
                repeated = await notes.create_mav_comment_job(1, FakeRequest(payload))
                self.assertEqual(first["id"], repeated["id"])
                self.assertEqual(repeated["status"], "completed")
                self.assertEqual(len(calls), 1)
                with closing(database._get_connection()) as conn:
                    rows = conn.execute("SELECT content FROM image_note_comments").fetchall()
                self.assertEqual([row["content"] for row in rows], ["Mav看到了这篇笔记。"])
            del database
            gc.collect()

    async def test_failed_regeneration_keeps_old_comments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.execute("INSERT INTO image_note_comments (note_id, author, content) VALUES (1, 'mav', '原来的评论')")
                conn.commit()

            async def fail(*args, **kwargs):
                raise RuntimeError("upstream disconnected")
                yield ""  # pragma: no cover

            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=fail
            ):
                job = await notes.create_mav_comment_job(1, FakeRequest({
                    "mode": "regenerate", "request_id": "regenerate-123"
                }))
                await notes._comment_tasks[job["id"]]
                self.assertEqual(notes._get_comment_job(1, job["id"])["status"], "failed")
                with closing(database._get_connection()) as conn:
                    rows = conn.execute("SELECT content FROM image_note_comments").fetchall()
                self.assertEqual([row["content"] for row in rows], ["原来的评论"])
            del database
            gc.collect()

    async def test_upstream_timeout_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.commit()
            calls = []

            async def timeout(*args, **kwargs):
                calls.append(1)
                raise notes.httpx.ReadTimeout("read timed out")
                yield ""  # pragma: no cover

            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=timeout
            ):
                job = await notes.create_mav_comment_job(1, FakeRequest({
                    "mode": "new", "request_id": "timeout-request-123"
                }))
                await notes._comment_tasks[job["id"]]
                failed = notes._get_comment_job(1, job["id"])
                self.assertEqual(failed["status"], "failed")
                self.assertIn("可能已计费", failed["error"])
                self.assertEqual(len(calls), 1)
            del database
            gc.collect()

    async def test_successful_regeneration_replaces_comments_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.execute("INSERT INTO image_note_comments (note_id, author, content) VALUES (1, 'mav', '原来的评论')")
                conn.commit()

            async def generate(*args, **kwargs):
                yield "新的评论"

            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=generate
            ):
                job = await notes.create_mav_comment_job(1, FakeRequest({
                    "mode": "regenerate", "request_id": "regenerate-success-123"
                }))
                with closing(database._get_connection()) as conn:
                    before = conn.execute("SELECT content FROM image_note_comments").fetchall()
                self.assertEqual([row["content"] for row in before], ["原来的评论"])
                await notes._comment_tasks[job["id"]]
                with closing(database._get_connection()) as conn:
                    after = conn.execute("SELECT content FROM image_note_comments").fetchall()
                self.assertEqual([row["content"] for row in after], ["新的评论"])
            del database
            gc.collect()

    async def test_two_clients_share_one_active_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.commit()
            release = asyncio.Event()
            calls = []

            async def generate(*args, **kwargs):
                calls.append(1)
                await release.wait()
                yield "一条评论"

            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=generate
            ):
                first = await notes.create_mav_comment_job(1, FakeRequest({"mode": "new", "request_id": "first-request-123"}))
                second = await notes.create_mav_comment_job(1, FakeRequest({"mode": "new", "request_id": "second-request-123"}))
                self.assertEqual(first["id"], second["id"])
                release.set()
                await notes._comment_tasks[first["id"]]
                self.assertEqual(len(calls), 1)
            del database
            gc.collect()

    async def test_legacy_stream_still_returns_text_and_done(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.commit()

            async def generate(*args, **kwargs):
                yield "旧页面也能收到"

            with patch.object(notes, "db", database), patch.object(
                notes, "_ensure_note_vision", new_callable=AsyncMock, return_value=[]
            ), patch.object(notes, "_build_note_system_prompt", return_value="system"), patch.object(
                notes, "_stream_main_model", new=generate
            ):
                response = await notes.create_mav_comment(1, FakeRequest({"mode": "new"}))
                events = []
                async for chunk in response.body_iterator:
                    if chunk.startswith("data:"):
                        events.append(json.loads(chunk[5:].strip()))
                self.assertEqual([event["type"] for event in events], ["text", "done"])
                self.assertEqual(events[0]["text"], "旧页面也能收到")
            del database
            gc.collect()

    async def test_restart_marks_unfinished_job_without_resending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "notes.db"))
            with closing(database._get_connection()) as conn:
                conn.execute("INSERT INTO image_notes (title) VALUES ('测试笔记')")
                conn.execute("""INSERT INTO image_note_comment_jobs
                    (request_id, note_id, mode, status) VALUES ('old-request', 1, 'new', 'running')""")
                conn.commit()
            with patch.object(notes, "db", database):
                notes.interrupt_stale_comment_jobs()
                self.assertEqual(notes._get_comment_job(1)["status"], "interrupted")
            del database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
