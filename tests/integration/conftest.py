"""Integration-test plumbing.

The suite needs a reachable Neo4j. To run it, point at any Neo4j you have
(local Podman per `references/neo4j-podman.md`, Aura, anything Bolt-reachable):

    export KI_TEST_NEO4J_URI=bolt://localhost:7687
    export KI_TEST_NEO4J_USER=neo4j
    export KI_TEST_NEO4J_PASSWORD=password
    uv run pytest tests/ -v

Without those env vars the integration tests are skipped (unit tests still run).
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from ki.config import Profile
from ki.neo4j_client import driver_for


def _has_env_creds() -> bool:
    return all(
        os.environ.get(k)
        for k in ("KI_TEST_NEO4J_URI", "KI_TEST_NEO4J_USER", "KI_TEST_NEO4J_PASSWORD")
    )


def _can_reach_neo4j(profile: Profile) -> bool:
    try:
        with driver_for(profile) as driver:
            driver.verify_connectivity()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def neo4j_profile() -> Iterator[Profile]:
    """Return a usable Profile for the integration suite.

    Skips if no Neo4j is reachable.
    """
    if not _has_env_creds():
        pytest.skip(
            "integration tests need KI_TEST_NEO4J_URI / KI_TEST_NEO4J_USER / "
            "KI_TEST_NEO4J_PASSWORD env vars set. To bring up a local Neo4j, "
            "see references/neo4j-podman.md."
        )

    profile = Profile(
        name="ki-test",
        uri=os.environ["KI_TEST_NEO4J_URI"],
        user=os.environ["KI_TEST_NEO4J_USER"],
        password=os.environ["KI_TEST_NEO4J_PASSWORD"],
        source="existing",
    )

    if not _can_reach_neo4j(profile):
        pytest.skip(f"could not reach Neo4j at {profile.uri}")

    yield profile


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Copy the sample_vault fixture into an isolated tmp dir per test."""
    src = Path(__file__).resolve().parent.parent / "fixtures" / "sample_vault"
    dst = tmp_path / "vault"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def cleanup_vault(neo4j_profile: Profile) -> Iterator[list[str]]:
    """Yields a list to which test code can append vault URIs to delete on teardown.

    Ensures each test starts/leaves a clean slate even if a prior test died mid-write.
    """
    to_clean: list[str] = []
    yield to_clean
    if not to_clean:
        return
    from ki.ingest.remove import remove_vault  # local import; pulls Cypher

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            for vault_uri in to_clean:
                try:
                    remove_vault(session, vault_uri)
                except Exception:  # noqa: BLE001
                    pass


@pytest.fixture
def unique_vault_id() -> str:
    """A stable but unique vault id, in case a test wants to override the marker."""
    return str(uuid.uuid4())
