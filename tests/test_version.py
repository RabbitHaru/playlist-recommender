import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__, main

ROOT = Path(__file__).resolve().parent.parent


class VersionTest(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_changelog_top_entry_matches_current_version(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        first = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M).group(1)
        self.assertEqual(first, __version__)

    def test_health_reports_version(self):
        body = TestClient(main.app).get("/api/health").json()
        self.assertEqual(body["version"], __version__)


if __name__ == "__main__":
    unittest.main()
