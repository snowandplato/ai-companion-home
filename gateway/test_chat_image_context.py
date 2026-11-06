import json
import unittest

from chat_image_context import (
    build_image_context,
    context_layer_for_rounds,
    parse_vl_result,
    truncate_sentence,
)


class ChatImageContextTests(unittest.TestCase):
    def test_parse_structured_vl_result(self):
        result = parse_vl_result(json.dumps({
            "type": "photo",
            "summary": "一只橘猫趴在窗边。",
            "detail": "主体是一只橘猫，正趴在窗边向外看。",
        }))
        self.assertEqual(result["type"], "photo")
        self.assertIn("橘猫", result["summary"])
        self.assertIn("窗边", result["detail"])

    def test_markdown_fence_is_tolerated(self):
        result = parse_vl_result('```json\n{"type":"document","summary":"标题与结论。","detail":"正文内容。"}\n```')
        self.assertEqual(result["type"], "document")
        self.assertEqual(result["summary"], "标题与结论。")

    def test_sentence_truncation_prefers_complete_sentence(self):
        text = "第一句有完整内容。第二句也有内容但非常非常长。第三句。"
        result = truncate_sentence(text, 30)
        self.assertTrue(result.endswith("。"))
        self.assertLessEqual(len(result), 30)

    def test_context_uses_requested_layer_and_total_limit(self):
        images = [
            {"vision_summary": "摘要A", "vision_detail": "细节A" * 40},
            {"vision_summary": "摘要B", "vision_detail": "细节B" * 40},
        ]
        detailed = build_image_context(images, use_detail=True, limit=80)
        summary = build_image_context(images, use_detail=False, limit=80)
        self.assertIn("细节A", detailed)
        self.assertIn("摘要A", summary)
        self.assertLessEqual(len(detailed), 80)
        self.assertLessEqual(len(summary), 80)

    def test_context_layer_expires_after_ten_rounds(self):
        self.assertEqual(context_layer_for_rounds(0), "detail")
        self.assertEqual(context_layer_for_rounds(2), "detail")
        self.assertEqual(context_layer_for_rounds(3), "summary")
        self.assertEqual(context_layer_for_rounds(10), "summary")
        self.assertEqual(context_layer_for_rounds(11), "none")


if __name__ == "__main__":
    unittest.main()
