import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup_support as setup


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.json"
        self.example = root / "config.example.json"
        self.state = root / ".setup-model-state.json"
        self.example.write_text(json.dumps({"vision_model": "template", "asr_backend": "auto", "other": "keep"}), encoding="utf-8")
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("PROJECT", root), ("CONFIG", self.config), ("EXAMPLE", self.example), ("AUTO_STATE", self.state)):
            stack.enter_context(mock.patch.object(setup, name, value))
        stack.enter_context(mock.patch.dict(os.environ, {"MEDIA_INDEX_MODEL": ""}))

    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_auto_model_changes_with_new_hardware(self):
        setup.prepare_config("qwen3.8:27b-mlx")
        setup.prepare_config("qwen3.5:4b")
        self.assertEqual(self.load(self.config)["vision_model"], "qwen3.5:4b")
        self.assertEqual(self.load(self.config)["other"], "keep")
        self.assertEqual(self.load(self.state), {"mode": "auto", "last_auto_model": "qwen3.5:4b"})

    def test_user_edited_model_stays_unchanged(self):
        setup.prepare_config("qwen3.8:27b-mlx")
        config = self.load(self.config)
        config["vision_model"] = "my-vision-model"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        setup.prepare_config("qwen3.5:4b")
        self.assertEqual(self.load(self.config)["vision_model"], "my-vision-model")
        self.assertEqual(self.load(self.state)["mode"], "manual")

    def test_existing_installation_gets_auto_state(self):
        self.config.write_text(json.dumps({"vision_model": "qwen3.8:27b-mlx"}), encoding="utf-8")
        setup.prepare_config("qwen3.8:27b-mlx")
        self.assertEqual(self.load(self.state), {"mode": "auto", "last_auto_model": "qwen3.8:27b-mlx"})

    def test_explicit_model_is_manual(self):
        with mock.patch.dict(os.environ, {"MEDIA_INDEX_MODEL": "my-vision-model"}):
            setup.prepare_config("my-vision-model")
        self.assertEqual(self.load(self.state)["mode"], "manual")


if __name__ == "__main__":
    unittest.main()
