"""Integration tests for `ki search`."""

from __future__ import annotations

import time
import uuid

import pytest
import yaml

from ki.ingest.pipeline import IngestOptions, ingest_vault
from ki.neo4j_client import driver_for
from ki.search.queries import (
    run_b1,
    run_b2,
    run_b3,
    run_b12,
    run_b12_links,
    run_vault_search,
)

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
    """`ki search --types vault` emits a stderr warning per Vault row with null/empty description.

    The merged-search helper now keys off the `label` field (rows in the
    unified result list each carry one), and only warns for `Vault` rows.
    """
    from ki.commands.search import _warn_missing_vault_description

    vault = tmp_path / "blank-vault"
    vault.mkdir()
    (vault / "n.md").write_text("# N\n\nbody.\n")
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    _await_index_population(neo4j_profile, vault_uri=res.vault_uri)

    # Vault has no description — feed the renderer the row directly and assert
    # the stderr warning fires.
    rows = [{
        "label": "Vault",
        "vault_uri": res.vault_uri,
        "name": vault.name,
        "description": None,
    }]
    _warn_missing_vault_description(rows)
    captured = capsys.readouterr()
    assert "no description set" in captured.err


def test_warn_missing_vault_description_skips_non_vault_rows():
    """Mixed-type search results: only Vault rows are eligible to warn."""
    from ki.commands.search import _warn_missing_vault_description

    rows = [
        {"label": "Document", "document_uri": "vault://v/foo.md", "description": None},
        {"label": "Section", "section_uri": "vault://v/foo.md#bar", "description": None},
    ]
    # Should not raise and should not warn — these rows are non-Vault.
    _warn_missing_vault_description(rows)
    # Test passes if no AssertionError; this is the regression guard.


def test_search_b12_accepts_parameterized_depth(tmp_path, neo4j_profile, cleanup_vault):
    """B.12 must accept an arbitrary `depth` at runtime.

    Regression guard: like B.3, B.12 uses a quantified-path quantifier
    (`{1,$depth}`) and Neo4j 5.x rejects parameters inside it, so `run_b12`
    substitutes the literal int client-side. If anyone removes that
    substitution, the query fails with `SyntaxError: Invalid input '$': ...`.
    """
    vault = tmp_path / "tree-vault"
    vault.mkdir()
    (vault / "ideas").mkdir()
    (vault / "ideas" / "big.md").write_text("# Big\n\n## Sub-A\n\nbody.\n## Sub-B\n\nbody.\n")

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        # depth=1: should reach only the Folder under the Vault.
        shallow = run_b12(session, res.vault_uri, depth=1)
        # depth=4: should reach into sub-sections.
        deep = run_b12(session, res.vault_uri, depth=4)

    shallow_labels = {r["label"] for r in shallow}
    deep_labels = {r["label"] for r in deep}
    assert "Folder" in shallow_labels
    assert "Section" not in shallow_labels  # too far at depth=1
    assert "Section" in deep_labels  # reached at depth=4


def test_search_b12_sort_pos_set_for_sections_in_reading_order(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """B.12 must emit `sort_pos` on every Section row matching NEXT_SECTION order.

    The DFS reading order for `# Big → ## Background → ## Origins → ## Impl`
    yields chain positions 0, 1, 2, 3. The query must surface those even
    though they are not the alphabetical order of section names.
    """
    vault = tmp_path / "sort-pos-vault"
    vault.mkdir()
    (vault / "big.md").write_text(
        "# Big\n\nintro.\n\n## Background\n\nb.\n\n## Origins\n\no.\n\n## Implementation\n\ni.\n"
    )

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = run_b12(session, res.vault_uri, depth=5)

    sections = [r for r in rows if r["label"] == "Section"]
    # Key by displayName (`r.name` is the heading-path slug like
    # `big/background`; displayName is the heading text "Background").
    by_display = {r["displayName"]: r["sort_pos"] for r in sections}
    # Reading order is Big → Background → Origins → Implementation.
    # Alphabetical would be Background → Big → Implementation → Origins.
    # sort_pos must encode the former, not the latter.
    assert by_display["Big"] == 0
    assert by_display["Background"] == 1
    assert by_display["Origins"] == 2
    assert by_display["Implementation"] == 3


def test_search_b12_null_root_uri_returns_all_vaults(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """When --at is omitted, B.12 matches every :Vault as a root."""
    vault_a = tmp_path / "vault-a"
    vault_a.mkdir()
    (vault_a / "x.md").write_text("# X\n\nbody.\n")
    vault_b = tmp_path / "vault-b"
    vault_b.mkdir()
    (vault_b / "y.md").write_text("# Y\n\nbody.\n")

    a = ingest_vault(vault_a, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(a.vault_uri)
    b = ingest_vault(vault_b, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(b.vault_uri)

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        rows = run_b12(session, None, depth=2)

    root_uris = {r["uri"] for r in rows if r["depth"] == 0 and r["label"] == "Vault"}
    assert a.vault_uri in root_uris
    assert b.vault_uri in root_uris


def test_search_b12_links_returns_outbound_links_to_edges(
    tmp_path, neo4j_profile, cleanup_vault,
):
    """B.12-links must return outbound :LINKS_TO edges from supplied source URIs."""
    vault = tmp_path / "links-vault"
    vault.mkdir()
    (vault / "a.md").write_text("Refers to [[b]].\n\n# A\n\nbody.\n")
    (vault / "b.md").write_text("# B\n\nbody.\n")

    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)

    a_uri = f"{res.vault_uri}/a.md"

    with driver_for(neo4j_profile) as driver, driver.session() as session:
        links = run_b12_links(session, [a_uri])

    assert any(
        link["parent_uri"] == a_uri and link["uri"] == f"{res.vault_uri}/b.md"
        for link in links
    ), links


def test_search_b12_links_empty_source_list_returns_empty(neo4j_profile):
    """run_b12_links short-circuits on an empty source list without hitting Neo4j."""
    # We don't actually open a session — the function should return [] immediately.
    assert run_b12_links(None, []) == []


# ---- cmd_search end-to-end (merged --types behavior) ----------------------


def _write_test_config(tmp_path, neo4j_profile, monkeypatch):
    """Materialize the active neo4j_profile under $XDG_CONFIG_HOME/ki/config.yaml."""
    xdg = tmp_path / "xdg"
    (xdg / "ki").mkdir(parents=True)
    (xdg / "ki" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "default_profile": neo4j_profile.name,
                "profiles": {
                    neo4j_profile.name: {
                        "uri": neo4j_profile.uri,
                        "user": neo4j_profile.user,
                        "password": neo4j_profile.password,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))


@pytest.fixture
def search_corpus(tmp_path, neo4j_profile, cleanup_vault):
    """Index a small vault with a Vault description and indexed content.

    The corpus is shaped so a single fulltext query ('retrieval') matches at
    least one Document, Section, and the Vault (via description).
    """
    vault = tmp_path / "merged-vault"
    vault.mkdir()
    (vault / ".ki").mkdir()
    seeded_uri = str(uuid.uuid4())
    (vault / ".ki" / "vault.yaml").write_text(
        yaml.safe_dump(
            {
                "uri": seeded_uri,
                "description": (
                    "Research notes on retrieval, semantic search, and "
                    "vector-free indexing strategies."
                ),
            },
            sort_keys=False,
        )
    )
    (vault / "retrieval.md").write_text(
        "# Retrieval Notes\n\n"
        "Discusses retrieval strategies in depth.\n\n"
        "## Background on retrieval\n\n"
        "background body about retrieval.\n"
    )
    res = ingest_vault(vault, IngestOptions(profile=neo4j_profile, batch_size=64))
    cleanup_vault.append(res.vault_uri)
    _await_index_population(neo4j_profile, vault_uri=res.vault_uri)
    _await_vault_index(neo4j_profile, vault_uri=res.vault_uri, term="retrieval")
    return res.vault_uri


def test_cmd_search_default_returns_mixed_types(
    search_corpus, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """Default invocation runs B.1 + B.2 + B.11 and merges into one JSON list."""
    import json

    from ki.commands.search import cmd_search

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_search(
        "retrieval", profile=None,
        types_csv="document,section,vault", k=20, as_json=True,
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    labels = {r.get("label") for r in rows}
    # The corpus is constructed so a single 'retrieval' query hits all three.
    assert "Document" in labels
    assert "Section" in labels
    assert "Vault" in labels


def test_cmd_search_types_filter_excludes_other_types(
    search_corpus, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """`--types section,document` must not return Vault rows."""
    import json

    from ki.commands.search import cmd_search

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_search(
        "retrieval", profile=None,
        types_csv="section,document", k=20, as_json=True,
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    labels = {r.get("label") for r in rows}
    assert "Vault" not in labels
    # And at least one of the requested types is present.
    assert labels & {"Document", "Section"}


def test_cmd_search_k_caps_total_results(
    search_corpus, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """`--k N` is the TOTAL cap across all types, not per-type."""
    import json

    from ki.commands.search import cmd_search

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_search(
        "retrieval", profile=None,
        types_csv="document,section,vault", k=2, as_json=True,
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) <= 2


def test_cmd_search_rows_are_score_sorted(
    search_corpus, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """Merged rows must be sorted by `score` descending."""
    import json

    from ki.commands.search import cmd_search

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_search(
        "retrieval", profile=None,
        types_csv="document,section,vault", k=10, as_json=True,
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    scores = [r.get("score") or 0.0 for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_cmd_search_plain_text_includes_key_header(
    search_corpus, neo4j_profile, tmp_path, monkeypatch, capsys,
):
    """Plain-text output renders the `Key:` header line, same as `ki tree`."""
    from ki.commands.search import cmd_search

    _write_test_config(tmp_path, neo4j_profile, monkeypatch)
    rc = cmd_search(
        "retrieval", profile=None,
        types_csv="document,section,vault", k=10, as_json=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Key:" in out
    assert "V Vault" in out
    assert "D Document" in out
    assert "S Section" in out
