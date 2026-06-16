#!/usr/bin/env python3
"""
Deterministic Obsidian-style markdown vault generator for `ki` testing.

Usage:
    uv run python scripts/gen_test_vault.py \\
        --size {tiny,small,medium,large} \\
        --output ./out/vault-large/ \\
        --seed 42 \\
        [--zip]

Identical --seed produces byte-identical file contents across runs. The only
wall-clock value lives in `README.md` ("Generated at ..."); everything else is
pinned to a seed-derived value, including frontmatter `created:` timestamps
and the `.ki/vault.yaml` UUID.

Deps (PyPI): python-frontmatter, PyYAML. Already listed in pyproject.toml.
For ad-hoc runs outside the project venv:

    uv run --with python-frontmatter --with pyyaml \\
        python scripts/gen_test_vault.py ...

See prompts/build-test-vault.md and docs/archive/requirements_v01_mvp.md §Scalability for the
sizing contract this script implements.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import re
import sys
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import yaml  # PyYAML

# ──────────────────────────────────────────────────────────────────────────────
# Size envelopes
# ──────────────────────────────────────────────────────────────────────────────

SIZE_CONFIG: dict[str, dict] = {
    "tiny": {
        "files": 20,
        "total_bytes": 100_000,
        "max_file_bytes": 10_000,
        "max_heading_depth": 4,
        "topics": 50,
        "oversized_file": False,
        "ten_thousand_section_doc": False,
        "hub_500_backlinks": False,
    },
    "small": {
        "files": 200,
        "total_bytes": 10_000_000,
        "max_file_bytes": 100_000,
        "max_heading_depth": 4,
        "topics": 150,
        "oversized_file": False,
        "ten_thousand_section_doc": False,
        "hub_500_backlinks": False,
    },
    "medium": {
        "files": 2_000,
        "total_bytes": 200_000_000,
        "max_file_bytes": 500_000,
        "max_heading_depth": 6,
        "topics": 300,
        "oversized_file": True,
        "ten_thousand_section_doc": False,
        "hub_500_backlinks": False,
    },
    "large": {
        "files": 10_000,
        "total_bytes": 1_000_000_000,
        "max_file_bytes": 1_000_000,
        "max_heading_depth": 6,
        "topics": 500,
        "oversized_file": True,
        "ten_thousand_section_doc": True,
        "hub_500_backlinks": True,
    },
}

CORPUS_PATH = Path(__file__).parent / "test_vault_corpus.txt"


# ──────────────────────────────────────────────────────────────────────────────
# Slugification — matches docs/data-model/schema.md Path conventions
# ──────────────────────────────────────────────────────────────────────────────

def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# ──────────────────────────────────────────────────────────────────────────────
# Deterministic UUID v4 (no os.urandom)
# ──────────────────────────────────────────────────────────────────────────────

def det_uuid4(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


# ──────────────────────────────────────────────────────────────────────────────
# Markov chain — hand-rolled bigram over the bundled corpus
# ──────────────────────────────────────────────────────────────────────────────

class Markov:
    """Bigram Markov chain.

    Deterministic when fed a seeded `random.Random`. Internal candidate lists
    are sorted, so generation does not depend on dict/set hash iteration order.
    """

    def __init__(self, corpus_text: str, rng: random.Random, n: int = 2):
        self.n = n
        self.rng = rng
        cleaned = re.sub(r"\s+", " ", corpus_text).strip()
        tokens = cleaned.split()
        chain: dict[tuple[str, ...], list[str]] = {}
        for i in range(len(tokens) - n):
            key = tuple(tokens[i:i + n])
            chain.setdefault(key, []).append(tokens[i + n])
        self.chain = OrderedDict(
            (k, sorted(v)) for k, v in sorted(chain.items())
        )
        starters = [
            k for k in self.chain
            if k[0] and k[0][0].isalpha() and k[0][0].isupper()
        ]
        self.starters = sorted(starters) if starters else sorted(self.chain.keys())

    def _next_state(self, out: list[str]) -> tuple[str, ...]:
        return tuple(out[-self.n:])

    def paragraph(self, n_words: int) -> str:
        """Roughly `n_words` of prose, with sensible sentence-end capitalisation."""
        state = self.rng.choice(self.starters)
        out: list[str] = list(state)
        while len(out) < n_words:
            candidates = self.chain.get(state)
            if not candidates:
                state = self.rng.choice(self.starters)
                out.extend(state)
                continue
            out.append(self.rng.choice(candidates))
            state = self._next_state(out)
        # Trim to exactly n_words and finish on a period.
        words = out[:n_words]
        text = " ".join(words).rstrip(",;:")
        if not text.endswith((".", "!", "?")):
            text += "."
        return text


# ──────────────────────────────────────────────────────────────────────────────
# Topic-graph synthesis — drives both folder structure and wikilink targets
# ──────────────────────────────────────────────────────────────────────────────

# Curated noun stems + modifiers. Cartesian product gives plenty of unique
# topic names for any size; deterministic shuffle picks the first N.
TOPIC_NOUNS = [
    "distributed-systems", "machine-learning", "type-theory", "graph-algorithms",
    "functional-programming", "information-retrieval", "database-design",
    "operating-systems", "compilers", "version-control", "cryptography",
    "networking", "concurrency", "garbage-collection", "lambda-calculus",
    "virtualization", "cloud-computing", "observability", "consensus",
    "transactions", "caches", "compression", "search", "embeddings",
    "vector-indexes", "fulltext-indexes", "neo4j", "postgres", "redis",
    "kafka", "spark", "rust", "go", "python", "javascript", "typescript",
    "clojure", "haskell", "ocaml", "lisp", "smalltalk", "simula",
    "unix", "linux", "macos", "windows", "kubernetes", "docker",
    "wasm", "llvm", "regex", "parsing", "interpreters", "memory-models",
    "threading", "actors", "csp", "stm", "streams", "pipelines",
    "etl", "data-warehouses", "lakehouses", "bloom-filters", "skip-lists",
    "btrees", "lsm-trees", "wal", "raft", "paxos", "vector-clocks",
    "lamport-clocks", "merkle-trees", "hashing", "rsa", "ecc",
    "tls", "http", "quic", "tcp", "udp", "websockets", "grpc",
    "graphql", "rest", "soap", "rpc", "proto-buffers", "avro",
    "json", "yaml", "toml", "markdown", "asciidoc", "latex",
    "wikis", "obsidian", "logseq", "roam", "zettelkasten", "notes",
    "agents", "memory", "tools", "prompts", "retrieval", "indexing",
]

TOPIC_MODIFIERS = [
    "overview", "internals", "patterns", "advanced", "history",
    "in-practice", "case-study", "deep-dive", "tutorial", "notes",
    "primer", "field-guide", "reference", "cheat-sheet", "design",
    "architecture", "performance", "scaling", "debugging", "testing",
    "anti-patterns", "war-stories", "first-principles", "essentials",
    "in-2026", "for-engineers", "review", "compendium", "reflections",
    "lessons-learned",
]


@dataclass
class Topic:
    id: int
    name: str             # display name, may contain spaces or capitals
    slug: str             # filesystem-safe lowercase-dash
    folder: str           # POSIX-relative folder path within vault
    has_doc: bool         # True if a procedural doc materialises this topic
    aliases: tuple[str, ...] = ()
    neighbors: tuple[int, ...] = ()


def build_topic_names(n: int, rng: random.Random) -> list[str]:
    """Deterministic list of `n` unique topic display names."""
    base = sorted({f"{m} {b}".replace("-", " ") for m in TOPIC_MODIFIERS for b in TOPIC_NOUNS})
    # Stable shuffle. We need way more than `n` and a long-lived candidate pool.
    rng.shuffle(base)
    if len(base) < n:
        raise RuntimeError(f"Topic pool too small: have {len(base)} need {n}")
    return base[:n]


def build_topic_graph(size_cfg: dict, rng: random.Random) -> list[Topic]:
    """Build the topic graph: nodes + directed edges (avg out-degree ~4).

    Cycles are emitted deliberately so `LINKS_TO` has shortest-path cases
    that include them. Some topics are densely connected (hubs)."""
    n = size_cfg["topics"]
    names = build_topic_names(n, rng)
    # Folder buckets — gives nested structure without forcing every doc N deep.
    # `Notes` (capital N) matches the case used by FIXED_EDGE_CASE_PATHS
    # below — without this, procedural topics under "notes/" and fixed
    # paths under "Notes/" create two distinct directories on
    # case-sensitive filesystems (Linux CI) but collapse into one on
    # case-insensitive ones (macOS default), breaking reproducibility of
    # the committed fixture across platforms.
    buckets = ["Notes", "tech", "science", "history", "philosophy", "inbox"]
    topics: list[Topic] = []
    for i, name in enumerate(names):
        slug = slugify(name)
        bucket = buckets[i % len(buckets)]
        # Half the topics get a 2-level folder for variety; combined with the
        # edge-case docs (which always have 3+ levels) this satisfies the
        # "nested directories" guarantee.
        if i % 2 == 0:
            folder = f"{bucket}/{slug[0]}"
        else:
            folder = bucket
        topics.append(Topic(id=i, name=name, slug=slug, folder=folder, has_doc=True))

    # Some topics are doc-less — they appear only as wikilink targets via
    # aliases or as unresolved targets. For tiny we keep ~60% with docs.
    n_files = size_cfg["files"]
    # The edge-case docs (~14) eat into the procedural count; ensure the
    # topic graph has at least n_files - edge_case_count topics with docs.
    procedural_doc_slots = max(0, n_files - len(FIXED_EDGE_CASE_PATHS))
    # First `procedural_doc_slots` topics get docs; the rest are unresolved /
    # alias-only. (Cap at n; if files > topics we'll generate extra docs
    # per-topic later.)
    if procedural_doc_slots < n:
        for t in topics[procedural_doc_slots:]:
            t.has_doc = False

    # Edges: avg out-degree ~4. For each topic, pick ~4 distinct successors.
    for t in topics:
        out_degree = max(1, int(rng.gauss(4, 1)))
        out_degree = min(out_degree, n - 1, 8)
        candidates = [j for j in range(n) if j != t.id]
        rng.shuffle(candidates)
        t.neighbors = tuple(candidates[:out_degree])

    # Hand a few topics extra aliases so alias-based wikilink resolution has
    # plausible material at every size.
    for t in topics[: max(2, n // 25)]:
        # Aliases are deterministic transformations of the topic name.
        words = t.name.split()
        acronym = "".join(w[0].upper() for w in words if w)
        t.aliases = tuple(sorted({acronym, " ".join(w.capitalize() for w in words)}))

    return topics


# ──────────────────────────────────────────────────────────────────────────────
# Edge-case docs — fixed paths, always present at every size
# ──────────────────────────────────────────────────────────────────────────────

# Order matters for determinism. Paths are POSIX-relative within the vault.
# Some folders intentionally include spaces and capitals to exercise URI
# slugification (Path conventions in docs/data-model/schema.md).
FIXED_EDGE_CASE_PATHS: list[str] = [
    "Notes/My Projects/big-idea.md",        # 3-level nesting + slugified folder names
    "Notes/People/Jane Doe/bio.md",         # 4-level nesting + slugified folder
    "concepts/heading-skip.md",             # H1 -> H3 skip
    "concepts/duplicate-headings.md",       # two ## Installation
    "references/35th-president.md",         # alias-only target ("JFK")
    "references/README.md",                 # folder-note pattern (README)
    "Notes/My Projects/_index.md",          # folder-note pattern (_index)
    "inbox/cycle-a.md",                     # cycle A->B
    "inbox/cycle-b.md",                     # cycle B->A
    "inbox/dense-hub.md",                   # ~20 wikilinks in one section
    "inbox/url-links.md",                   # markdown URL links
    "daily/no-frontmatter.md",              # body but no frontmatter
    "daily/frontmatter-only.md",            # frontmatter only, no body
    "daily/empty-section.md",               # heading with no body
    "daily/unresolved-links.md",            # [[Nonexistent Page]]
]

# A doc that wikilinks to "JFK" via alias — alias-resolution path.
# We pick one fixed edge-case doc to carry this link so tests can assert it.
ALIAS_REFERRING_DOC = "Notes/My Projects/big-idea.md"
# A doc that wikilinks to "Nonexistent Page" — unresolved path.
UNRESOLVED_REFERRING_DOC = "daily/unresolved-links.md"


# ──────────────────────────────────────────────────────────────────────────────
# Markdown emission
# ──────────────────────────────────────────────────────────────────────────────

def yaml_frontmatter_block(meta: dict) -> str:
    """PyYAML with sort_keys=True is the simplest deterministic option here;
    python-frontmatter's `dumps` wraps PyYAML but adds its own normalisation
    quirks we don't need."""
    body = yaml.safe_dump(
        meta,
        sort_keys=True,                # determinism — never rely on dict insertion order
        default_flow_style=False,
        allow_unicode=True,
        width=1000,
    )
    return f"---\n{body}---\n"


def make_heading_text(rng: random.Random) -> str:
    """Deterministic faux-business heading drawn from the curated topic pools."""
    pool = TOPIC_MODIFIERS + TOPIC_NOUNS
    n = rng.randint(2, 4)
    words = [rng.choice(pool) for _ in range(n)]
    return " ".join(w.replace("-", " ") for w in words).title()


def emit_doc(
    *,
    body_words: int,
    section_count: int,
    max_heading_depth: int,
    wikilink_slugs: list[str],
    embed_slugs: list[str],
    markov: Markov,
    rng: random.Random,
    title: str,
    frontmatter_meta: dict | None,
    extra_lines: list[str] | None = None,
) -> str:
    """Compose a markdown file: optional frontmatter, H1 title, then sections.

    Wikilinks (`[[slug]]`) and embeds (`![[slug]]`) are interspersed into the
    section bodies at deterministic positions.
    """
    parts: list[str] = []
    if frontmatter_meta:
        parts.append(yaml_frontmatter_block(frontmatter_meta))

    parts.append(f"# {title}\n")
    # Preamble paragraph above the first H2.
    if body_words > 0:
        parts.append(markov.paragraph(min(80, max(20, body_words // (section_count + 1)))))
        parts.append("")

    # Build sections. Use H2 as the default; sprinkle deeper headings as we go.
    remaining = body_words
    sections_to_emit = max(1, section_count)
    # Pre-compute wikilink / embed indices so they distribute evenly.
    insertion_points: list[tuple[int, str, bool]] = []
    for i, slug in enumerate(wikilink_slugs):
        sec_idx = i % sections_to_emit
        insertion_points.append((sec_idx, slug, False))
    for i, slug in enumerate(embed_slugs):
        sec_idx = (i + sections_to_emit // 2) % sections_to_emit
        insertion_points.append((sec_idx, slug, True))

    # Group insertions by section index.
    by_section: dict[int, list[tuple[str, bool]]] = {}
    for sec_idx, slug, is_embed in insertion_points:
        by_section.setdefault(sec_idx, []).append((slug, is_embed))

    for s in range(sections_to_emit):
        # Vary heading depth slightly for medium/large.
        depth = 2
        if max_heading_depth >= 3 and s > 0 and s % 3 == 0:
            depth = 3
        if max_heading_depth >= 4 and s > 0 and s % 7 == 0:
            depth = 4
        if max_heading_depth >= 5 and s > 0 and s % 11 == 0:
            depth = 5
        if max_heading_depth >= 6 and s > 0 and s % 13 == 0:
            depth = 6
        heading = "#" * depth + f" {make_heading_text(rng)}"
        parts.append(heading)
        parts.append("")
        # Section budget: fair share of remaining words. Split across one or
        # more paragraphs each capped at 500 words (matches the spec's
        # "50-500 words" per-paragraph guidance).
        section_budget = max(20, remaining // max(1, sections_to_emit - s))
        n_paragraphs = max(1, (section_budget + 499) // 500)
        words_per_para = section_budget // n_paragraphs
        words_per_para = max(20, min(500, words_per_para))
        paragraphs: list[str] = []
        for _ in range(n_paragraphs):
            paragraphs.append(markov.paragraph(words_per_para))
        para_text = "\n\n".join(paragraphs)
        # Insert any wikilinks/embeds for this section by appending them to
        # word positions deterministically across the section body.
        links = by_section.get(s, [])
        if links:
            tokens = para_text.split(" ")
            stride = max(1, len(tokens) // (len(links) + 1))
            for idx, (slug, is_embed) in enumerate(links):
                pos = min(len(tokens) - 1, (idx + 1) * stride)
                wl = f"![[{slug}]]" if is_embed else f"[[{slug}]]"
                tokens[pos] = f"{tokens[pos]} {wl}"
            para_text = " ".join(tokens)
        parts.append(para_text)
        parts.append("")
        remaining -= words_per_para * n_paragraphs
        if remaining <= 0:
            break

    if extra_lines:
        parts.extend(extra_lines)

    text = "\n".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


def emit_edge_case_doc(
    path: str,
    *,
    topics: list[Topic],
    topic_by_slug: dict[str, Topic],
    markov: Markov,
    rng: random.Random,
    base_date: dt.datetime,
    alias_target_slug: str,
) -> str:
    """Hand-shaped content for each fixed edge-case path.

    Each edge-case doc is ~1.5–3 KB so that the cumulative size for tiny lands
    near the byte target. The structural feature each doc demonstrates is
    preserved verbatim; the surrounding prose is markov-generated padding.
    """
    # Helpers — deterministic timestamp keyed off the path so frontmatter is
    # stable across runs without depending on wall clock.
    def created_at_for(p: str) -> str:
        offset = sum(ord(c) for c in p) % 730
        return (base_date + dt.timedelta(days=offset)).isoformat()

    def meta_with(extra: dict | None = None) -> dict:
        meta: dict = {"created": created_at_for(path), "tags": ["fixture", "edge-case"]}
        if extra:
            meta.update(extra)
        return meta

    if path == "Notes/My Projects/big-idea.md":
        # Has aliases, alias-resolved wikilink to JFK, dense wikilinks to neighbours.
        meta = meta_with({"aliases": ["Big Idea", "BI"]})
        return (
            yaml_frontmatter_block(meta)
            + "# Big Idea\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## Origin\n\n"
            + markov.paragraph(260) + " See [[JFK]] for the canonical sketch.\n\n"
            + "## Plan\n\n"
            + markov.paragraph(260) + " Cross-references: [[cycle-a]], [[dense-hub]].\n"
        )

    if path == "Notes/People/Jane Doe/bio.md":
        meta = meta_with({"aliases": ["Jane", "J. Doe"]})
        return (
            yaml_frontmatter_block(meta)
            + "# Jane Doe\n\n"
            + markov.paragraph(240) + "\n\n"
            + "## Career\n\n"
            + markov.paragraph(260) + " See [[big-idea]].\n"
        )

    if path == "concepts/heading-skip.md":
        # H1 -> H3 (no H2). Tests skipped-level handling per Path conventions Rule 2.
        return (
            "# Heading Skip\n\n"
            + markov.paragraph(260) + "\n\n"
            + "### Skipped Down To H3\n\n"
            + markov.paragraph(260) + "\n\n"
            + "#### Deeper Still\n\n"
            + markov.paragraph(260) + "\n"
        )

    if path == "concepts/duplicate-headings.md":
        # Two ## Installation sections — must disambiguate to #installation and
        # #installation-1 (Rule 3).
        return (
            "# Duplicate Headings\n\n"
            + markov.paragraph(200) + "\n\n"
            + "## Installation\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## Installation\n\n"
            + markov.paragraph(260) + "\n"
        )

    if path == "references/35th-president.md":
        # Alias-only target: filename does NOT contain "JFK", but aliases do.
        meta = meta_with({"aliases": ["JFK", "John F Kennedy"]})
        return (
            yaml_frontmatter_block(meta)
            + "# Thirty-fifth President\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## Biography\n\n"
            + markov.paragraph(260) + "\n"
        )

    if path == "references/README.md":
        # Folder-note pattern (README inside subdir).
        return (
            "# References\n\n"
            + markov.paragraph(240) + "\n\n"
            + "## Index\n\n"
            + markov.paragraph(200) + "\n"
        )

    if path == "Notes/My Projects/_index.md":
        # Folder-note pattern (_index inside subdir, slugifiable folder name).
        return (
            "# My Projects\n\n"
            + markov.paragraph(240) + "\n\n"
            + "## Active\n\n"
            + markov.paragraph(200) + "\n"
        )

    if path == "inbox/cycle-a.md":
        return (
            "# Cycle A\n\n"
            + markov.paragraph(240) + " See [[cycle-b]].\n\n"
            + "## Notes\n\n"
            + markov.paragraph(200) + "\n"
        )

    if path == "inbox/cycle-b.md":
        return (
            "# Cycle B\n\n"
            + markov.paragraph(240) + " See [[cycle-a]].\n\n"
            + "## Notes\n\n"
            + markov.paragraph(200) + "\n"
        )

    if path == "inbox/dense-hub.md":
        # ~20 wikilinks in one section — exercises dense LINKS_TO. We don't
        # filter to has_doc topics: mixing resolved, alias-resolved, and
        # unresolved targets is a feature, not a bug (the parser is supposed
        # to handle all three).
        link_slugs: list[str] = []
        for t in topics:
            if t.slug:
                link_slugs.append(t.slug)
            if len(link_slugs) >= 20:
                break
        link_block = " ".join(f"[[{s}]]" for s in link_slugs)
        return (
            "# Dense Hub\n\n"
            + markov.paragraph(240) + "\n\n"
            + "## Many Links\n\n"
            + link_block + "\n\n"
            + markov.paragraph(200) + "\n"
        )

    if path == "inbox/url-links.md":
        return (
            "# URL Links\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## External References\n\n"
            + "See [the spec](https://example.com/spec), "
            + "[the implementation guide](https://example.com/impl/v1), and "
            + "[the changelog](https://example.com/changelog).\n\n"
            + markov.paragraph(260) + "\n"
        )

    if path == "daily/no-frontmatter.md":
        return (
            "# Daily Note\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## Today\n\n"
            + markov.paragraph(240) + "\n"
        )

    if path == "daily/frontmatter-only.md":
        meta = meta_with({"aliases": ["frontmatter-only"]})
        return yaml_frontmatter_block(meta)

    if path == "daily/empty-section.md":
        return (
            "# Empty Section\n\n"
            + markov.paragraph(260) + "\n\n"
            + "## Empty\n\n"
            + "## Not Empty\n\n"
            + markov.paragraph(240) + "\n"
        )

    if path == "daily/unresolved-links.md":
        return (
            "# Unresolved Links\n\n"
            + markov.paragraph(260) + " See [[Nonexistent Page]] for details.\n"
        )

    raise ValueError(f"Unknown edge-case path: {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Procedural docs
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DocPlan:
    rel_path: str
    topic: Topic
    body_target: int                # target body bytes
    section_count: int
    frontmatter: dict | None
    wikilink_slugs: list[str]
    embed_slugs: list[str]


def plan_procedural_docs(
    topics: list[Topic],
    size_cfg: dict,
    procedural_byte_budget: int,
    rng: random.Random,
    base_date: dt.datetime,
) -> list[DocPlan]:
    """Plan one DocPlan per procedural slot.

    Procedural docs round out the file count to the size target. Each is
    attached to a primary Topic; wikilinks are sampled from the topic's
    neighbours. About 30% carry frontmatter.
    """
    n_files = size_cfg["files"]
    n_procedural = max(0, n_files - len(FIXED_EDGE_CASE_PATHS))
    if size_cfg["oversized_file"]:
        n_procedural -= 1
    if size_cfg["ten_thousand_section_doc"]:
        n_procedural -= 1
    n_procedural = max(0, n_procedural)
    if n_procedural == 0:
        return []

    topics_with_doc = [t for t in topics if t.has_doc]
    # Jitter mean is 0.85; we divide by it so the realised average bytes per
    # doc matches `procedural_budget / n_procedural`.
    jitter_lo, jitter_hi = 0.7, 1.0
    jitter_mean = (jitter_lo + jitter_hi) / 2
    avg_body = max(200, procedural_byte_budget // max(1, n_procedural))
    avg_body = int(avg_body / jitter_mean)
    # Leave headroom for headings, frontmatter, wikilinks, and the words-to-bytes
    # slop in the markov sampler. Setting this firmly under the per-file cap is
    # what keeps the procedural docs comfortably below `max_file_bytes`. The
    # 0.80 coefficient + 800-byte slack was tuned by empirical measurement:
    # markov runs ~6.35 bytes per word and the per-doc overhead (frontmatter +
    # headings + wikilink slugs) can reach 800 bytes on a wikilink-dense doc.
    max_body = max(200, int(size_cfg["max_file_bytes"] * 0.80) - 800)
    plans: list[DocPlan] = []

    for i in range(n_procedural):
        topic = topics_with_doc[i % len(topics_with_doc)]
        # If multiple docs share a topic (large sizes), disambiguate by suffix.
        suffix_n = i // len(topics_with_doc)
        if suffix_n > 0:
            doc_slug = f"{topic.slug}-{suffix_n + 1}"
        else:
            doc_slug = topic.slug
        rel_path = f"{topic.folder}/{doc_slug}.md"

        # Body target — jittered around average, bounded by max.
        jitter = rng.uniform(jitter_lo, jitter_hi)
        body_target = int(min(max_body, avg_body * jitter))
        body_target = max(200, body_target)

        # Section count scales loosely with body size.
        section_count = max(2, min(20, body_target // 1500))

        # 30% of docs have frontmatter.
        has_fm = rng.random() < 0.30
        fm = None
        if has_fm:
            ts = (base_date + dt.timedelta(days=i % 730, hours=i % 24)).isoformat()
            fm = {
                "created": ts,
                "tags": sorted({topic.folder.split("/")[0], topic.slug[:3]}),
            }
            if topic.aliases:
                fm["aliases"] = list(topic.aliases)

        # Wikilinks: sample from topic neighbours.
        neighbour_topics = [topics[j] for j in topic.neighbors]
        # Mix of has-doc, alias-only, and missing — keeps unresolved cases alive.
        rng.shuffle(neighbour_topics)
        n_links = rng.randint(2, 10)
        wikilink_targets = neighbour_topics[:n_links]
        wikilink_slugs: list[str] = []
        for nt in wikilink_targets:
            if nt.has_doc:
                wikilink_slugs.append(nt.slug)
            elif nt.aliases:
                wikilink_slugs.append(nt.aliases[0])
            else:
                # Unresolved
                wikilink_slugs.append(f"unresolved-{nt.slug}")

        # 10% of docs get an `![[embed]]` to one neighbour.
        embed_slugs: list[str] = []
        if rng.random() < 0.10 and neighbour_topics:
            target = neighbour_topics[-1]
            embed_slugs.append(target.slug if target.has_doc else f"unresolved-{target.slug}")

        plans.append(DocPlan(
            rel_path=rel_path,
            topic=topic,
            body_target=body_target,
            section_count=section_count,
            frontmatter=fm,
            wikilink_slugs=wikilink_slugs,
            embed_slugs=embed_slugs,
        ))

    return plans


# ──────────────────────────────────────────────────────────────────────────────
# Specialty docs — oversized, 10k-section, hub
# ──────────────────────────────────────────────────────────────────────────────

def emit_oversized_doc(markov: Markov) -> str:
    """~12 MB single doc with one giant body paragraph. Triggers --max-file-size."""
    # ~5 bytes per word + spaces, so 2.4M words ≈ 12 MB.
    target_words = 2_400_000
    return (
        "# Oversized Document\n\n"
        + markov.paragraph(target_words) + "\n"
    )


def emit_ten_thousand_section_doc(markov: Markov, max_bytes: int) -> str:
    """~10k sections in a single doc, mostly skeletal so total stays ≤ max_bytes."""
    parts: list[str] = ["# Ten Thousand Sections\n", ""]
    bytes_per_section_budget = max(40, (max_bytes - 1000) // 10_000)
    # Skeleton: alternating H2/H3 headings + a single short line of body.
    for i in range(10_000):
        depth = 2 if i % 5 != 0 else 3
        heading = "#" * depth + f" Section {i + 1}\n"
        # Minimal body — a handful of words.
        body_words = max(3, bytes_per_section_budget // 8)
        body = markov.paragraph(body_words)
        # Truncate body if needed to keep the per-section budget honest.
        line = heading + body[: max(0, bytes_per_section_budget - len(heading) - 1)] + "\n\n"
        parts.append(line)
    return "".join(parts)


def emit_hub_doc(slug: str, markov: Markov) -> str:
    return (
        "# Hub Document\n\n"
        + markov.paragraph(120) + "\n\n"
        + "## Index\n\n"
        + markov.paragraph(80) + "\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────────

def generate_vault(size: str, output: Path, seed: int, zip_out: bool) -> dict:
    size_cfg = SIZE_CONFIG[size]
    rng = random.Random(seed)

    corpus_text = CORPUS_PATH.read_text(encoding="utf-8")
    markov = Markov(corpus_text, rng)

    # Pinned base date for deterministic frontmatter timestamps.
    base_date = dt.datetime(2024, 1, 1, 0, 0, 0)

    # 1. Topic graph.
    topics = build_topic_graph(size_cfg, rng)
    topic_by_slug = {t.slug: t for t in topics}

    # 2. Plan procedural docs.
    # Budget = total target - estimated bytes for edge-case + specialty docs.
    edge_case_budget = 30_000  # ~2 KB * 15 edge-case docs, with slack
    specialty_budget = 0
    if size_cfg["oversized_file"]:
        specialty_budget += 12_000_000
    if size_cfg["ten_thousand_section_doc"]:
        specialty_budget += min(size_cfg["max_file_bytes"], 1_000_000)
    procedural_budget = max(0, size_cfg["total_bytes"] - edge_case_budget - specialty_budget)

    procedural_plans = plan_procedural_docs(
        topics, size_cfg, procedural_budget, rng, base_date,
    )

    # 3. Identify hub doc (large only).
    hub_slug: str | None = None
    if size_cfg["hub_500_backlinks"] and procedural_plans:
        hub_slug = procedural_plans[0].topic.slug

    # 4. Materialise files.
    output.mkdir(parents=True, exist_ok=True)
    vault_id_dir = output / ".ki"
    vault_id_dir.mkdir(exist_ok=True)
    vault_uuid = det_uuid4(rng)
    (vault_id_dir / "vault.yaml").write_text(
        yaml.safe_dump({"uri": str(vault_uuid)}, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    files_written: list[tuple[str, int]] = []  # (rel_path, byte_size)

    def write_md(rel_path: str, content: str) -> None:
        target = output / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        files_written.append((rel_path, len(data)))

    # 4a. Edge-case docs.
    for path in FIXED_EDGE_CASE_PATHS:
        content = emit_edge_case_doc(
            path,
            topics=topics,
            topic_by_slug=topic_by_slug,
            markov=markov,
            rng=rng,
            base_date=base_date,
            alias_target_slug="35th-president",
        )
        write_md(path, content)

    # 4b. Procedural docs. Inject hub-link into the first 500 procedural docs
    # whenever the size has a 500-backlink hub.
    for idx, plan in enumerate(procedural_plans):
        wikilink_slugs = list(plan.wikilink_slugs)
        if hub_slug and idx < 500 and hub_slug not in wikilink_slugs and plan.topic.slug != hub_slug:
            wikilink_slugs.insert(0, hub_slug)

        title_words = plan.topic.name.split()
        title = " ".join(w.capitalize() for w in title_words)
        # Empirical: markov output averages ~6.35 bytes per word over this
        # corpus (incl. whitespace, headings, frontmatter). Dividing by 6
        # gives slightly more words than the body byte target — the small
        # overshoot offsets the per-doc heading/preamble fixed cost.
        body_words = max(40, plan.body_target // 6)
        content = emit_doc(
            body_words=body_words,
            section_count=plan.section_count,
            max_heading_depth=size_cfg["max_heading_depth"],
            wikilink_slugs=wikilink_slugs,
            embed_slugs=plan.embed_slugs,
            markov=markov,
            rng=rng,
            title=title,
            frontmatter_meta=plan.frontmatter,
        )
        write_md(plan.rel_path, content)

    # 4c. Oversized doc.
    if size_cfg["oversized_file"]:
        write_md("special/oversized.md", emit_oversized_doc(markov))

    # 4d. 10k-section doc.
    if size_cfg["ten_thousand_section_doc"]:
        write_md(
            "special/ten-thousand-sections.md",
            emit_ten_thousand_section_doc(markov, size_cfg["max_file_bytes"]),
        )

    # 5. README.
    total_bytes = sum(b for _, b in files_written)
    generation_ts = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    readme = (
        f"# Test Vault ({size})\n\n"
        f"Generated by `scripts/gen_test_vault.py` from the `knowledge-index` repo.\n\n"
        f"- Size: `{size}`\n"
        f"- Seed: `{seed}`\n"
        f"- Topic count: {len(topics)}\n"
        f"- File count (excluding this README): {len(files_written)}\n"
        f"- Total content bytes: {total_bytes}\n"
        f"- Vault UUID: `{vault_uuid}`\n"
        f"- Generated at: {generation_ts}\n\n"
        f"This vault exercises every node property and edge type in "
        f"`docs/data-model/schema.md`. See `prompts/build-test-vault.md` for the "
        f"contract this fixture satisfies.\n"
    )
    (output / "README.md").write_text(readme, encoding="utf-8")
    files_written.append(("README.md", len(readme.encode("utf-8"))))

    summary = {
        "size": size,
        "seed": seed,
        "vault_uuid": str(vault_uuid),
        "file_count": len(files_written),
        "total_bytes": sum(b for _, b in files_written),
        "topic_count": len(topics),
        "output": str(output),
    }

    # 6. Zip (optional).
    if zip_out:
        zip_path = Path(str(output).rstrip("/") + ".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, _dirs, files in os.walk(output):
                # Sort for deterministic archive order.
                for name in sorted(files):
                    abs_p = Path(root) / name
                    rel_p = abs_p.relative_to(output)
                    zf.write(abs_p, arcname=str(rel_p))
        summary["zip_path"] = str(zip_path)

    return summary


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically generate an Obsidian-style markdown vault "
                    "for ki testing."
    )
    parser.add_argument("--size", required=True, choices=sorted(SIZE_CONFIG.keys()))
    parser.add_argument("--output", required=True, type=Path,
                        help="Output directory (created if missing).")
    parser.add_argument("--seed", required=True, type=int,
                        help="Seeds RNG, Faker, UUID generation, frontmatter dates.")
    parser.add_argument("--zip", dest="zip_out", action="store_true",
                        help="Also write `<output>.zip` (ZIP_DEFLATED, level 6).")
    args = parser.parse_args(argv)

    summary = generate_vault(
        size=args.size,
        output=args.output,
        seed=args.seed,
        zip_out=args.zip_out,
    )

    # Single-line summary for humans / scripts.
    print(
        f"[gen_test_vault] size={summary['size']} files={summary['file_count']} "
        f"bytes={summary['total_bytes']} topics={summary['topic_count']} "
        f"output={summary['output']}"
        + (f" zip={summary['zip_path']}" if "zip_path" in summary else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
