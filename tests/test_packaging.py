"""Unit tests for packaging metadata, PyPI onboarding script, and publish workflow."""
from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


class TestPackaging(unittest.TestCase):
    def test_pyproject_toml_structure(self):
        self.assertTrue(PYPROJECT_PATH.exists(), "pyproject.toml must exist")
        content = PYPROJECT_PATH.read_text(encoding="utf-8")
        data = tomllib.loads(content)

        project = data.get("project", {})
        self.assertEqual(project.get("name"), "empower-personal-dashboard")
        self.assertTrue(project.get("version"))
        self.assertEqual(project.get("license"), "MIT")
        keywords = project.get("keywords", [])
        self.assertIn("empower", keywords)
        self.assertIn("personal-capital", keywords)
        self.assertIn("finance", keywords)

        authors = project.get("authors", [])
        self.assertGreater(len(authors), 0)
        self.assertEqual(authors[0].get("name"), "Don Petry")

        classifiers = project.get("classifiers", [])
        self.assertNotIn("License :: OSI Approved :: MIT License", classifiers)  # PEP 639
        self.assertIn("Programming Language :: Python :: 3", classifiers)

        urls = project.get("urls", {})
        self.assertIn("https://github.com/petry-projects/empower-personal-dashboard", urls.get("Homepage", ""))
        self.assertIn("https://github.com/petry-projects/empower-personal-dashboard", urls.get("Repository", ""))

        opt_deps = project.get("optional-dependencies", {})
        self.assertIn("build", opt_deps)
        self.assertIn("dev", opt_deps)
        self.assertIn("all", opt_deps)
        self.assertIn("mcp", opt_deps)
        self.assertIn("test", opt_deps)
        self.assertIn("beancount", opt_deps)

    def test_check_pypi_status_available(self):
        from scripts.pypi_onboard import PACKAGE_NAME, check_pypi_status

        mock_err = urllib.error.HTTPError(
            url="https://pypi.org/pypi/empower-personal-dashboard/json",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            status, detail = check_pypi_status(PACKAGE_NAME)
            self.assertEqual(status, "AVAILABLE")
            self.assertIn("available", detail.lower())

    def test_check_pypi_status_taken(self):
        from scripts.pypi_onboard import PACKAGE_NAME, check_pypi_status

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "0.1.0"}}'
        mock_resp.__enter__.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status, detail = check_pypi_status(PACKAGE_NAME)
            self.assertEqual(status, "TAKEN")
            self.assertIn("0.1.0", detail)

    def test_check_pypi_status_http_error(self):
        from scripts.pypi_onboard import PACKAGE_NAME, check_pypi_status

        mock_err = urllib.error.HTTPError(
            url="https://pypi.org/pypi/empower-personal-dashboard/json",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            status, detail = check_pypi_status(PACKAGE_NAME)
            self.assertEqual(status, "UNKNOWN")
            self.assertIn("500", detail)

    def test_check_build_tools(self):
        from scripts.pypi_onboard import check_build_tools

        missing = check_build_tools()
        self.assertIsInstance(missing, list)

    def test_pending_publisher_hint_content(self):
        from scripts.pypi_onboard import PACKAGE_NAME, PENDING_PUBLISHER_HINT

        self.assertIn(PACKAGE_NAME, PENDING_PUBLISHER_HINT)
        self.assertIn("petry-projects", PENDING_PUBLISHER_HINT)
        self.assertIn("publish.yml", PENDING_PUBLISHER_HINT)
        self.assertIn("pypi", PENDING_PUBLISHER_HINT)

    def test_publish_workflow_structure(self):
        self.assertTrue(PUBLISH_WORKFLOW.exists(), "publish.yml workflow must exist")
        text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

        # Workflow triggers
        self.assertIn("release:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("dry_run:", text)

        # Permissions
        self.assertIn("id-token: write", text)
        self.assertIn("contents: read", text)

        # Environment
        self.assertIn("environment:", text)
        self.assertIn("name: pypi", text)

        # Action pinning to commit SHAs (no naked @v1 or @v4)
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("uses:"):
                parts = line.split("@", 1)
                self.assertEqual(len(parts), 2, f"Action reference must be pinned with @: {line}")
                ref_part = parts[1].split()[0]
                self.assertEqual(len(ref_part), 40, f"Action must be pinned to 40-character SHA: {line}")


if __name__ == "__main__":
    unittest.main()
