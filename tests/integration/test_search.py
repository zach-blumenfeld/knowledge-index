"""Integration tests for `ki search`."""

from __future__ import annotations

import time

import pytest

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.search.queries import run_b1, run_b2

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
                    "CALL db.index.fulltext.queryNodes('doc_section_search', 'retrieval')"
                    " YIELD node WHERE node.uri STARTS WITH $u RETURN count(node) AS n",
                    u=vault_uri,
                ))
                if rows and rows[0]["n"] > 0:
                    return
                time.sleep(0.2)


def test_search_b1_finds_document_by_title(indexed_vault, neo4j_profile):
    """The generated fixture pins a 'Big Idea' document (H1 + alias BI)."""
    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            results = run_b1(session, "Big Idea", k=5)
    titles = [r["title"] for r in results]
    assert any("big idea" in (t or "").lower() for t in titles), titles


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
