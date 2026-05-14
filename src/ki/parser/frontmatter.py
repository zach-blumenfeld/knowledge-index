"""Frontmatter extraction.

`python-frontmatter` strips a leading YAML `---` block from the markdown body
and gives us a dict. We split that into:

  - `aliases`               (list[str], from `aliases`)
  - `frontmatter_created_at`(datetime|None, from `created` or `date`)
  - `frontmatter`           (JSON-serialised blob of *unknown* keys —
                             i.e., everything we didn't otherwise lift out)

The "unknown blob" makes the property a stable string for indexing without
forcing a rigid schema onto the user's metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import frontmatter as _frontmatter

ALIAS_KEYS = ("aliases", "alias")
CREATED_KEYS = ("created", "date", "createdAt")


@dataclass
class FrontmatterFields:
    aliases: list[str]
    frontmatter_created_at: datetime | None
    frontmatter_json: str | None  # JSON-serialised "everything else", or None if empty
    body: str  # markdown content with the frontmatter stripped


def _coerce_datetime(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _coerce_aliases(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None]
    return []


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    return str(o)


def parse_frontmatter(text: str) -> FrontmatterFields:
    """Parse YAML frontmatter from a markdown string and split it into fields."""
    post = _frontmatter.loads(text)
    meta: dict[str, Any] = dict(post.metadata or {})

    aliases: list[str] = []
    for k in ALIAS_KEYS:
        if k in meta:
            aliases = _coerce_aliases(meta.pop(k))
            break

    created_at: datetime | None = None
    for k in CREATED_KEYS:
        if k in meta:
            created_at = _coerce_datetime(meta.pop(k))
            if created_at is not None:
                break

    blob = json.dumps(meta, default=_json_default, sort_keys=True) if meta else None

    return FrontmatterFields(
        aliases=aliases,
        frontmatter_created_at=created_at,
        frontmatter_json=blob,
        body=post.content,
    )
