import gc
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).parent
os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "data" / "m_home_identity_import.db"))
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

import db as db_module  # noqa: E402
from app_identity import names_in_prompt, names_in_template  # noqa: E402


class AppIdentityTests(unittest.TestCase):
    def test_names_persist_without_rewriting_existing_content(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp_dir:
            path = str(Path(temp_dir) / "identity.db")
            database = db_module.Database(path)
            self.assertEqual(database.get_app_identity(), {"assistant_name": "Mav", "user_name": "岚"})
            database.update_system_core("Mav记得岚说过的话")
            database.update_app_identity("阿澄", "小鱼")
            reopened = db_module.Database(path)
            self.assertEqual(reopened.get_app_identity(), {"assistant_name": "阿澄", "user_name": "小鱼"})
            self.assertEqual(reopened.get_system_core(), "Mav记得岚说过的话")
            del reopened, database
            gc.collect()

    def test_only_app_authored_prompt_text_is_renamed(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp_dir:
            original = db_module.db
            db_module.db = db_module.Database(str(Path(temp_dir) / "identity.db"))
            try:
                db_module.db.update_app_identity("阿澄", "小鱼")
                self.assertEqual(names_in_template("Mav向岚说话"), "阿澄向小鱼说话")
                conversation = "岚说：我以前叫过你Mav。"
                prompt = f"请让Mav总结岚的话：\n{conversation}\n结束"
                self.assertEqual(
                    names_in_prompt(prompt, [conversation]),
                    f"请让阿澄总结小鱼的话：\n{conversation}\n结束",
                )
            finally:
                db_module.db = original
                gc.collect()


if __name__ == "__main__":
    unittest.main()
