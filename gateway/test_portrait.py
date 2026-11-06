import gc
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


os.environ.setdefault(
    "DATABASE_PATH",
    str(Path(tempfile.gettempdir()) / "m_home_portrait_import.db")
)

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from db import Database  # noqa: E402
from portrait import (  # noqa: E402
    InternalBlockFilter,
    build_portrait_injection,
    load_portrait_injection,
    parse_portrait_update,
)


class PortraitTests(unittest.TestCase):
    def test_parses_valid_update_and_rejects_invalid_section(self):
        update, error = parse_portrait_update(
            '{"section":"values","action":"revise","content":"新的完整内容"}'
        )
        self.assertIsNone(error)
        self.assertEqual(update["section"], "values")

        update, error = parse_portrait_update(
            '{"section":"new_section","action":"append","content":"内容"}'
        )
        self.assertIsNone(update)
        self.assertEqual(error, "invalid_section")

    def test_mechanically_repairs_punctuation_without_rewriting_content(self):
        update, error = parse_portrait_update(
            '“section”：“values”，“action”：“revise”，“content”：“她会说“先等等”，再重新判断。”，'
        )
        self.assertIsNone(error)
        self.assertEqual(update, {
            "section": "values",
            "action": "revise",
            "content": "她会说“先等等”，再重新判断。",
        })

    def test_mechanically_repairs_unescaped_inner_quotes_and_trailing_comma(self):
        update, error = parse_portrait_update(
            '{"section":"values","action":"append","content":"保留"原话"的含义",}'
        )
        self.assertIsNone(error)
        self.assertEqual(update["content"], '保留"原话"的含义')

    def test_mechanical_repair_still_rejects_unknown_section(self):
        update, error = parse_portrait_update(
            'section：unknown，action：append，content：“不能猜测section”'
        )
        self.assertIsNone(update)
        self.assertEqual(error, "invalid_section")

    def test_stream_filter_hides_note_portrait_and_art_across_chunks(self):
        block_filter = InternalBlockFilter()
        chunks = [
            "前半段<mav_",
            "note>{\"content\":\"便签\"}</mav_note>中间",
            "<mav_portrait>{\"section\":\"values\",",
            "\"action\":\"append\",\"content\":\"认识\"}</mav_portrait>",
            '<mav_art>{"prompt":"雨夜里的两个人"}</mav_art>后半段'
        ]
        visible = "".join(block_filter.feed(chunk) for chunk in chunks)
        visible += block_filter.flush_visible()

        self.assertEqual(visible, "前半段中间后半段")
        self.assertEqual(len(block_filter.notes), 1)
        self.assertEqual(len(block_filter.portraits), 1)
        self.assertEqual(len(block_filter.arts), 1)
        self.assertEqual(block_filter.visible_text, "前半段中间后半段")
        self.assertEqual(block_filter.art_start_visible_chars, [5])

    def test_recovers_unclosed_art_block_without_exposing_it(self):
        block_filter = InternalBlockFilter()
        visible = block_filter.feed(
            '我想画这个。<mav_art>{"decision":"create","prompt":"夜里的画室"}'
        )
        self.assertEqual(visible, "我想画这个。")
        self.assertTrue(block_filter.recover_incomplete_art())
        self.assertEqual(len(block_filter.arts), 1)
        self.assertEqual(block_filter.flush_visible(), "")

    def test_unclosed_art_block_restores_prose_after_complete_json(self):
        block_filter = InternalBlockFilter()
        visible = block_filter.feed(
            '我先说到这里。<mav_art>{"decision":"create","prompt":"夜里的画室"}'
            '\n这幅画还想留一点暖光。'
        )
        self.assertEqual(visible, "我先说到这里。")
        self.assertTrue(block_filter.recover_incomplete_art())
        self.assertEqual(
            block_filter.flush_visible(),
            "\n\n这幅画还想留一点暖光。",
        )
        self.assertEqual(len(block_filter.arts), 1)

    def test_invalid_unclosed_art_block_keeps_internal_text_hidden(self):
        block_filter = InternalBlockFilter()
        block_filter.feed('<mav_art>{"decision":"create","prompt":"未写完"')
        self.assertTrue(block_filter.recover_incomplete_art())
        self.assertEqual(block_filter.flush_visible(), "")

    def test_recovers_unclosed_portrait_block_without_exposing_it(self):
        block_filter = InternalBlockFilter()
        visible = block_filter.feed(
            '我有一点新认识。<mav_portrait>{"section":"values","action":"append","content":"新认识"}'
        )
        self.assertEqual(visible, "我有一点新认识。")
        self.assertTrue(block_filter.recover_incomplete_portrait())
        self.assertEqual(len(block_filter.portraits), 1)
        self.assertEqual(block_filter.flush_visible(), "")

    def test_database_update_is_logged_without_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "portrait.db"))
            self.assertTrue(database.replace_portrait_sections({"values": "初始内容"}))

            result = database.apply_portrait_update("values", "append", "新增认识")
            self.assertTrue(result["updated"])
            values = next(
                item for item in database.get_portrait_sections()
                if item["section_key"] == "values"
            )
            self.assertEqual(values["content"], "初始内容\n新增认识")

            logs = database.list_portrait_update_logs()
            self.assertEqual(len(logs), 1)
            self.assertNotIn("content", logs[0])
            self.assertEqual(logs[0]["section_name"], "价值取向")

    def test_injection_keeps_fixed_groups_and_soft_limit_warning(self):
        text = build_portrait_injection(
            [{
                "section_key": "values",
                "content": "一" * 151,
                "char_limit": 150
            }],
            include_maintenance_hints=True
        )
        self.assertIn("【人格侧】", text)
        self.assertIn("【能力侧】", text)
        self.assertIn("[该段已超出建议长度，下次更新时请精简]", text)

    def test_injection_reports_portrait_read_failure(self):
        class FailingDatabase:
            def get_portrait_sections(self, raise_on_error=False):
                raise RuntimeError("database unavailable")

        text = load_portrait_injection(FailingDatabase())
        self.assertEqual(
            text,
            "=== 岚的画像（Mav 的持续观察）===\n[画像暂时不可用]"
        )

    def test_message_persists_prompt_cache_usage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "usage.db"))
            self.assertTrue(database.create_conversation("conversation-1"))
            self.assertTrue(database.add_message(
                id="message-1",
                conversation_id="conversation-1",
                role="assistant",
                content="回复",
                input_tokens=1200,
                output_tokens=100,
                total_tokens=1300,
                cache_creation_tokens=0,
                cache_read_tokens=900,
                cache_creation_1h_tokens=0
            ))

            messages = database.list_messages("conversation-1")["messages"]
            self.assertEqual(messages[0]["cache_read_tokens"], 900)
            self.assertEqual(messages[0]["cache_creation_tokens"], 0)
            del messages
            del database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
