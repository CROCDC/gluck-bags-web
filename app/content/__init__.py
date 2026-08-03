"""Editable site copy: the registry of every string plus its resolver.

Public surface:
    from app.content import t, t_lines, register_content
    from app.content import registry            # the catalogue itself

See docs/CONTENT-ADMIN.md for how to add a new editable string.
"""

from app.content import registry
from app.content.resolver import (
    brand,
    category_intro,
    category_intro_editable,
    category_label,
    category_label_editable,
    category_tagline,
    category_tagline_editable,
    field_state,
    group_states,
    instagram_url,
    is_preview,
    override_count,
    pending_draft_count,
    register_content,
    t,
    t_lines,
    tagline,
)
from app.content.sanitizer import sanitize, strip_tags

__all__ = [
    "brand",
    "category_intro",
    "category_intro_editable",
    "category_label",
    "category_label_editable",
    "category_tagline",
    "category_tagline_editable",
    "field_state",
    "group_states",
    "instagram_url",
    "is_preview",
    "override_count",
    "pending_draft_count",
    "register_content",
    "registry",
    "sanitize",
    "strip_tags",
    "t",
    "t_lines",
    "tagline",
]
