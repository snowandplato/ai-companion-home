import gc
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.gettempdir()) / "m_home_profile_search_import.db"))
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from db import Database  # noqa: E402


class ProfileSearchTests(unittest.TestCase):
    def test_only_profile_keys_participate_in_retrieval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "profiles.db"))
            self.assertTrue(database.upsert_profile("favorite_food", "岚喜欢astronomy相关内容"))
            self.assertTrue(database.upsert_profile("astronomy_interest", "会关注天文新闻"))

            results = database.search_profiles(["astronomy"])

            self.assertEqual([item["key"] for item in results], ["astronomy_interest"])
            self.assertTrue(all(item["match_type"] == "key" for item in results))

            del results, database
            gc.collect()

    def test_short_term_snapshot_is_never_returned_as_dynamic_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "profiles.db"))
            self.assertTrue(database.upsert_profile("short_term_snapshot", "近期状态", category="memory"))

            self.assertEqual(database.search_profiles(["snapshot"]), [])

            del database
            gc.collect()


if __name__ == "__main__":
    unittest.main()
