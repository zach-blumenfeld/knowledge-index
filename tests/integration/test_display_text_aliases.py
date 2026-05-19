"""Wikilink display-text → target aliases — end-to-end.

The headline case: a vault where every reference to "Anakin" is written as
`[[Darth Vader|Anakin]]`. Before this release `ki search "Anakin"` returned
nothing — the literal string only appears in source-document body text and
the target (`Darth Vader.md`) had no idea it was also called "Anakin". After
this release, the display text propagates into `Darth Vader.aliases` at
ingest, and the existing `content_search` fulltext index picks it up.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.search.queries import run_b1, run_b2

pytestmark = pytest.mark.integration


def _await_alias_index(profile, *, vault_uri: str, term: str, timeout: float = 10.0) -> None:
    """Fulltext indexes are eventually consistent — give the writer a moment."""
    deadline = time.time() + timeout
    with driver_for(profile) as driver:
        with driver.session() as session:
            while time.time() < deadline:
                rows = list(
                    session.run(
                        "CALL db.index.fulltext.queryNodes('content_search', $q) "
                        "YIELD node WHERE node.uri STARTS WITH $u RETURN count(node) AS n",
                        q=term,
                        u=vault_uri,
                    )
                )
                if rows and rows[0]["n"] > 0:
                    return
                time.sleep(0.2)


@pytest.fixture
def anakin_vault(tmp_path: Path) -> Path:
    """A tiny self-contained vault with `[[Darth Vader|Anakin]]` references.

    We don't extend the deterministic `gen_test_vault.py` here — the test is
    specifically about piped-wikilink behavior, and a 2-file fixture is easier
    to reason about than a regenerated 20-file fixture diff.
    """
    vault = tmp_path / "anakin-vault"
    vault.mkdir()

    # Note: `Darth Vader.md` has no H1 wrapper on purpose. With an H1 like
    # `# Darth Vader` the section URI becomes `<doc>#darth-vader/origins`
    # (heading path includes the H1 ancestor), but Obsidian-style wikilinks
    # `[[Doc#Heading]]` only encode the bare heading text — the resolver
    # computes `<doc>#origins` and the two never meet. That's a pre-existing
    # gap in the wikilink resolver (not v0.3.0 behavior). Keep the H1 off so
    # the section URI matches what the resolver computes.
    (vault / "Darth Vader.md").write_text(
        "A Sith Lord of the Galactic Empire.\n\n"
        "## Origins\n\n"
        "Born on Tatooine; later fell to the dark side.\n",
        encoding="utf-8",
    )

    (vault / "Star Wars.md").write_text(
        "# Star Wars\n\n"
        "The redemption arc of [[Darth Vader|Anakin]] is the through-line of "
        "the original saga. As a boy, [[Darth Vader|Anakin]] competed in "
        "podraces on Tatooine; his fall began later.\n\n"
        "## Trivia\n\n"
        "See also [[Darth Vader#Origins|Anakin's origin story]] for details.\n",
        encoding="utf-8",
    )

    return vault


def test_anakin_resolves_to_darth_vader_after_display_text_aliasing(
    anakin_vault: Path, neo4j_profile, cleanup_vault
):
    res = ingest_vault(
        anakin_vault,
        IngestOptions(profile=neo4j_profile, batch_size=64),
    )
    cleanup_vault.append(res.vault_uri)
    _await_alias_index(neo4j_profile, vault_uri=res.vault_uri, term="Anakin")

    # B.1 document-level: "Anakin" should now find Darth Vader.md.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            doc_hits = run_b1(session, "Anakin", k=5)

    titles = [(r.get("title") or "").lower() for r in doc_hits]
    assert any("darth vader" in t for t in titles), (
        f"expected Darth Vader doc to match 'Anakin' via display-text aliases; got {titles}"
    )

    # And the alias should actually be persisted on the node, not just an
    # index-time artifact.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (d:Document {uri: $u + '/darth-vader.md'}) "
                "RETURN d.aliases AS a",
                u=res.vault_uri,
            ).single()
    assert row is not None, "expected the Darth Vader document to exist"
    aliases = list(row["a"] or [])
    assert any(a.lower() == "anakin" for a in aliases), (
        f"expected 'Anakin' in Darth Vader.aliases; got {aliases}"
    )


def test_section_target_display_text_aliases_the_section(
    anakin_vault: Path, neo4j_profile, cleanup_vault
):
    """`[[Darth Vader#Origins|Anakin's origin story]]` aliases the Section."""
    res = ingest_vault(
        anakin_vault,
        IngestOptions(profile=neo4j_profile, batch_size=64),
    )
    cleanup_vault.append(res.vault_uri)
    _await_alias_index(neo4j_profile, vault_uri=res.vault_uri, term="origin")

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            row = session.run(
                "MATCH (s:Section {uri: $u + '/darth-vader.md#origins'}) "
                "RETURN s.aliases AS a",
                u=res.vault_uri,
            ).single()

    assert row is not None, "expected the Origins section to exist"
    aliases = list(row["a"] or [])
    assert any("origin story" in a.lower() for a in aliases), (
        f"expected the piped section-link display text in Section.aliases; got {aliases}"
    )

    # And the section should be findable by section-level fulltext.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            sec_hits = run_b2(session, '"origin story"', k=5)
    headings = [(r.get("heading") or "").lower() for r in sec_hits]
    assert any("origins" in h for h in headings), (
        f"expected the 'Origins' section to match 'origin story'; got {headings}"
    )
