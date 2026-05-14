"""Unit tests for `scripts/gen_test_vault.py`.

These assertions enforce the contract documented in
`prompts/build-test-vault.md`:

- Identical `--seed` produces byte-identical output (modulo the wall-clock
  timestamp baked into `README.md`).
- Every edge case the spec calls out is actually present in the tiny fixture.
- File counts and total bytes are within ±10% of the size targets.
- `--zip` produces a valid archive that unpacks to byte-identical contents.

Slow sizes (`medium`, `large`) are not exercised here — those live behind the
`scripts/upload_test_vault.sh` workflow.
"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "gen_test_vault.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_vault"


def run_generator(size: str, output: Path, seed: int = 42, zip_out: bool = False) -> None:
    args = [
        sys.executable, str(GENERATOR),
        "--size", size, "--seed", str(seed),
        "--output", str(output),
    ]
    if zip_out:
        args.append("--zip")
    subprocess.run(args, check=True)


def files_excluding_readme(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name != "README.md"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Determinism
# ──────────────────────────────────────────────────────────────────────────────

def test_tiny_is_deterministic_across_runs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    run_generator("tiny", a)
    run_generator("tiny", b)
    rel_a = sorted(p.relative_to(a) for p in files_excluding_readme(a))
    rel_b = sorted(p.relative_to(b) for p in files_excluding_readme(b))
    assert rel_a == rel_b
    for rel in rel_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), (
            f"{rel} bytes differ between runs"
        )


def test_committed_fixture_matches_fresh_generation(tmp_path):
    """The committed `tests/fixtures/sample_vault/` must be reproducible by
    anyone running the generator with the same seed."""
    if not FIXTURE.exists():
        pytest.skip("Committed fixture not present yet — generate it first")
    fresh = tmp_path / "fresh"
    run_generator("tiny", fresh)
    rel_fixture = sorted(p.relative_to(FIXTURE) for p in files_excluding_readme(FIXTURE))
    rel_fresh = sorted(p.relative_to(fresh) for p in files_excluding_readme(fresh))
    assert rel_fixture == rel_fresh, "File set differs between fixture and fresh run"
    for rel in rel_fixture:
        assert (FIXTURE / rel).read_bytes() == (fresh / rel).read_bytes(), (
            f"{rel} differs between committed fixture and fresh generation"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Edge-case presence — every documented case must appear in the tiny fixture
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fixture_root() -> Path:
    if not FIXTURE.exists():
        pytest.skip("Committed fixture not present yet — generate it first")
    return FIXTURE


def test_nested_directories_at_least_three_levels(fixture_root):
    deep = [
        p for p in fixture_root.rglob("*.md")
        if len(p.relative_to(fixture_root).parts) >= 4
    ]
    assert deep, "Expected at least one doc nested 3+ folders deep"


def test_folder_name_needs_slugification(fixture_root):
    # The 'My Projects' folder (with space + capitals) must round-trip through
    # the URI slugifier as 'my-projects'.
    has_my_projects = any(
        "My Projects" in str(p.relative_to(fixture_root))
        for p in fixture_root.rglob("*.md")
    )
    assert has_my_projects, "Expected a folder name needing slugification"


def test_heading_level_skip(fixture_root):
    doc = (fixture_root / "concepts/heading-skip.md").read_text()
    has_h1 = re.search(r"^# [^#]", doc, re.MULTILINE)
    has_h3 = re.search(r"^### ", doc, re.MULTILINE)
    has_h2 = re.search(r"^## [^#]", doc, re.MULTILINE)
    assert has_h1, "Expected an H1"
    assert has_h3, "Expected an H3"
    assert not has_h2, "Expected NO H2 (this doc demonstrates the skip)"


def test_duplicate_headings_in_one_doc(fixture_root):
    doc = (fixture_root / "concepts/duplicate-headings.md").read_text()
    n_install = sum(
        1 for line in doc.splitlines() if line.strip() == "## Installation"
    )
    assert n_install >= 2, f"Expected ≥2 '## Installation' headings, got {n_install}"


def test_alias_target_has_jfk_alias(fixture_root):
    doc = (fixture_root / "references/35th-president.md").read_text()
    m = re.match(r"^---\n(.*?)\n---\n", doc, re.DOTALL)
    assert m, "Frontmatter missing on alias-only doc"
    fm = yaml.safe_load(m.group(1))
    aliases = fm.get("aliases") or []
    assert "JFK" in aliases, f"Expected 'JFK' alias, got {aliases}"


def test_wikilink_relies_on_alias_resolution(fixture_root):
    """At least one doc references `[[JFK]]`, which resolves only via the
    `35th-president.md` alias list (no doc literally named JFK exists)."""
    referrers = [p for p in fixture_root.rglob("*.md") if "[[JFK]]" in p.read_text()]
    assert referrers, "No doc wikilinks to alias [[JFK]]"
    # Defensive: confirm no doc is literally named JFK.md (case-insensitive).
    literal_jfks = [
        p for p in fixture_root.rglob("*.md")
        if p.stem.lower() == "jfk"
    ]
    assert not literal_jfks, "Found a literal JFK.md; alias resolution becomes ambiguous"


def test_unresolved_wikilink_exists(fixture_root):
    referrers = [
        p for p in fixture_root.rglob("*.md")
        if "[[Nonexistent Page]]" in p.read_text()
    ]
    assert referrers, "No doc wikilinks to [[Nonexistent Page]]"


def test_url_links_present(fixture_root):
    doc = (fixture_root / "inbox/url-links.md").read_text()
    assert re.search(r"\[[^\]]+\]\(https://", doc), "Expected markdown URL links"


def test_folder_note_patterns(fixture_root):
    has_index = any(p.name == "_index.md" for p in fixture_root.rglob("*.md"))
    has_readme_in_subdir = any(
        p.name == "README.md" and p.parent != fixture_root
        for p in fixture_root.rglob("*.md")
    )
    assert has_index, "Expected at least one _index.md"
    assert has_readme_in_subdir, "Expected at least one README.md inside a subdirectory"


def test_doc_with_no_frontmatter(fixture_root):
    doc = (fixture_root / "daily/no-frontmatter.md").read_text()
    assert not doc.startswith("---")


def test_doc_with_only_frontmatter(fixture_root):
    doc = (fixture_root / "daily/frontmatter-only.md").read_text()
    assert doc.startswith("---")
    parts = doc.split("---\n")
    # Header --- block + body. Body should be empty.
    body = parts[-1].strip()
    assert body == "", f"Expected empty body after frontmatter, got: {body[:40]!r}"


def test_empty_section_present(fixture_root):
    doc = (fixture_root / "daily/empty-section.md").read_text()
    assert re.search(r"^## Empty\s*\n\s*\n## ", doc, re.MULTILINE), (
        "Expected two consecutive H2s with no body between them"
    )


def test_dense_wikilink_section(fixture_root):
    doc = (fixture_root / "inbox/dense-hub.md").read_text()
    links = re.findall(r"\[\[[^\]]+\]\]", doc)
    assert len(links) >= 15, f"Expected ≥15 wikilinks in dense-hub, got {len(links)}"


def test_cycle_in_links_to_graph(fixture_root):
    a = (fixture_root / "inbox/cycle-a.md").read_text()
    b = (fixture_root / "inbox/cycle-b.md").read_text()
    assert "[[cycle-b]]" in a
    assert "[[cycle-a]]" in b


def test_vault_id_marker(fixture_root):
    vault_id = (fixture_root / ".ki/vault-id").read_text().strip()
    uuid_re = (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert re.match(uuid_re, vault_id), f"vault-id is not a UUID v4: {vault_id}"


def test_aliases_drive_some_wikilinks(fixture_root):
    """At least one frontmatter-bearing doc has an `aliases:` list."""
    found = False
    for p in fixture_root.rglob("*.md"):
        text = p.read_text()
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            continue
        fm = yaml.safe_load(m.group(1)) or {}
        if fm.get("aliases"):
            found = True
            break
    assert found, "Expected at least one doc with frontmatter `aliases:`"


# ──────────────────────────────────────────────────────────────────────────────
# Size envelopes — ±10% tolerance per the spec
# ──────────────────────────────────────────────────────────────────────────────

SIZE_TARGETS = {
    "tiny":  {"files": 20,  "total_bytes": 100_000,    "max_file_bytes": 10_000},
    "small": {"files": 200, "total_bytes": 10_000_000, "max_file_bytes": 100_000},
}


@pytest.mark.parametrize("size", list(SIZE_TARGETS.keys()))
def test_size_targets(tmp_path, size):
    out = tmp_path / "v"
    run_generator(size, out)
    target = SIZE_TARGETS[size]
    md_files = list(out.rglob("*.md"))
    # README.md counts toward total bytes but not toward the "files" count
    # (the spec's "~20 files" refers to actual content, not the meta-readme).
    content_files = [p for p in md_files if p.name != "README.md"]
    total = sum(p.stat().st_size for p in md_files)
    assert 0.9 * target["files"] <= len(content_files) <= 1.1 * target["files"], (
        f"file count {len(content_files)} out of band for {size} "
        f"(target {target['files']})"
    )
    assert 0.9 * target["total_bytes"] <= total <= 1.1 * target["total_bytes"], (
        f"total bytes {total} out of band for {size} "
        f"(target {target['total_bytes']})"
    )
    over_cap = [p for p in md_files if p.stat().st_size > target["max_file_bytes"]]
    assert not over_cap, (
        f"Files exceed per-file cap ({target['max_file_bytes']}): "
        + ", ".join(f"{p.name}={p.stat().st_size}" for p in over_cap[:3])
    )


# ──────────────────────────────────────────────────────────────────────────────
# Zip output
# ──────────────────────────────────────────────────────────────────────────────

def test_zip_round_trip(tmp_path):
    out = tmp_path / "vault"
    run_generator("tiny", out, zip_out=True)
    zip_path = Path(str(out) + ".zip")
    assert zip_path.exists(), "Zip not created"
    extract = tmp_path / "extracted"
    extract.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract)
    # Byte-equal round-trip for every file.
    for p in out.rglob("*"):
        if p.is_file():
            rel = p.relative_to(out)
            target = extract / rel
            assert target.exists(), f"Missing in zip: {rel}"
            assert target.read_bytes() == p.read_bytes(), f"Bytes differ: {rel}"
