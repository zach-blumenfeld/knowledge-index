"""Vault identity, slug rules, and URI construction.

A vault is a folder on disk. Its identity and optional user-authored metadata
live in `<vault>/.ki/vault.yaml`:

    uri: <UUID v4>          # ki-owned, write-once
    description: |          # user-authored, ki is read-only
      What this vault is for. Used as a routing hint for agents picking
      which vault to search.

The marker is the only state `ki` writes inside the vault; everything else
lives in `~/.config/ki/` or in Neo4j. The bare-UUID `.ki/vault-id` format
used pre-0.4.0 is no longer supported (wipe + re-index to upgrade).

URI conventions (from docs/data-model.md *Path conventions*):
  - Document.uri = "<vaultId>/<file path within vault>"     (slugified, '/' kept)
  - Section.uri  = "<vaultId>/<file path within vault>#<slugified heading path>"

Slugification is segment-wise: each path / heading-path segment is slugified
independently, and the separator ('/' for paths, '/' for heading paths) is
preserved so the hierarchy stays queryable via prefix match.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

MARKER_DIR = ".ki"
MARKER_FILE = "vault.yaml"
DESCRIPTION_MAX_BYTES = 8 * 1024  # 8 KB soft cap on Vault.description


def vault_marker_path(vault_root: Path) -> Path:
    """Return the absolute path to the vault marker file."""
    return Path(vault_root) / MARKER_DIR / MARKER_FILE


def _load_marker(marker: Path) -> dict:
    """Parse `.ki/vault.yaml` and validate top-level shape.

    Returns the loaded mapping. Raises ValueError on malformed YAML or
    missing/invalid `uri:` field.
    """
    try:
        loaded = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"malformed {marker}: {e}") from e
    if loaded is None:
        raise ValueError(f"{marker} is empty — expected at least `uri:`")
    if not isinstance(loaded, dict):
        raise ValueError(f"{marker} must be a YAML mapping, got {type(loaded).__name__}")
    uri = loaded.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError(f"{marker} is missing a non-empty `uri:` field")
    return loaded


def read_or_create_vault_id(vault_root: Path) -> tuple[str, bool]:
    """Read the vault UUID from `.ki/vault.yaml`, creating one if missing.

    Returns (vault_id, created) where `created` is True on first write.
    """
    marker = vault_marker_path(vault_root)
    if marker.exists():
        data = _load_marker(marker)
        return str(data["uri"]).strip(), False
    marker.parent.mkdir(parents=True, exist_ok=True)
    vault_id = str(uuid.uuid4())
    marker.write_text(
        yaml.safe_dump({"uri": vault_id}, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return vault_id, True


def read_vault_id(vault_root: Path) -> str | None:
    """Return the existing vault UUID or None if the marker is missing."""
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        return None
    return str(_load_marker(marker)["uri"]).strip()


def read_vault_description(vault_root: Path) -> str | None:
    """Return the user-authored `description:` from `.ki/vault.yaml`, if any.

    None when the marker is missing or the field is absent / empty. Values
    longer than 8 KB are truncated and a one-line warning is emitted.
    """
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        return None
    data = _load_marker(marker)
    desc = data.get("description")
    if desc is None:
        return None
    if not isinstance(desc, str):
        log.warning(
            "%s: `description:` should be a string, got %s — ignoring",
            marker,
            type(desc).__name__,
        )
        return None
    desc = desc.strip()
    if not desc:
        return None
    encoded = desc.encode("utf-8")
    if len(encoded) > DESCRIPTION_MAX_BYTES:
        log.warning(
            "%s: `description:` is %d bytes (>%d); truncating",
            marker,
            len(encoded),
            DESCRIPTION_MAX_BYTES,
        )
        desc = encoded[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore")
    return desc


class VaultDescriptionExists(ValueError):
    """Raised by `write_vault_description` when a non-empty `description:` is
    already set and the caller didn't pass `force=True`. Carries the existing
    value so callers can echo it in error messages."""

    def __init__(self, existing: str) -> None:
        super().__init__(
            "vault already has a `description:` set; pass force=True to overwrite"
        )
        self.existing = existing


def write_vault_description(
    vault_root: Path, description: str, *, force: bool = False
) -> None:
    """Write `description:` into `.ki/vault.yaml`, preserving `uri:` + any other keys.

    The marker must already exist (call `read_or_create_vault_id` first).
    Raises `VaultDescriptionExists` when a non-empty description is already
    present and `force` is False. Values longer than 8 KB are truncated and a
    one-line warning is emitted.
    """
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        raise FileNotFoundError(
            f"{marker} does not exist — initialise the vault first "
            "(read_or_create_vault_id)"
        )
    data = _load_marker(marker)
    existing = data.get("description")
    if (
        isinstance(existing, str)
        and existing.strip()
        and not force
    ):
        raise VaultDescriptionExists(existing.strip())

    desc = (description or "").strip()
    encoded = desc.encode("utf-8")
    if len(encoded) > DESCRIPTION_MAX_BYTES:
        log.warning(
            "%s: `description:` is %d bytes (>%d); truncating",
            marker,
            len(encoded),
            DESCRIPTION_MAX_BYTES,
        )
        desc = encoded[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore")

    data["description"] = desc
    marker.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def remove_vault_marker(vault_root: Path) -> None:
    """Remove `.ki/vault.yaml` (and the `.ki/` dir if empty). Idempotent."""
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


def folder_uri(vault_id: str, segments: tuple[str, ...] | list[str]) -> str:
    """Compute Folder.uri = '<vaultId>/<slugified path>' (no trailing slash).

    `segments` is the path segments from the vault root, un-slugified. e.g.
    `('notes', 'My Projects')` → `'<vault_id>/notes/my-projects'`.
    """
    if not segments:
        raise ValueError("folder_uri requires at least one path segment")
    slugified = "/".join(slugify_segment(p) for p in segments)
    return f"{vault_id}/{slugified}"


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
