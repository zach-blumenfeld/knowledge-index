"""Display-text → target-alias normalization.

When the markdown parser sees `[[Target|Display]]` (a *piped* wikilink), the
display text is the alternate name the user gave the target in running prose.
At ingest time we collect these display texts per target URI and union them
into the target's `aliases` field so the existing `content_search`
fulltext index starts matching them — `ki search "Anakin"` then finds the
`Darth Vader.md` document.

The rules below keep junk out of the alias list. Order matters: each rule
runs in sequence per target, against the *batch* of newly-seen display texts
for that target.

Frontmatter aliases are the user's hand-authored ground truth; this module
only produces derived display-text aliases and never overwrites or reorders
the existing list. The caller (ingest pipeline) is responsible for the union.

Spec: `docs/v0_3_0_semantic_search/requirements.md` *Normalization rules*.
"""

from __future__ import annotations

from collections import Counter

# Tiny initial stopword list — case-insensitive match against the trimmed
# display text. A future PR can tune this from observed failures; keep it
# small so we don't drop legitimate vault terms.
STOPWORDS: frozenset[str] = frozenset(
    {
        "him",
        "her",
        "it",
        "this",
        "that",
        "these",
        "those",
        "here",
        "there",
        "see",
        "link",
        "note",
        "ref",
        "the",
    }
)

MIN_LENGTH = 3
PER_TARGET_CAP = 50


def normalize_display_texts(
    display_texts: list[str],
    *,
    target_display_name: str | None,
    existing_aliases: list[str] | None,
) -> list[str]:
    """Normalize a batch of wikilink display texts for one target.

    Returns the list of new aliases to *add* to the target's `aliases` field.
    The caller unions the result with the existing list; this function does
    not return the union itself.

    Args:
        display_texts: All display texts seen for this target across the
            vault, in any order. Duplicates are expected and drive the
            "sort by occurrence count desc" stability rule.
        target_display_name: The target's `displayName` (e.g. the document's
            filename or the section's heading text). Used to drop entries
            that add no information.
        existing_aliases: Already-stored aliases on the target — typically
            frontmatter aliases. Used to drop entries already present (case-
            insensitive). Preserves the existing casing; this function only
            produces *new* additions.
    """
    # 1. Trim each entry and drop empties.
    candidates: list[str] = []
    for raw in display_texts:
        if raw is None:
            continue
        s = raw.strip()
        if not s:
            continue
        candidates.append(s)

    # 2. Length threshold (post-trim).
    candidates = [s for s in candidates if len(s) >= MIN_LENGTH]

    # 3. Stopword filter (case-insensitive).
    candidates = [s for s in candidates if s.lower() not in STOPWORDS]

    # 4. Drop entries equal to the target's displayName (case-insensitive).
    if target_display_name:
        dn_lower = target_display_name.strip().lower()
        if dn_lower:
            candidates = [s for s in candidates if s.lower() != dn_lower]

    # 5. Drop entries already in the target's existing aliases (case-
    #    insensitive). We compare lowercased; we preserve the *existing*
    #    casing of whatever's already stored — the caller does the union.
    existing_lower = {a.strip().lower() for a in (existing_aliases or []) if a}
    candidates = [s for s in candidates if s.lower() not in existing_lower]

    # 6. Lowercase-dedup within the new batch. Preserve the *first-seen*
    #    original casing; lowercase only for the comparison. Track per-
    #    lowercase counts so the per-target cap (rule 7) can sort by
    #    frequency.
    first_seen_casing: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for s in candidates:
        key = s.lower()
        counts[key] += 1
        if key not in first_seen_casing:
            first_seen_casing[key] = s

    # 7. Per-target cap. Sort by occurrence count desc, then alphabetical
    #    on the lowercase key for stable cross-run ordering, then truncate.
    sorted_keys = sorted(
        first_seen_casing.keys(),
        key=lambda k: (-counts[k], k),
    )
    sorted_keys = sorted_keys[:PER_TARGET_CAP]

    return [first_seen_casing[k] for k in sorted_keys]
