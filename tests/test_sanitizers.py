"""
test_sanitizers.py — Unit tests for text sanitizers.
"""

import unittest
from empower_personal_dashboard.sanitizers import clean_api_text


class TestSanitizers(unittest.TestCase):
    def test_clean_api_text_removes_replacement_char(self):
        dirty = "Standard\ufffd Trust\ufffd Index Fund"
        cleaned = clean_api_text(dirty)
        self.assertEqual(cleaned, "Standard Trust Index Fund")
        self.assertNotIn("\ufffd", cleaned)

    def test_clean_api_text_handles_none_and_empty(self):
        self.assertEqual(clean_api_text(None), "")
        self.assertEqual(clean_api_text(""), "")

    def test_clean_api_text_collapses_whitespace(self):
        messy = "Global    Market    Bond"
        self.assertEqual(clean_api_text(messy), "Global Market Bond")


if __name__ == "__main__":
    unittest.main()
