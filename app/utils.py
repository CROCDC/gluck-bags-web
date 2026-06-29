from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """URL-safe slug: 'Mini Bag' -> 'mini-bag'. Strips accents, lowercases and
    collapses whitespace/separators to single hyphens."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)
