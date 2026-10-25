import re
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import main

BASE = "http://testserver"
ROOT = Path(__file__).resolve().parent.parent


class SeoPagesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def get(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp

    def test_pages_have_title_description_and_canonical_without_placeholders(self):
        for path in ("/", "/about", "/privacy"):
            html = self.get(path).text
            self.assertNotIn("__SITE__", html, path)
            title = re.search(r"<title>(.+?)</title>", html).group(1)
            self.assertTrue(10 < len(title) <= 70, f"{path}: {title}")
            self.assertRegex(html, r'<meta name="description" content="[^"]{40,}')
            canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)
            self.assertEqual(canonical, BASE + (path if path != "/" else "/"))

    def test_home_page_has_indexable_text_and_structured_data(self):
        html = self.get("/").text
        self.assertIn("<h1>", html)
        self.assertIn("자주 묻는 질문", html)  # real text for search engines, not only a script-built UI
        self.assertIn('"@type":"WebApplication"', html)
        self.assertIn('property="og:title"', html)

    def test_site_url_setting_overrides_request_address(self):
        with mock.patch("app.config.SITE_URL", "https://example.test"):
            self.assertIn('href="https://example.test/about"', self.get("/about").text)
            self.assertIn("https://example.test/sitemap.xml", self.get("/robots.txt").text)

    def test_robots_blocks_api_and_points_to_sitemap(self):
        text = self.get("/robots.txt").text
        self.assertIn("Disallow: /api/", text)
        self.assertIn("Disallow: /auth/", text)
        self.assertIn(f"Sitemap: {BASE}/sitemap.xml", text)

    def test_sitemap_lists_public_pages_only(self):
        resp = self.get("/sitemap.xml")
        self.assertIn("xml", resp.headers["content-type"])
        locs = re.findall(r"<loc>(.+?)</loc>", resp.text)
        self.assertEqual(locs, [f"{BASE}/", f"{BASE}/about", f"{BASE}/privacy"])

    def test_deploy_blueprint_never_contains_secret_values(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        for key in ("SPOTIFY_CLIENT_SECRET", "LASTFM_API_KEY", "YOUTUBE_API_KEY"):
            block = text.split(f"key: {key}")[1].split("- key:")[0]
            self.assertIn("sync: false", block)
            self.assertNotIn("value:", block)
        self.assertIn("--proxy-headers", text)


if __name__ == "__main__":
    unittest.main()
