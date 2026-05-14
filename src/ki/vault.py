"""Vault identity, slug rules, and URI construction.

A vault is a folder on disk. Its `Vault.uri` is a UUID v4 stored in
`<vault>/.ki/vault-id`. The marker is the only state `ki` writes inside the
vault; everything else lives in `~/.config/ki/` or in Neo4j.

URI conventions (from docs/data-model.md *Path conventions*):
  - Document.uri = "<vaultId>/<file path within vault>"     (slugified, '/' kept)
  - Section.uri  = "<vaultId>/<file path within vault>#<slugified heading path>"

Slugification is segment-wise: each path / heading-path segment is slugified
independently, and the separator ('/' for paths, '/' for heading paths) is
preserved so the hierarchy stays queryable via prefix match.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path

MARKER_DIR = ".ki"
MARKER_FILE = "vault-id"


def vault_marker_path(vault_root: Path) -> Path:
    """Return the absolute path to the vault marker file."""
    return Path(vault_root) / MARKER_DIR / MARKER_FILE


def read_or_create_vault_id(vault_root: Path) -> tuple[str, bool]:
    """Read the vault UUID from the marker, creating one if missing.

    Returns (vault_id, created) where `created` is True on first write.
    """
    marker = vault_marker_path(vault_root)
    if marker.exists():
        existing = marker.read_text(encoding="utf-8").strip()
        if existing:
            return existing, False
    marker.parent.mkdir(parents=True, exist_ok=True)
    vault_id = str(uuid.uuid4())
    marker.write_text(vault_id + "\n", encoding="utf-8")
    return vault_id, True


def read_vault_id(vault_root: Path) -> str | None:
    """Return the existing vault UUID or None if the marker is missing."""
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        return None
    txt = marker.read_text(encoding="utf-8").strip()
    return txt or None


def remove_vault_marker(vault_root: Path) -> None:
    """Remove `.ki/vault-id` (and the `.ki/` dir if empty). Idempotent."""
    marker = vault_marker_path(vault_root)
    if marker.exists():
        marker.unlink()
    parent = marker.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


# Slug rules — segment-wise. Each segment is normalised to ASCII, lowercased,
# whitespace → '-', non-alphanumeric (except '_', '-', '.') stripped, and
# collapsed. Empty segments fall back to "section".
_SLUG_REPLACE_RE = re.compile(r"[^\w.\-]+")
_SLUG_COLLAPSE_RE = re.compile(r"-+")


def slugify_segment(s: str) -> str:
    """Slugify a single path/heading segment. Preserves '_' and '.'."""
    ascii_only = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    out = ascii_only.lower()
    out = _SLUG_REPLACE_RE.sub("-", out)
    out = _SLUG_COLLAPSE_RE.sub("-", out)
    out = out.strip("-")
    return out or "section"


def slugify_path(rel_path: str) -> str:
    """Slugify a relative path segment-wise, preserving '/' as separator."""
    parts = [slugify_segment(p) for p in rel_path.split("/") if p]
    return "/".join(parts)


def document_uri(vault_id: str, rel_path: Path | str) -> str:
    """Compute Document.uri = '<vaultId>/<slugified relative path>'."""
    rel = Path(rel_path).as_posix() if isinstance(rel_path, Path) else rel_path
    rel = rel.lstrip("/")
    return f"{vault_id}/{slugify_path(rel)}"


def section_uri(doc_uri: str, heading_path: list[str]) -> str:
    """Compute Section.uri = '<doc_uri>#<slugified heading path>'.

    `heading_path` is a list of disambiguated heading slugs (already
    post-processed for duplicate-at-same-level disambiguation per
    docs/data-model.md *Content Construction Rules* Rule 3).
    """
    return f"{doc_uri}#{'/'.join(heading_path)}"


def is_hidden_segment(name: str) -> bool:
    """Whether a path segment is a hidden (`.`-prefixed) one that we should skip."""
    return name.startswith(".") and name not in (".", "..")


_DEFAULT_IGNORE_DIRS = {
    ".git",
    ".obsidian",
    ".ki",
    ".trash",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
}


def iter_markdown_files(vault_root: Path) -> list[Path]:
    """Return all markdown files under vault_root, sorted, skipping hidden dirs."""
    root = Path(vault_root).resolve()
    out: list[Path] = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part in _DEFAULT_IGNORE_DIRS or is_hidden_segment(part) for part in rel_parts[:-1]):
            continue
        out.append(p)
    out.sort(key=lambda x: x.as_posix())
    return out
