import unittest

from prompt_cache import (
    apply_anthropic_prompt_cache,
    build_prompt_cache_system_sections,
)


class PromptCacheTests(unittest.TestCase):
    def test_system_sections_preserve_text_and_layer_boundaries(self):
        separator = "\n\n---\n\n"
        parts = ["core", "instructions", "portrait", "notes", "snapshot", "dynamic"]
        sections = build_prompt_cache_system_sections(
            stable_prefix_parts=parts[:3],
            notes_part=parts[3],
            snapshot_part=parts[4],
            dynamic_parts=parts[5:],
            separator=separator
        )

        self.assertEqual("".join(section["text"] for section in sections), separator.join(parts))
        self.assertEqual(
            [section["cache_boundary"] for section in sections],
            [True, True, True, False]
        )

    def test_anthropic_cache_builds_three_one_hour_breakpoints(self):
        body = {"system": "plain system"}
        parsed = {
            "prompt_cache_system_sections": [
                {"text": "core + instructions + portrait", "cache_boundary": True},
                {"text": "\n\n---\n\nnotes", "cache_boundary": True},
                {"text": "\n\n---\n\nsnapshot", "cache_boundary": True},
                {"text": "\n\n---\n\ndynamic memory", "cache_boundary": False},
            ]
        }

        changed = apply_anthropic_prompt_cache(body, parsed, "1h")

        self.assertTrue(changed)
        self.assertEqual(len(body["system"]), 4)
        self.assertEqual(
            [block.get("cache_control") for block in body["system"][:3]],
            [{"type": "ephemeral", "ttl": "1h"}] * 3
        )
        self.assertNotIn("cache_control", body["system"][3])

    def test_cache_off_keeps_plain_system(self):
        body = {"system": "plain system"}
        parsed = {
            "prompt_cache_system_sections": [
                {"text": "cache me", "cache_boundary": True}
            ]
        }

        changed = apply_anthropic_prompt_cache(body, parsed, "off")

        self.assertFalse(changed)
        self.assertEqual(body["system"], "plain system")

    def test_invalid_ttl_keeps_plain_system(self):
        body = {"system": "plain system"}
        parsed = {
            "prompt_cache_system_sections": [
                {"text": "cache me", "cache_boundary": True}
            ]
        }

        changed = apply_anthropic_prompt_cache(body, parsed, "on")

        self.assertFalse(changed)
        self.assertEqual(body["system"], "plain system")


if __name__ == "__main__":
    unittest.main()
