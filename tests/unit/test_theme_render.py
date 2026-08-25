"""Renderer tests for `ki theme` — pure, no Neo4j.

Drives `render()` off synthetic wire records, so every guarantee in
docs/commands/theme-format.md is pinned without needing a GDS instance.
The pipeline that *produces* those records is covered in
tests/integration/test_theme.py.
"""

from __future__ import annotations

import pytest

from ki.commands.theme import render
from ki.themes.producer import ThemeResult, cohesion


def member(key, uri, name, count, coh="moderate"):
    return {
        "cluster_key": key, "kind": "member", "uri": uri, "displayName": name,
        "count": count, "other_cluster_key": None, "cohesion": coh,
    }


def target(key, uri, name, count):
    return {
        "cluster_key": key, "kind": "link_target", "uri": uri, "displayName": name,
        "count": count, "other_cluster_key": None, "cohesion": None,
    }


def crossover(key, other, uri, name):
    return {
        "cluster_key": key, "kind": "crossover", "uri": uri, "displayName": name,
        "count": None, "other_cluster_key": other, "cohesion": None,
    }


def result(rows, *, total=10, grouped=8, excluded=0, name="my-vault"):
    return ThemeResult(
        method="links", vault_name=name, total_docs=total,
        grouped_docs=grouped, excluded_docs=excluded, rows=rows,
    )


@pytest.fixture
def two_themes():
    rows = []
    # T1: 5 docs, T2: 3 docs — deliberately inserted small-first so the
    # renderer has to sort rather than preserve input order.
    for i in range(3):
        rows.append(member("b", f"v/b{i}.md", f"b{i}.md", 3 - i, "loose"))
    for i in range(5):
        rows.append(member("a", f"v/a{i}.md", f"a{i}.md", 5 - i, "tight"))
    rows.append(target("a", "v/ga.md", "GraphRAG.md", 4))
    rows.append(target("a", "v/lu.md", "Lucene.md", 2))
    rows.append(crossover("a", "b", "v/a0.md", "a0.md"))
    return result(rows, total=10, grouped=8)


# ---- header ---------------------------------------------------------------


def test_header_reconciles(two_themes):
    head = render(two_themes).splitlines()[0]
    assert head == (
        "THEMES  my-vault   10 docs · 8 grouped into 2 themes by wikilinks · "
        "2 ungrouped"
    )


def test_ungrouped_prints_even_at_zero():
    rows = [member("a", f"v/a{i}.md", f"a{i}.md", 1) for i in range(3)]
    head = render(result(rows, total=3, grouped=3)).splitlines()[0]
    assert "0 ungrouped" in head


def test_excluded_segment_only_when_nonzero(two_themes):
    assert "excluded" not in render(two_themes).splitlines()[0]
    two_themes.excluded_docs = 2
    assert "· 2 excluded" in render(two_themes).splitlines()[0]


def test_method_phrase_is_always_wikilinks(two_themes):
    """There is no folder fallback — the phrase must never vary (issue #72)."""
    assert "by wikilinks" in render(two_themes).splitlines()[0]


# ---- ordering + ids -------------------------------------------------------


def test_themes_numbered_by_size_descending(two_themes):
    out = render(two_themes)
    assert "T1  5 docs (50%)" in out
    assert "T2  3 docs (30%)" in out
    assert out.index("T1  5 docs") < out.index("T2  3 docs")


def test_equal_sized_themes_break_ties_on_smallest_uri():
    rows = [member("z", "v/m.md", "m.md", 1), member("z", "v/n.md", "n.md", 1)]
    rows += [member("y", "v/a.md", "a.md", 1), member("y", "v/b.md", "b.md", 1)]
    out = render(result(rows, total=4, grouped=4))
    # cluster "y" holds v/a.md, the smallest URI, so it must render first.
    assert out.index("a.md") < out.index("m.md")


def test_members_render_in_producer_order(two_themes):
    """Producer sorts by within-theme link count; renderer must not resort."""
    body = render(two_themes)
    assert body.index("a0.md") < body.index("a1.md") < body.index("a2.md")


# ---- membership reconciliation -------------------------------------------


def test_remainder_line_reconciles_with_theme_count(two_themes):
    out = render(two_themes, per_theme=2)
    # T1 has 5 docs, 2 shown → 3 remaining.
    assert "(+3 more docs)" in out
    # T2 has 3 docs, 2 shown → 1 remaining.
    assert "(+1 more docs)" in out


def test_no_remainder_line_when_all_members_shown(two_themes):
    assert "more docs)" not in render(two_themes, per_theme=99)


# ---- link targets ---------------------------------------------------------


def test_targets_strip_md_and_carry_unit_on_first_only(two_themes):
    line = [x for x in render(two_themes).splitlines() if "top wikilink" in x][0]
    # `.md` stripped: the user wrote [[GraphRAG]], not [[GraphRAG.md]].
    assert "[[GraphRAG]] in 4 docs · [[Lucene]] in 2" in line
    assert "[[GraphRAG.md]]" not in line


def test_target_line_is_present_and_self_explanatory_when_empty(two_themes):
    line = [x for x in render(two_themes).splitlines() if "top wikilink" in x][1]
    assert "(none shared by 2+ docs)" in line


# ---- crossovers -----------------------------------------------------------


def test_crossover_names_the_target_theme(two_themes):
    assert "links into T2 via" in render(two_themes)


def test_crossovers_into_hidden_themes_are_suppressed(two_themes):
    """A top-k cut must never leave a dangling T<j> reference."""
    out = render(two_themes, top_k=1)
    assert "T2" not in out.split("\n", 1)[1]  # body only; header may mention counts


# ---- top-k ----------------------------------------------------------------


def test_top_k_reports_hidden_themes_in_header(two_themes):
    head = render(two_themes, top_k=1).splitlines()[0]
    assert "(showing top 1 — 1 smaller themes cover 3 more docs)" in head
    # Hidden themes stay *grouped* — the cut is display-only.
    assert "8 grouped into 2 themes" in head


# ---- row shape ------------------------------------------------------------


def test_doc_rows_carry_full_uri_and_type_letter(two_themes):
    row = [x for x in render(two_themes).splitlines() if "a0.md" in x][0]
    assert row.rstrip().endswith("v/a0.md")
    assert " D   " in row


def test_long_names_truncate_but_uris_never_do():
    long_name = "x" * 80
    rows = [member("a", "v/" + long_name + ".md", long_name + ".md", 1) for _ in range(1)]
    rows += [member("a", "v/b.md", "b.md", 1), member("a", "v/c.md", "c.md", 1)]
    out = render(result(rows, total=3, grouped=3))
    row = [x for x in out.splitlines() if long_name[:20] in x][0]
    assert "…" in row
    assert row.rstrip().endswith("v/" + long_name + ".md")


# ---- cohesion -------------------------------------------------------------


def test_cohesion_words(two_themes):
    out = render(two_themes)
    assert "· tightly interlinked" in out
    assert "· loosely interlinked" in out


@pytest.mark.parametrize(
    "conductance,word",
    [(0.0, "tight"), (0.35, "tight"), (0.36, "moderate"),
     (0.59, "moderate"), (0.60, "loose"), (1.0, "loose")],
)
def test_cohesion_thresholds(conductance, word):
    assert cohesion(conductance) == word


# ---- degenerate cases -----------------------------------------------------


def test_zero_themes_still_prints_header_and_explains():
    out = render(result([], total=7, grouped=0))
    assert out.splitlines()[0].startswith(
        "THEMES  my-vault   7 docs · 0 grouped into 0 themes by wikilinks · 7 ungrouped"
    )
    assert "no themes" in out


def test_zero_docs_does_not_divide_by_zero():
    rows = [member("a", "v/a.md", "a.md", 1)]
    out = render(result(rows, total=0, grouped=0))
    assert "(0%)" in out
