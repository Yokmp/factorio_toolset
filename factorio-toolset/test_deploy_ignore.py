"""Focused tests for mod-local deploy ignore rules."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import deploy


class DeployIgnoreTests(unittest.TestCase):
    def test_template_and_archive_specific_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            mod_root = Path(temp) / "example"
            mod_root.mkdir()
            (mod_root / "info.json").write_text(json.dumps({"name": "example", "title": "Example", "version": "1.0.0"}), encoding="utf-8")
            for name in ("control.lua", "readme.md", "secret.txt", "public-only.txt", "portal-only.txt", "debug.py"):
                (mod_root / name).write_text(name, encoding="utf-8")
            config = deploy.DeployConfig(mod_root, mod_root / "_release_", False, True, False)

            patterns = deploy.load_ignore_patterns(mod_root)
            ignore_path = mod_root / deploy.IGNORE_FILENAME
            self.assertTrue(ignore_path.exists())
            self.assertIn("*.py", patterns["common"])
            self.assertIn("*.md", patterns["portal"])
            with ignore_path.open("a", encoding="utf-8") as handle:
                handle.write("\n[common]\nsecret.txt\n[public]\npublic-only.txt\n[portal]\nportal-only.txt\n")

            deploy.build_release(config)
            with zipfile.ZipFile(config.release_dir / "public.zip") as archive:
                public = {Path(name).name for name in archive.namelist()}
            with zipfile.ZipFile(config.release_dir / "example_1.0.0.zip") as archive:
                portal = {Path(name).name for name in archive.namelist()}
            self.assertIn("readme.md", public)
            self.assertNotIn("readme.md", portal)
            self.assertIn(deploy.IGNORE_FILENAME, public)
            self.assertNotIn(deploy.IGNORE_FILENAME, portal)
            self.assertIn("portal-only.txt", public)
            self.assertNotIn("portal-only.txt", portal)
            self.assertIn("public-only.txt", portal)
            self.assertNotIn("public-only.txt", public)
            for names in (public, portal):
                self.assertIn("control.lua", names)
                self.assertNotIn("secret.txt", names)
                self.assertNotIn("debug.py", names)
            self.assertEqual(ignore_path.read_text(encoding="utf-8").count("secret.txt"), 1)


if __name__ == "__main__":
    unittest.main()
