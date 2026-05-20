"""Vault identity, slug rules, and URI construction.

A vault is a folder on disk. Its identity and optional user-authored metadata
live in `<vault>/.ki/vault.yaml`:

    uri: <slug>             # ki-owned, write-once per vault
    description: |          # user-authored, ki is read-only
      What this vault is for. Used as a routing hint for agents picking
      which vault to search.

`uri` is a human-readable slug derived from the vault's directory basename
on first ingest (e.g. `~/my-notes` → `my-notes`). If another vault on the
same Neo4j already claims that slug, a `-N` suffix is appended where N is
one more than the highest existing suffix in the family. Slugs are
never reused once assigned — see `find_next_vault_slug` for the algorithm.

The marker is the only state `ki` writes inside the vault; everything else
lives in `~/.config/ki/` or in Neo4j.

URI conventions (from docs/data-model.md *Path conventions*):
  - Document.uri = "<vaultUri>/<file path within vault>"     (slugified, '/' kept)
  - Section.uri  = "<vaultUri>/<file path within vault>#<slugified heading path>"

Slugification is segment-wise: each path / heading-path segment is slugified
independently, and the separator ('/' for paths, '/' for heading paths) is
preserved so the hierarchy stays queryable via prefix match.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

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


def read_vault_marker(vault_root: Path) -> dict | None:
    """Return the parsed `.ki/vault.yaml` contents, or None if absent."""
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        return None
    return _load_marker(marker)


def read_vault_uri(vault_root: Path) -> str | None:
    """Convenience: read just the `uri:` field from the marker (or None if absent)."""
    data = read_vault_marker(vault_root)
    if data is None:
        return None
    return str(data["uri"]).strip()


def write_vault_marker(
    vault_root: Path, *, uri: str, description: str | None = None
) -> None:
    """Write `.ki/vault.yaml` with the assigned URI and optional description.

    Atomic-enough for ki's purposes (single-file YAML write). Always
    rewrites the file in full — callers compose the desired state and pass
    it in. Description, if provided, is truncated to DESCRIPTION_MAX_BYTES
    with a warning per the existing soft cap.
    """
    marker = vault_marker_path(vault_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"uri": uri}
    if description is not None:
        desc = description.strip()
        encoded = desc.encode("utf-8")
        if len(encoded) > DESCRIPTION_MAX_BYTES:
            log.warning(
                "%s: `description:` is %d bytes (>%d); truncating",
                marker, len(encoded), DESCRIPTION_MAX_BYTES,
            )
            desc = encoded[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore")
        if desc:
            data["description"] = desc
    marker.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


class InvalidVaultBasenameError(ValueError):
    """The vault directory's basename doesn't slugify to anything useful.

    Carries the original basename and slug rules in the message so callers
    can show the user what to rename.
    """


def compute_base_slug(vault_root: Path) -> str:
    """Slugified directory basename — the default Vault.uri seed.

    Raises InvalidVaultBasenameError when the basename has no alphanumeric
    content to anchor a slug. A vault named `~/___` or `~/----` slugifies
    to garbage; the user should pick a descriptive name.
    """
    raw = vault_root.name
    if not raw.strip() or raw in (".", ".."):
        raise InvalidVaultBasenameError(
            f"vault directory basename {raw!r} is empty / not a real folder "
            "name. Please rename the directory to something descriptive — "
            "e.g. 'my-notes', 'work-journal', 'project-x'."
        )
    # Check the pre-slugify ASCII form for any alnum content. If there's
    # nothing alphanumeric to anchor a slug, slugify_segment would either
    # produce pure punctuation (still useless) or fall through to the
    # generic "section" sentinel (very useless).
    ascii_form = (
        unicodedata.normalize("NFKD", raw)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    if not any(c.isalnum() for c in ascii_form):
        raise InvalidVaultBasenameError(
            f"vault directory basename {raw!r} doesn't contain any "
            "alphanumeric characters to anchor a readable slug. Please "
            "rename the directory to something descriptive — e.g. "
            "'my-notes', 'work-journal', 'project-x' — so the vault has a "
            "readable identifier in URIs and in `ki tree` / `ki search` "
            "output."
        )
    return slugify_segment(raw)


_VAULT_SLUG_FAMILY_QUERY = (
    "MATCH (v:Vault) "
    "WHERE v.uri = $base OR v.uri STARTS WITH $base + '-' "
    "RETURN v.uri AS uri"
)


def find_next_vault_slug(session, base: str) -> str:
    """Find the next available slug in the {base, base-1, base-2, ...} family.

    Single Neo4j query for the family. Strategy: if `$base` itself is free,
    use it; otherwise return `${base}-{max+1}` where `max` is the highest
    integer suffix on a *currently-present* slug in the family.

    **Reuse semantics.** The algorithm operates on the graph's current
    state, so if a vault is removed (`ki rm --vault`), its slug becomes
    eligible for reassignment. Concretely: if `base`, `base-1`, `base-3`
    exist (because `-2` was removed), the next assignment is `base-4`;
    but if `base-3` is also removed before the next ingest, the family is
    `{base, base-1}` and the next assignment is `base-2`. Cross-vault
    references that pointed at a removed slug can be silently re-pointed
    at a different vault — a known trade-off documented here. If you need
    permanent never-reuse semantics, file an issue; a `:VaultTombstone`
    scheme is the natural fix.

    Concurrency: the caller `CREATE`s the chosen slug under a uniqueness
    constraint on `Vault.uri`. If a parallel writer races us to the same
    suffix, the constraint trips and the caller retries with a refreshed
    family query.
    """
    rows = list(session.run(_VAULT_SLUG_FAMILY_QUERY, base=base))
    existing = [r["uri"] for r in rows]
    pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
    max_n = -1
    for u in existing:
        m = pattern.match(u)
        if not m:
            continue
        suffix = m.group(1)
        n = 0 if suffix is None else int(suffix)
        if n > max_n:
            max_n = n
    if max_n < 0:
        return base
    return f"{base}-{max_n + 1}"


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

    The marker must already exist (assign a slug via `ingest_vault` first,
    which writes the marker, or use `write_vault_marker` directly).
    Raises `VaultDescriptionExists` when a non-empty description is already
    present and `force` is False. Values longer than 8 KB are truncated and a
    one-line warning is emitted.
    """
    marker = vault_marker_path(vault_root)
    if not marker.exists():
        raise FileNotFoundError(
            f"{marker} does not exist — initialise the vault first "
            "(write_vault_marker / ingest_vault)"
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
