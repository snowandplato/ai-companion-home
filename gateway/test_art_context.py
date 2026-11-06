import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_art_import.db"))
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from art_context import (  # noqa: E402
    parse_art_request,
    parse_art_review,
    review_model_for_main_model,
    size_for_aspect_ratio,
)
from db import Database  # noqa: E402


class ArtContextTests(unittest.TestCase):
    def test_request_defaults_and_limits_lists(self):
        request, error = parse_art_request(
            '{"prompt":"画一间暖色画室","must_keep":["Mav","岚"],"avoid":["文字"]}'
        )
        self.assertIsNone(error)
        self.assertEqual(request["decision"], "create")
        self.assertEqual(request["aspect_ratio"], "square")
        self.assertEqual(request["must_keep"], ["Mav", "岚"])

    def test_invalid_aspect_falls_back_to_square(self):
        request, error = parse_art_request('{"prompt":"一张画","aspect_ratio":"cinema"}')
        self.assertIsNone(error)
        self.assertEqual(request["aspect_ratio"], "square")

    def test_mav_can_explicitly_decline(self):
        request, error = parse_art_request('{"decision":"decline"}')
        self.assertIsNone(error)
        self.assertEqual(request, {"decision": "decline"})

    def test_recovers_first_complete_json_before_trailing_text(self):
        request, error = parse_art_request(
            '{"decision":"create","prompt":"窗边画画的水獭"}\n我已经开始画了。'
        )
        self.assertIsNone(error)
        self.assertEqual(request["prompt"], "窗边画画的水獭")

    def test_review_select_requires_existing_candidate(self):
        review, error = parse_art_review(
            '{"decision":"select","selected_index":3,"caption":"给你。"}', 3, True
        )
        self.assertIsNone(error)
        self.assertEqual(review["selected_index"], 3)

        review, error = parse_art_review('{"decision":"select","selected_index":4}', 3, True)
        self.assertIsNone(review)
        self.assertEqual(error, "invalid_selected_index")

    def test_retry_is_converted_to_reject_on_final_round(self):
        review, error = parse_art_review(
            '{"decision":"retry","revised_prompt":"换个构图","caption":"先不交稿"}', 3, False
        )
        self.assertIsNone(error)
        self.assertEqual(review["decision"], "reject")

    def test_image_sizes_match_three_supported_aspects(self):
        self.assertEqual(size_for_aspect_ratio("square"), "1024x1024")
        self.assertEqual(size_for_aspect_ratio("portrait"), "1024x1536")
        self.assertEqual(size_for_aspect_ratio("landscape"), "1536x1024")

    def test_review_model_follows_claude_and_falls_back_for_other_models(self):
        fallback = "claude-sonnet-4-6"
        self.assertEqual(
            review_model_for_main_model("claude-opus-4-6", fallback),
            "claude-opus-4-6",
        )
        self.assertEqual(
            review_model_for_main_model("anthropic/claude-sonnet-4.6", fallback),
            "anthropic/claude-sonnet-4.6",
        )
        self.assertEqual(review_model_for_main_model("gpt-5", fallback), fallback)

    def test_database_creates_art_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "art.db"))
            conn = database._get_connection()
            try:
                names = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'art_%'"
                    ).fetchall()
                }
            finally:
                conn.close()
            self.assertEqual(names, {"art_jobs", "art_candidates"})


if __name__ == "__main__":
    unittest.main()
