"""Display-text → alias normalization rules.

Rules (see src/ki/parser/aliases.py docstring + spec):
  1. trim
  2. length >= 3 after trim
  3. stopword filter (case-insensitive)
  4. drop if equals target displayName (case-insensitive)
  5. drop if already in existing aliases (case-insensitive)
  6. lowercase-dedup within batch (preserve first-seen casing)
  7. per-target cap of 50, sorted by count desc + alphabetical (stable)
"""

from __future__ import annotations

from ki.parser.aliases import PER_TARGET_CAP, normalize_display_texts

# --- Rule 1: trim ----------------------------------------------------------


def test_trim_strips_whitespace():
    out = normalize_display_texts(
        ["  Anakin  "], target_display_name="Darth Vader", existing_aliases=[]
    )
    assert out == ["Anakin"]


def test_trim_drops_whitespace_only():
    out = normalize_display_texts(
        ["   ", "\t\n", "Anakin"], target_display_name="Darth Vader", existing_aliases=[]
    )
    assert out == ["Anakin"]


# --- Rule 2: length threshold ----------------------------------------------


def test_length_threshold_drops_short_entries():
    out = normalize_display_texts(
        ["AB", "BB"], target_display_name="Darth Vader", existing_aliases=[]
    )
    assert out == []


def test_length_threshold_keeps_3_chars():
    out = normalize_display_texts(
        ["JFK"], target_display_name="John F Kennedy", existing_aliases=[]
    )
    assert out == ["JFK"]


# --- Rule 3: stopword filter -----------------------------------------------


def test_stopword_filter_drops_him():
    out = normalize_display_texts(
        ["him", "Anakin"], target_display_name="Darth Vader", existing_aliases=[]
    )
    assert out == ["Anakin"]


def test_stopword_filter_is_case_insensitive():
    out = normalize_display_texts(
        ["HERE", "Here", "Anakin"],
        target_display_name="Darth Vader",
        existing_aliases=[],
    )
    assert out == ["Anakin"]


def test_stopword_filter_keeps_non_stopwords():
    out = normalize_display_texts(
        ["herald"], target_display_name="Darth Vader", existing_aliases=[]
    )
    assert out == ["herald"]


# --- Rule 4: equal-to-displayName drop -------------------------------------


def test_drop_when_equals_display_name_case_insensitive():
    out = normalize_display_texts(
        ["darth vader", "Anakin"],
        target_display_name="Darth Vader",
        existing_aliases=[],
    )
    assert out == ["Anakin"]


def test_drop_equal_to_display_name_handles_whitespace():
    out = normalize_display_texts(
        ["  Darth Vader  "],
        target_display_name="Darth Vader",
        existing_aliases=[],
    )
    assert out == []


# --- Rule 5: drop-if-already-in-existing -----------------------------------


def test_drop_when_already_in_existing_aliases_case_insensitive():
    out = normalize_display_texts(
        ["anakin", "Vader"],
        target_display_name="Darth Vader",
        existing_aliases=["Anakin"],
    )
    assert out == ["Vader"]


def test_frontmatter_aliases_not_displaced():
    """Frontmatter aliases are the user's ground truth — they must not be
    reordered, lowered, or otherwise mutated by this function. We only
    return *new additions* and leave the union to the caller.
    """
    out = normalize_display_texts(
        ["Anakin", "Vader", "Skywalker"],
        target_display_name="Darth Vader",
        existing_aliases=["Anakin", "Sith Lord"],
    )
    # "Anakin" already there → dropped; "Vader" + "Skywalker" are new.
    assert sorted(out) == ["Skywalker", "Vader"]
    # The function never returns the original frontmatter entries —
    # those stay only in the caller's `existing_aliases`.
    assert "Anakin" not in out
    assert "Sith Lord" not in out


# --- Rule 6: lowercase-dedup, first-seen casing ----------------------------


def test_lowercase_dedup_preserves_first_seen_casing():
    out = normalize_display_texts(
        ["Anakin", "anakin", "ANAKIN"],
        target_display_name="Darth Vader",
        existing_aliases=[],
    )
    assert out == ["Anakin"]


def test_lowercase_dedup_preserves_each_distinct_alias():
    out = normalize_display_texts(
        ["Anakin", "Vader", "anakin"],
        target_display_name="Darth Vader",
        existing_aliases=[],
    )
    # Both distinct aliases survive; order is by count desc then alpha — both
    # have count 1 for Vader, 2 for anakin's group.
    assert "Anakin" in out
    assert "Vader" in out


# --- Rule 7: per-target cap, stable ordering --------------------------------


def test_per_target_cap_truncates_at_50():
    batch = [f"Alias{i:03d}" for i in range(80)]
    out = normalize_display_texts(
        batch, target_display_name="Target", existing_aliases=[]
    )
    assert len(out) == PER_TARGET_CAP == 50


def test_per_target_cap_is_stable_across_re_runs():
    """Same input → same output, byte-for-byte."""
    batch = [f"Alias{i:03d}" for i in range(80)]
    a = normalize_display_texts(
        batch, target_display_name="Target", existing_aliases=[]
    )
    b = normalize_display_texts(
        list(reversed(batch)), target_display_name="Target", existing_aliases=[]
    )
    # Same counts (1 each) → sorted alphabetically → identical output.
    assert a == b


def test_per_target_cap_prefers_high_count_aliases():
    """Aliases that occur more often should survive the cap."""
    # 49 single-occurrence aliases + one alias seen 5 times → at the cap,
    # the 5x alias must win and the 49 stay alongside, dropping the 50th
    # single occurrence.
    high_freq = ["Anakin"] * 5
    fillers = [f"Filler{i:03d}" for i in range(60)]
    out = normalize_display_texts(
        high_freq + fillers,
        target_display_name="Target",
        existing_aliases=[],
    )
    assert "Anakin" in out
    assert len(out) == PER_TARGET_CAP
