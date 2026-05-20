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
     c. Compute the :Folder layer from `keep` (one entry per distinct
        directory containing an indexed doc) and write Folder nodes.
        The HAS edges that wire the tree together are written later.
     d. Fetch existing Document.fileHash for fileHash-skip.
     e. Build a wikilink resolver from existing docs in this vault
        (walks `[:HAS*]` so nested docs are included).
  5. For each changed/new doc:
     - Parse markdown → ParsedDocument.
     - Assign URIs + Rule-1 content.
     - Build doc/section/has-section/next-section/doc-loaded row batches.
     - Write them in order (nodes only — Vault|Folder -> Document HAS
       edges live in the per-vault tree-edge write below). Release.
     - Update the resolver with this doc's name/aliases.
     - Stash links for the post-pass.
  6. Write the tree HAS edges in one batch (Vault->Folder, Folder->Folder,
     Vault->Document for root docs, Folder->Document for nested docs).
     Single-parent invariant: each Folder / Document has exactly one
     incoming HAS edge.
  7. Resolve wikilinks/markdown-links against the resolver; emit LINKS_TO.
  8. Aggregate piped-wikilink display texts per target URI, normalize, and
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
from neo4j.exceptions import ClientError

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
    VaultDescriptionExists,
    compute_base_slug,
    document_uri,
    find_next_vault_slug,
    folder_uri,
    iter_markdown_files,
    read_vault_marker,
    section_uri,
    slugify_segment,
    write_vault_marker,
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
    vault_description_set: bool = False  # True iff .ki/vault.yaml had a non-empty description
    docs_total: int = 0
    docs_added: int = 0
    docs_updated: int = 0
    docs_skipped_unchanged: int = 0
    docs_skipped_oversize: int = 0
    sections_written: int = 0
    links_written: int = 0
    folders_total: int = 0  # distinct :Folder nodes touched by this ingest
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


def _document_row(doc: ParsedDocument, doc_uri: str, file_path: str) -> dict[str, Any]:
    create_only: dict[str, Any] = {}
    if doc.frontmatter_created_at is not None:
        create_only["frontmatterCreatedAt"] = doc.frontmatter_created_at
    props: dict[str, Any] = {
        "name": doc.name,
        "displayName": doc.display_name,
        "path": file_path,
        "aliases": doc.aliases,
        "fileHash": doc.file_hash,
        "content": document_content_from(doc),
        "sourceType": "LOCAL_FILE",
    }
    if doc.frontmatter_json is not None:
        props["frontmatter"] = doc.frontmatter_json
    return {"uri": doc_uri, "createOnly": create_only, "props": props}


def _section_row(sec, file_path: str) -> dict[str, Any]:
    return {
        "uri": sec.uri,
        "props": {
            "name": "/".join(sec.heading_path),
            "displayName": sec.heading_text,
            "headingLevel": sec.heading_level,
            "content": sec.content,
            "path": file_path,
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


def _build_folder_and_tree_rows(
    vault_uri: str,
    vault_root: Path,
    doc_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build :Folder upsert rows + Vault|Folder -[:HAS]-> Folder|Document edges.

    Walks each document's directory chain from vault root downward and
    materialises a :Folder for every distinct intermediate directory plus one
    HAS edge per parent → child relationship. Empty directories (no indexed
    docs under them) never appear in the result. Each Folder and Document
    gets exactly one incoming HAS edge — the single-parent invariant.

    Returns (folder_rows, tree_edge_rows):
      folder_rows    — [{ uri, props: { name, displayName, path } }, ...]
                       `path` is the absolute POSIX directory path on the
                       ingesting machine (machine-scoped, like Vault.path).
      tree_edge_rows — [{ parentUri, childUri }, ...] covering all four valid
                       endpoint shapes (Vault→Folder, Vault→Document,
                       Folder→Folder, Folder→Document).
    """
    folders: dict[str, dict[str, Any]] = {}
    tree_edges: list[dict[str, str]] = []

    for p in doc_paths:
        rel_parts = p.relative_to(vault_root).parts
        dir_parts = rel_parts[:-1]  # exclude the filename
        doc_uri_str = document_uri(vault_uri, p.relative_to(vault_root))

        if not dir_parts:
            # Root-level document: Vault -[:HAS]-> Document.
            tree_edges.append({"parentUri": vault_uri, "childUri": doc_uri_str})
            continue

        # Walk the folder chain from root downward; materialise each level the
        # first time we see it. parent_uri tracks the immediate parent for the
        # next level down so we emit a single HAS edge per child.
        parent_uri = vault_uri
        for depth in range(len(dir_parts)):
            segments = dir_parts[: depth + 1]
            f_uri = folder_uri(vault_uri, segments)
            if f_uri not in folders:
                # Compute the folder's absolute path on the ingesting machine
                # by joining vault_root with the on-disk directory parts.
                folder_abs_path = vault_root.joinpath(*dir_parts[: depth + 1])
                folders[f_uri] = {
                    "uri": f_uri,
                    "props": {
                        "name": slugify_segment(dir_parts[depth]),
                        "displayName": dir_parts[depth],
                        "path": str(folder_abs_path),
                    },
                }
                tree_edges.append({"parentUri": parent_uri, "childUri": f_uri})
            parent_uri = f_uri

        # Document's parent is the deepest folder.
        tree_edges.append({"parentUri": parent_uri, "childUri": doc_uri_str})

    return list(folders.values()), tree_edges


# --- Pipeline orchestration ------------------------------------------------


@dataclass
class IngestOptions:
    user_id: str | None = None
    profile: Profile | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    concurrency: int = DEFAULT_CONCURRENCY
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    agent_name: str | None = None
    # User-supplied description to write into `.ki/vault.yaml`. None = no
    # change (carry forward whatever's already in the marker, if anything).
    description: str | None = None
    # When `description` is set and the marker already has a non-empty
    # description, refuse unless `force_description` is True.
    force_description: bool = False


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

    # Marker handling — slug assignment moves inside the open Neo4j session
    # because collision detection needs to query the graph. Description
    # validation (--description vs existing) is still done up-front when
    # possible, but force-description semantics rely on the existing marker.
    existing_marker = read_vault_marker(vault_root)
    existing_description = (existing_marker or {}).get("description")
    if (
        opts.description is not None
        and isinstance(existing_description, str)
        and existing_description.strip()
        and not opts.force_description
    ):
        raise VaultDescriptionExists(existing_description.strip())

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

    # `result.vault_uri` / `vault_created` are filled in after slug assignment
    # inside the session below — see step 3.
    result = IngestResult(vault_uri="", vault_created=False)
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

            # 3a. Slug assignment. If `.ki/vault.yaml` already exists, honor
            # its `uri:` field unconditionally — that's how a synced vault
            # keeps the same Vault.uri across machines. Otherwise compute the
            # base slug from the directory basename and find the next
            # unclaimed `{base, base-1, base-2, ...}` in the graph.
            #
            # Concurrency: two clients racing on a fresh vault could both
            # compute the same `base-N`. The Vault.uri uniqueness constraint
            # catches that — the loser retries with a refreshed
            # `find_next_vault_slug` once. Two retries handles realistic
            # contention; if it still fails, surface the error.
            if existing_marker:
                vault_uri = str(existing_marker["uri"]).strip()
                vault_created = False
            else:
                base = compute_base_slug(vault_root)
                vault_uri = ""
                vault_created = False
                for attempt in range(2):
                    candidate = find_next_vault_slug(session, base)
                    try:
                        session.run(
                            "CREATE (v:Vault {uri: $uri}) SET v.firstSeenAt = $now",
                            uri=candidate, now=now,
                        ).consume()
                        vault_uri = candidate
                        vault_created = True
                        break
                    except ClientError:
                        if attempt == 1:
                            raise
                        log.warning(
                            "vault slug %r collided mid-write; retrying once",
                            candidate,
                        )

            # 3b. Finalize the marker now that the URI is settled.
            # Description precedence: user-supplied (--description) > existing
            # in the marker > none. force_description was already validated
            # against existing_description above.
            final_description: str | None
            if opts.description is not None:
                final_description = opts.description
            elif isinstance(existing_description, str) and existing_description.strip():
                final_description = existing_description
            else:
                final_description = None
            write_vault_marker(
                vault_root, uri=vault_uri, description=final_description,
            )
            result.vault_uri = vault_uri
            result.vault_created = vault_created
            result.vault_description_set = bool(
                final_description and final_description.strip()
            )

            # 3c. Per-vault write (User/Vault mutables + USES_VAULT + LOADED).
            vault_mutable: dict[str, Any] = {
                "name": vault_root.name,
                "displayName": vault_root.name,
                "path": vault_root.as_posix(),
                "isObsidianVault": (vault_root / ".obsidian").exists(),
            }
            if final_description is not None:
                vault_mutable["description"] = final_description
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

            # 4. Build the folder layer up-front from the kept doc paths and
            # write the Folder nodes now. The HAS edges that wire the tree
            # together (Vault|Folder -> Folder|Document) are written *after*
            # the per-doc loop in step 8, so Document nodes exist as MATCH
            # targets.
            folder_rows, tree_edge_rows = _build_folder_and_tree_rows(
                vault_uri, vault_root, keep
            )
            result.folders_total = len(folder_rows)
            if folder_rows:
                run_batched(
                    session,
                    Q.WRITE_FOLDERS,
                    "folderRows",
                    folder_rows,
                    batch_size=opts.batch_size,
                    extra_params={"now": now},
                    on_shrink=on_shrink,
                )

            # 5. Existing doc hashes for fileHash-skip.
            doc_uris = [document_uri(vault_uri, p.relative_to(vault_root)) for p in keep]
            existing_hashes = _fetch_existing_hashes(session, doc_uris)

            # 6. Existing-vault resolver.
            resolver = _load_resolver(session, vault_uri)

            # 7. Per-document loop.
            pending_links: list[tuple[str, list[ParsedLink], list[tuple[str, list[ParsedLink]]]]] = []
            doc_uris_loaded_this_run: list[str] = []
            # Path-only refresh rows for fileHash-skipped docs. Machine-scoped
            # `path` may have shifted (vault moved across mounts) even when
            # contents haven't changed, so we stamp the new path post-loop.
            path_refresh_rows: list[dict[str, str]] = []

            for path, content_bytes in files_bytes:
                rel_path = path.relative_to(vault_root)
                doc_uri = document_uri(vault_uri, rel_path)
                fh = hash_bytes(content_bytes)
                if existing_hashes.get(doc_uri) == fh:
                    result.docs_skipped_unchanged += 1
                    # Even though we skip the heavy writes, the doc's `path`
                    # property must still be refreshed because it's
                    # machine-scoped and may have moved. The resolver is
                    # already populated from _load_resolver.
                    path_refresh_rows.append({"docUri": doc_uri, "path": str(path)})
                    continue

                is_new = existing_hashes.get(doc_uri) is None
                try:
                    text = content_bytes.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    log.warning("failed to decode %s; skipping", path)
                    continue

                parsed = parse_markdown(text, filename=path.name)
                parsed.file_hash = fh
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
                    file_path=str(path),
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

            # 8. Tree HAS edges (Vault|Folder -> Folder|Document) — written
            # after the doc loop so every Document MATCH target exists. Folder
            # nodes were already written in step 4.
            if tree_edge_rows:
                run_batched(
                    session,
                    Q.WRITE_TREE_EDGES,
                    "treeEdgeRows",
                    tree_edge_rows,
                    batch_size=opts.batch_size,
                    on_shrink=on_shrink,
                )

            # 8b. Path-only refresh for fileHash-skipped docs. Their content
            # didn't change so the heavy writes were skipped, but `path` is
            # machine-scoped and may have moved across mounts — stamp it.
            # Updates Section.path too (every Section under each refreshed
            # Document, since Section.path mirrors Document.path).
            if path_refresh_rows:
                run_batched(
                    session,
                    Q.REFRESH_DOC_AND_SECTION_PATHS,
                    "pathRefreshRows",
                    path_refresh_rows,
                    batch_size=opts.batch_size,
                    on_shrink=on_shrink,
                )

            # 9. Per-doc LOADED provenance — all in one batched call.
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

            # 10. Resolve wikilinks and write LINKS_TO.
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

            # 11. Wikilink display-text → target aliases (docs/ingest-cypher.md
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
    file_path: str,
    batch_size: int,
    on_shrink: Any,
) -> None:
    doc_row = _document_row(parsed, doc_uri, file_path)
    run_batched(
        session,
        Q.WRITE_DOCUMENTS,
        "documentRows",
        [doc_row],
        batch_size=batch_size,
        extra_params={"vaultUri": vault_uri, "now": now_utc()},
        on_shrink=on_shrink,
    )

    section_rows = [_section_row(s, file_path) for s in parsed.flat_sections]
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

        # Section tree HAS edges.
        run_batched(
            session,
            Q.WRITE_SECTION_EDGES,
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
    # Walks `[:HAS*]` so docs nested under :Folder nodes are included alongside
    # root-level docs (which sit directly under the :Vault).
    res = session.run(
        """
        MATCH (v:Vault {uri: $vaultUri})-[:HAS*]->(d:Document)
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
