"""
sanitizers.py — Text sanitization utilities for Empower Personal Dashboard API.
"""

import re
from typing import Any


def clean_api_text(val: Any) -> str:
    """
    Sanitize API text fields to remove mojibake artifacts such as U+FFFD
    and collapse irregular whitespace.

    Upstream broker and aggregator feeds occasionally inject Unicode replacement
    characters (U+FFFD) where trademark or copyright symbols originally appeared.
    """
    if val is None:
        return ""
    s = str(val)
    # Remove U+FFFD replacement characters
    s = s.replace("\ufffd", "")
    # Collapse multiple consecutive spaces
    s = re.sub(r" {2,}", " ", s)
    return s.strip()
