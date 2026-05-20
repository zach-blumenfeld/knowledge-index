"""Integration tests for `ki search`."""

from __future__ import annotations

import time
import uuid

import pytest
import yaml

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.search.queries import run_b1, run_b2, run_b3, run_vault_search

pytestmark = pytest.mark.integration


@pytest.fixture
def indexed_vault(vault_dir, neo4j_profile, cleanup_vault):
    """Index the sample vault once, return the vault uri.

    Neo4j fulltext indexes are eventually-consistent; we give the index a
    short grace period so the search tests don't race the writer.
    """
    res = ingest_vault(vault_dir, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    # Wait for the fulltext index to catch up.
    _await_index_population(neo4j_profile, vault_uri=res.vault_uri)
    return res.vault_uri


def _await_index_population(profile, vault_uri: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    with driver_for(profile) as driver:
        with driver.session() as session:
            while time.time() < deadline:
                rows = list(session.run(
                    "CALL db.index.fulltext.queryNodes('content_search', 'retrieval')"
                    " YIELD node WHERE node.uri STARTS WITH $u RETURN count(node) AS n",
                    u=vault_uri,
                ))
                if rows and rows[0]["n"] > 0:
                    return
                time.sleep(0.2)


def _await_vault_index(profile, *, vault_uri: str, term: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    with driver_for(profile) as driver:
        with driver.session() as session:
            while time.time() < deadline:
                rows = list(session.run(
                    "CALL db.index.fulltext.queryNodes('content_search', $q) "
                    "YIELD node WHERE node:Vault AND node.uri = $u "
                    "RETURN count(node) AS n",
                    q=term,
                    u=vault_uri,
                ))
                if rows and rows[0]["n"] > 0:
                    return
                time.sleep(0.2)


def test_search_b1_finds_document_by_title(indexed_vault, neo4j_profile):
    """The generated fixture pins a 'Big Idea' document (filename + H1 + alias BI).

    Post-#28 the document's `displayName` is its filename (`big-idea.md`), not
    the H1 text — so we assert via `document_uri` (which carries the slug) and
    accept either "big idea" or "big-idea" in the surfaced title.
    """
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            results = run_b1(session, "Big Idea", k=5)
    uris = [r["document_uri"] for r in results]
    assert any("big-idea" in (u or "").lower() for u in uris), uris


def test_search_b2_finds_section_content(indexed_vault, neo4j_profile):
    """The Markov corpus produces 'retrieval' in many bodies; B.2 should hit some."""
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            results = run_b2(session, "retrieval", k=10)
    assert results, "expected at least one section hit for 'retrieval'"
    # Each row should carry both section + document granularity (B.2 contract).
    for r in results:
        assert r.get("section_uri")
        assert r.get("document_uri")


def test_search_b2_returns_owning_document(indexed_vault, neo4j_profile):
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            results = run_b2(session, "retrieval", k=5)
    assert results, "expected at least one hit"
    for r in results:
        assert r.get("section_uri")
        assert r.get("document_uri")


def test_search_b3_neighbourhood_accepts_parameterized_depth(tmp_path, neo4j_profile, cleanup_vault):
    """B.3 must accept an arbitrary `n` at runtime.

    Regression guard: Neo4j 5.x (incl. Aura) rejects Cypher parameters inside
    a quantified-path-pattern quantifier (`{1,$n}`), so `run_b3` substitutes
    the literal int client-side. If anyone removes that substitution, the
    query fails with `SyntaxError: Invalid input '$': expected '}'...`.

    The wikilinks are placed in **preambles** (before any H1) so multi-hop
    traversal works — B.3 walks pure `LINKS_TO` edges, and the parser only
    emits Doc→Doc `LINKS_TO` for preamble links. Section-internal wikilinks
    produce Section→Doc edges that can't extend the chain beyond one hop
    (the target Document has no outgoing `LINKS_TO`). That's a separate
    B.3 limitation, not what this test guards.
    """
    fresh = tmp_path / "b3-vault"
    fresh.mkdir()
    (fresh / "a.md").write_text("Refers to [[b]].\n\n# A\n\nbody.\n")
    (fresh / "b.md").write_text("Refers to [[c]].\n\n# B\n\nbody.\n")
    (fresh / "c.md").write_text("# C\n\nleaf.\n")

    res = ingest_vault(fresh, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    a_uri = f"{res.vault_uri}/a.md"

    # n=1: only direct neighbours of A reachable in one LINKS_TO hop → just B.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            hits = run_b3(session, a_uri, n=1)
    uris = {h["document_uri"] for h in hits}
    assert f"{res.vault_uri}/b.md" in uris
    assert f"{res.vault_uri}/c.md" not in uris  # too far at n=1

    # n=2: A's neighbours up to 2 hops → both B and C.
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            hits = run_b3(session, a_uri, n=2)
    uris = {h["document_uri"] for h in hits}
    assert f"{res.vault_uri}/b.md" in uris
    assert f"{res.vault_uri}/c.md" in uris


def test_vault_search_finds_by_description(tmp_path, neo4j_profile, cleanup_vault):
    """A vault with a description matches `--type vault` queries over that text."""
    vault = tmp_path / "graph-research"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody.\n")
    (vault / ".ki").mkdir()
    seeded_uri = str(uuid.uuid4())
    (vault / ".ki" / "vault.yaml").write_text(
        yaml.safe_dump(
            {
                "uri": seeded_uri,
                "description": (
                    "Research notes on graph databases, Neo4j internals, "
                    "and Cypher patterns."
                ),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    assert res.vault_uri == seeded_uri
    _await_vault_index(neo4j_profile, vault_uri=res.vault_uri, term="Cypher")

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            hits = run_vault_search(session, "Cypher", k=5)

    assert any(h["vault_uri"] == res.vault_uri for h in hits), (
        f"expected vault {res.vault_uri} to match 'Cypher' via its description; "
        f"got {hits}"
    )
    matched = next(h for h in hits if h["vault_uri"] == res.vault_uri)
    assert "Cypher" in (matched.get("description") or "")


def test_vault_search_warns_on_missing_description(tmp_path, neo4j_profile, cleanup_vault, capsys):
    """`--type vault` emits a stderr warning per vault with null/empty description."""
    from ki.commands.search import _warn_missing_vault_description

    vault = tmp_path / "blank-vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    _await_index_population(neo4j_profile, vault_uri=res.vault_uri)

    # Vault has no description — feed the renderer the row directly and assert
    # the stderr warning fires.
    rows = [{"vault_uri": res.vault_uri, "name": vault.name, "description": None}]
    _warn_missing_vault_description(rows)
    captured = capsys.readouterr()
    assert "no description set" in captured.err
