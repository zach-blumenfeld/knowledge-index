"""Per-vault ingest pipeline.

Process one document at a time end-to-end (parse → batch → write → release).
The single non-serial step is file reading, which uses bounded asyncio
concurrency. Neo4j writes share a single sync session (Scalability lever 5).

Order of operations per ingest run:

  1. Discover .md files under the vault root (skipping hidden / ignored dirs).
  2. Size-guard the file list; oversize files are reported, not parsed.
  3. Read all (filtered) file bytes concurrently (bounded by `concurrency`).
  4. Open a single Neo4j session.
     a. Apply schema (constraints + fulltext index).
     b. Run the per-vault upsert (User, Vault, USES_VAULT, vault LOADED).
     c. Fetch existing Document.fileHash for fileHash-skip.
     d. Build a wikilink resolver from existing docs in this vault.
  5. For each changed/new doc:
     - Parse markdown → ParsedDocument.
     - Assign URIs + Rule-1 content.
     - Build doc/section/has-section/next-section/doc-loaded row batches.
     - Write them in order. Release.
     - Update the resolver with this doc's name/aliases.
     - Stash links for the post-pass.
  6. Resolve wikilinks/markdown-links against the resolver; emit LINKS_TO.
  7. Aggregate piped-wikilink display texts per target URI, normalize, and
     union into the target's `aliases` (docs/ingest-cypher.md §4.3 step 7).

Scalability levers (docs/requirements_v01_mvp.md):
  1. fileHash skip                    — implemented in step 5
  2. configurable batch size          — `batch_size` (default 1000)
  3. concurrent file reads            — `concurrency` (default 16)
  4. one document at a time           — implicit in the loop
  5. single Neo4j write session       — `with driver.session() as s:`
  6. per-file size guard              — step 2
  +  Neo4j-OOM auto-recovery          — in `batcher.run_batched`
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiofiles

from ..config import Profile
from ..neo4j_client import driver_for, ensure_schema
from ..parser.aliases import normalize_display_texts
from ..parser.markdown import (
    ParsedDocument,
    ParsedLink,
    assign_uris_and_content,
    document_content_from,
    hash_bytes,
    parse_markdown,
)
from ..vault import (
    document_uri,
    iter_markdown_files,
    read_or_create_vault_id,
    read_vault_description,
    section_uri,
    slugify_segment,
)
from . import queries as Q
from .batcher import DEFAULT_BATCH_SIZE, run_batched
from .provenance import (
    build_load_provenance,
    build_user_mutable,
    detect_user_id,
    now_utc,
)

log = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 16
DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@dataclass
class IngestResult:
    vault_uri: str
    vault_created: bool
    docs_total: int = 0
    docs_added: int = 0
    docs_updated: int = 0
    docs_skipped_unchanged: int = 0
    docs_skipped_oversize: int = 0
    sections_written: int = 0
    links_written: int = 0
    oversize_files: list[Path] = field(default_factory=list)
    batch_shrunk_to: int | None = None  # set if we hit an OOM retry


# --- File I/O ---------------------------------------------------------------


async def _read_one(path: Path, sem: asyncio.Semaphore) -> tuple[Path, bytes]:
    async with sem:
        async with aiofiles.open(path, mode="rb") as f:
            return path, await f.read()


async def _read_all(paths: list[Path], concurrency: int) -> list[tuple[Path, bytes]]:
    sem = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*[_read_one(p, sem) for p in paths])


def _read_files_concurrent(paths: list[Path], concurrency: int) -> list[tuple[Path, bytes]]:
    if not paths:
        return []
    return asyncio.run(_read_all(paths, concurrency))


# --- Resolver ---------------------------------------------------------------


@dataclass
class WikilinkResolver:
    """name (basename, lowercased, with-or-without .md) and alias → doc_uri."""

    by_name: dict[str, str] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, aliases: list[str], doc_uri: str) -> None:
        keys = _name_lookup_keys(name)
        for k in keys:
            self.by_name.setdefault(k, doc_uri)
        for a in aliases or []:
            self.by_alias.setdefault(a.lower().strip(), doc_uri)

    def resolve(self, target: str) -> str | None:
        t = target.strip()
        # Wikilinks may carry `#section` — strip for doc-level lookup
        section_part = ""
        if "#" in t:
            t, section_part = t.split("#", 1)
        keys = _name_lookup_keys(t)
        for k in keys:
            if k in self.by_name:
                doc_uri = self.by_name[k]
                if section_part:
                    return f"{doc_uri}#{_slugify_section_path(section_part)}"
                return doc_uri
        alias_key = t.lower().strip()
        if alias_key in self.by_alias:
            doc_uri = self.by_alias[alias_key]
            if section_part:
                return f"{doc_uri}#{_slugify_section_path(section_part)}"
            return doc_uri
        return None


_MD_EXT_RE = re.compile(r"\.md$", re.IGNORECASE)


def _name_lookup_keys(name: str) -> list[str]:
    """Lookup keys for a wikilink target or document name.

    We accept "Foo", "Foo.md", "notes/Foo", and "notes/Foo.md" all to mean the
    same document. Internally everything is lowercased and stripped.
    """
    base = name.strip().lower()
    base_nomd = _MD_EXT_RE.sub("", base)
    out = {base, base_nomd}
    # Also accept just the file's basename (last path segment) for ambiguous
    # short forms — common in Obsidian.
    if "/" in base_nomd:
        out.add(base_nomd.rsplit("/", 1)[-1])
    return list(out)


def _slugify_section_path(s: str) -> str:
    return "/".join(slugify_segment(p) for p in s.split("/") if p)


# --- Row builders -----------------------------------------------------------


def _document_row(doc: ParsedDocument, doc_uri: str) -> dict[str, Any]:
    create_only: dict[str, Any] = {}
    if doc.frontmatter_created_at is not None:
        create_only["frontmatterCreatedAt"] = doc.frontmatter_created_at
    props: dict[str, Any] = {
        "name": doc.name,
        "displayName": doc.display_name,
        "aliases": doc.aliases,
        "fileHash": doc.file_hash,
        "content": document_content_from(doc),
        "sourceType": "LOCAL_FILE",
    }
    if doc.frontmatter_json is not None:
        props["frontmatter"] = doc.frontmatter_json
    return {"uri": doc_uri, "createOnly": create_only, "props": props}


def _section_row(sec) -> dict[str, Any]:
    return {
        "uri": sec.uri,
        "props": {
            "name": "/".join(sec.heading_path),
            "displayName": sec.heading_text,
            "headingLevel": sec.heading_level,
            "content": sec.content,
        },
    }


def _has_section_rows(doc: ParsedDocument, doc_uri: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for top in doc.sections:
        rows.append({"parentUri": doc_uri, "childUri": top.uri})
    for sec in doc.flat_sections:
        for child in sec.children:
            rows.append({"parentUri": sec.uri, "childUri": child.uri})
    return rows


def _next_section_rows(doc: ParsedDocument) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for prev, nxt in zip(doc.flat_sections, doc.flat_sections[1:], strict=False):
        rows.append({"srcUri": prev.uri, "tgtUri": nxt.uri})
    return rows


# --- Pipeline orchestration ------------------------------------------------


@dataclass
class IngestOptions:
    user_id: str | None = None
    profile: Profile | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    concurrency: int = DEFAULT_CONCURRENCY
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    agent_name: str | None = None


def _split_oversize(
    paths: list[Path], max_size: int
) -> tuple[list[Path], list[Path]]:
    keep: list[Path] = []
    skip: list[Path] = []
    for p in paths:
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        if sz > max_size:
            skip.append(p)
        else:
            keep.append(p)
    return keep, skip


def ingest_vault(vault_root: Path, opts: IngestOptions) -> IngestResult:
    if opts.profile is None:
        raise ValueError("IngestOptions.profile is required")
    vault_root = Path(vault_root).resolve()
    if not vault_root.exists() or not vault_root.is_dir():
        raise ValueError(f"vault path not a directory: {vault_root}")

    vault_uri, vault_created = read_or_create_vault_id(vault_root)
    vault_description = read_vault_description(vault_root)
    user_id = opts.user_id or detect_user_id()
    user_mutable = build_user_mutable()
    load_provenance = build_load_provenance(agent_name=opts.agent_name)
    load_id = str(uuid.uuid4())
    now = now_utc()

    # 1. Discover files.
    all_paths = iter_markdown_files(vault_root)
    keep, oversize = _split_oversize(all_paths, opts.max_file_size)
    for p in oversize:
        log.warning(
            "skipping oversize file (> %d bytes): %s",
            opts.max_file_size,
            p,
        )

    # 2. Concurrent reads. Bytes held in memory; the trade-off is one
    # pass of vault size — acceptable at v1 envelopes (1 GB).
    files_bytes = _read_files_concurrent(keep, opts.concurrency)

    result = IngestResult(vault_uri=vault_uri, vault_created=vault_created)
    result.docs_total = len(keep)
    result.docs_skipped_oversize = len(oversize)
    result.oversize_files = oversize

    shrink_state: dict[str, int | None] = {"size": None}

    def on_shrink(new_size: int) -> None:
        if shrink_state["size"] is None:
            log.warning(
                "Neo4j reported OOM mid-batch — shrinking to %d rows/batch. "
                "Pass --batch-size %d (or less) next run to skip this step.",
                new_size,
                new_size,
            )
        shrink_state["size"] = new_size

    with driver_for(opts.profile) as driver:
        with driver.session() as session:
            ensure_schema(session)

            # 3. Per-vault write.
            vault_mutable: dict[str, Any] = {
                "name": vault_root.name,
                "displayName": vault_root.name,
                "path": vault_root.as_posix(),
                "isObsidianVault": (vault_root / ".obsidian").exists(),
            }
            if vault_description is not None:
                vault_mutable["description"] = vault_description
            session.run(
                Q.PER_VAULT_WRITE,
                userId=user_id,
                userMutable=user_mutable,
                vaultUri=vault_uri,
                vaultMutable=vault_mutable,
                vaultLoadId=load_id,
                loadProvenance=load_provenance,
                now=now,
            ).consume()

            # 4. Existing doc hashes for fileHash-skip.
            doc_uris = [document_uri(vault_uri, p.relative_to(vault_root)) for p in keep]
            existing_hashes = _fetch_existing_hashes(session, doc_uris)

            # 5. Existing-vault resolver.
            resolver = _load_resolver(session, vault_uri)

            # 6. Per-document loop.
            pending_links: list[tuple[str, list[ParsedLink], list[tuple[str, list[ParsedLink]]]]] = []
            doc_uris_loaded_this_run: list[str] = []

            for path, content_bytes in files_bytes:
                rel_path = path.relative_to(vault_root)
                doc_uri = document_uri(vault_uri, rel_path)
                fh = hash_bytes(content_bytes)
                if existing_hashes.get(doc_uri) == fh:
                    result.docs_skipped_unchanged += 1
                    # Even though we skip writes, we'd still want this doc in the
                    # resolver — but it's already in there from _load_resolver.
                    continue

                is_new = existing_hashes.get(doc_uri) is None
                try:
                    text = content_bytes.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    log.warning("failed to decode %s; skipping", path)
                    continue

                parsed = parse_markdown(text, filename=path.name)
                parsed.file_hash = fh
                # Display name override: prefer the first non-empty H1 if present.
                if parsed.flat_sections and parsed.flat_sections[0].heading_level == 1:
                    parsed.display_name = parsed.flat_sections[0].heading_text

                # URIs + Rule-1 content.
                assign_uris_and_content(
                    parsed,
                    document_uri=doc_uri,
                    section_uri_fn=lambda hp, _d=doc_uri: section_uri(_d, hp),
                )

                # Build per-doc batches and write.
                _write_one_document(
                    session,
                    parsed=parsed,
                    doc_uri=doc_uri,
                    vault_uri=vault_uri,
                    batch_size=opts.batch_size,
                    on_shrink=on_shrink,
                )

                doc_uris_loaded_this_run.append(doc_uri)
                if is_new:
                    result.docs_added += 1
                else:
                    result.docs_updated += 1
                result.sections_written += len(parsed.flat_sections)

                # Update resolver + stash links.
                resolver.add(parsed.name, parsed.aliases, doc_uri)
                pending_links.append(
                    (
                        doc_uri,
                        parsed.document_links,
                        [(s.uri, s.links) for s in parsed.flat_sections if s.links],
                    )
                )

            # 7. Per-doc LOADED provenance — all in one batched call.
            if doc_uris_loaded_this_run:
                doc_load_rows = [{"docUri": u} for u in doc_uris_loaded_this_run]
                run_batched(
                    session,
                    Q.WRITE_DOC_LOADED,
                    "docLoadRows",
                    doc_load_rows,
                    batch_size=opts.batch_size,
                    extra_params={
                        "userId": user_id,
                        "loadId": load_id,
                        "loadProps": load_provenance,
                        "now": now,
                    },
                    on_shrink=on_shrink,
                )

            # 8. Resolve wikilinks and write LINKS_TO.
            link_rows, display_texts_per_target = _build_links_to_rows(
                pending_links, resolver
            )
            written = run_batched(
                session,
                Q.WRITE_LINKS_TO,
                "linksToRows",
                link_rows,
                batch_size=opts.batch_size,
                on_shrink=on_shrink,
            )
            result.links_written = written

            # 9. Wikilink display-text → target aliases (docs/ingest-cypher.md
            # §4.3 step 7). Runs after LINKS_TO so we can read the target's
            # current displayName + aliases for normalization, then union the
            # derived aliases without clobbering frontmatter.
            alias_rows = _build_display_text_alias_rows(
                session, display_texts_per_target
            )
            if alias_rows:
                run_batched(
                    session,
                    Q.WRITE_DISPLAY_TEXT_ALIASES,
                    "aliasRows",
                    alias_rows,
                    batch_size=opts.batch_size,
                    on_shrink=on_shrink,
                )

    result.batch_shrunk_to = shrink_state["size"]
    return result


def _write_one_document(
    session: Any,
    *,
    parsed: ParsedDocument,
    doc_uri: str,
    vault_uri: str,
    batch_size: int,
    on_shrink: Any,
) -> None:
    doc_row = _document_row(parsed, doc_uri)
    run_batched(
        session,
        Q.WRITE_DOCUMENTS,
        "documentRows",
        [doc_row],
        batch_size=batch_size,
        extra_params={"vaultUri": vault_uri, "now": now_utc()},
        on_shrink=on_shrink,
    )

    section_rows = [_section_row(s) for s in parsed.flat_sections]
    if section_rows:
        run_batched(
            session,
            Q.WRITE_SECTIONS,
            "sectionRows",
            section_rows,
            batch_size=batch_size,
            extra_params={"now": now_utc()},
            on_shrink=on_shrink,
        )

        # HAS_SECTION
        run_batched(
            session,
            Q.WRITE_HAS_SECTION,
            "hasSectionRows",
            _has_section_rows(parsed, doc_uri),
            batch_size=batch_size,
            on_shrink=on_shrink,
        )

        # Clear stale NEXT_SECTION for these sections.
        run_batched(
            session,
            Q.CLEAR_NEXT_SECTION,
            "sectionRows",
            section_rows,
            batch_size=batch_size,
            on_shrink=on_shrink,
        )

        # Rebuild NEXT_SECTION.
        next_rows = _next_section_rows(parsed)
        if next_rows:
            run_batched(
                session,
                Q.WRITE_NEXT_SECTION,
                "nextSectionRows",
                next_rows,
                batch_size=batch_size,
                on_shrink=on_shrink,
            )


def _fetch_existing_hashes(session: Any, doc_uris: list[str]) -> dict[str, str]:
    if not doc_uris:
        return {}
    res = session.run(
        "UNWIND $uris AS u MATCH (d:Document {uri: u}) RETURN d.uri AS uri, d.fileHash AS hash",
        uris=doc_uris,
    )
    return {row["uri"]: row["hash"] for row in res if row["hash"]}


def _load_resolver(session: Any, vault_uri: str) -> WikilinkResolver:
    res = session.run(
        """
        MATCH (v:Vault {uri: $vaultUri})-[:HAS_DOCUMENT]->(d:Document)
        RETURN d.uri AS uri, d.name AS name, d.aliases AS aliases
        """,
        vaultUri=vault_uri,
    )
    r = WikilinkResolver()
    for row in res:
        name = row["name"] or ""
        aliases = row["aliases"] or []
        r.add(name, aliases, row["uri"])
    return r


def _build_links_to_rows(
    pending: list[tuple[str, list, list[tuple[str, list]]]],
    resolver: WikilinkResolver,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Resolve LINKS_TO edges and collect per-target wikilink display texts.

    Returns (link_rows, display_texts_per_target). Display texts are
    aggregated independently of the LINKS_TO dedup `seen` set — even if we
    skip writing a duplicate edge, the display text on that occurrence is
    still relevant for the target's alias list (see docs/ingest-cypher.md
    §4.3 step 7).
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    display_texts: dict[str, list[str]] = {}
    for doc_uri, doc_links, section_links in pending:
        for link in doc_links:
            _process_link(doc_uri, link, resolver, seen, rows, display_texts)
        for sec_uri, links in section_links:
            for link in links:
                _process_link(sec_uri, link, resolver, seen, rows, display_texts)
    return rows, display_texts


def _build_display_text_alias_rows(
    session: Any,
    display_texts_per_target: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Normalize the per-target display-text batches and shape them for write.

    Reads each target's current (`displayName`, `aliases`) from Neo4j so the
    normalizer can drop entries that equal the target's name or are already
    in its alias list (case-insensitive). Returns one row per target whose
    normalized batch is non-empty.
    """
    if not display_texts_per_target:
        return []
    target_uris = list(display_texts_per_target.keys())
    res = session.run(
        """
        UNWIND $uris AS u
        MATCH (n {uri: u})
        WHERE n:Document OR n:Section
        RETURN n.uri AS uri,
               n.displayName AS displayName,
               coalesce(n.aliases, []) AS aliases
        """,
        uris=target_uris,
    )
    target_meta: dict[str, tuple[str | None, list[str]]] = {}
    for row in res:
        target_meta[row["uri"]] = (row["displayName"], list(row["aliases"] or []))

    rows: list[dict[str, Any]] = []
    for uri, texts in display_texts_per_target.items():
        meta = target_meta.get(uri)
        if meta is None:
            # Target wasn't written this run (e.g. WIKILINK_UNRESOLVED edge
            # case) — skip aliasing it.
            continue
        display_name, existing = meta
        new_aliases = normalize_display_texts(
            texts,
            target_display_name=display_name,
            existing_aliases=existing,
        )
        if new_aliases:
            rows.append({"uri": uri, "aliases": new_aliases})
    return rows


def _process_link(
    src_uri: str,
    link: ParsedLink,
    resolver: WikilinkResolver,
    seen: set[tuple[str, str]],
    rows: list[dict[str, Any]],
    display_texts: dict[str, list[str]],
) -> None:
    target = resolver.resolve(link.target)
    if target is None:
        return
    if target == src_uri:
        return  # self-link: skip
    # Aggregate display texts independent of LINKS_TO edge dedup — multiple
    # wikilinks from the same source to the same target should still all
    # contribute to the target's alias frequency count.
    if link.wikilink and link.display_text:
        display_texts.setdefault(target, []).append(link.display_text)
    key = (src_uri, target)
    if key in seen:
        return
    seen.add(key)
    rows.append(
        {
            "srcUri": src_uri,
            "tgtUri": target,
            "wikilink": link.wikilink,
            "embed": link.embed,
        }
    )
