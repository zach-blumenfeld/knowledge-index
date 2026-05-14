"""Integration-test plumbing.

Strategy:
  1. If `neo4j-local` is installed, start an ephemeral instance and use its
     credentials. Stop on teardown.
  2. Otherwise, fall back to KI_TEST_NEO4J_URI / KI_TEST_NEO4J_USER /
     KI_TEST_NEO4J_PASSWORD env vars so a local Docker Neo4j (or any reachable
     instance) can be used in CI / dev.
  3. Else skip the whole integration suite.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from ki import neo4j_local
from ki.config import Profile
from ki.neo4j_client import driver_for


def _has_neo4j_local() -> bool:
    return neo4j_local.is_installed()


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
    profile: Profile | None = None
    started_local = False

    if _has_neo4j_local():
        try:
            neo4j_local.start(ephemeral=True)
            started_local = True
            creds = neo4j_local.credentials()
            profile = Profile(
                name="ki-test", uri=creds.uri, user=creds.user,
                password=creds.password, source="neo4j-local",
            )
        except neo4j_local.Neo4jLocalError as exc:
            pytest.skip(f"neo4j-local could not start: {exc}")
    elif _has_env_creds():
        profile = Profile(
            name="ki-test",
            uri=os.environ["KI_TEST_NEO4J_URI"],
            user=os.environ["KI_TEST_NEO4J_USER"],
            password=os.environ["KI_TEST_NEO4J_PASSWORD"],
            source="existing",
        )
    else:
        pytest.skip(
            "integration tests need either `neo4j-local` installed or "
            "KI_TEST_NEO4J_URI/USER/PASSWORD env vars set"
        )

    if not _can_reach_neo4j(profile):
        if started_local:
            try:
                neo4j_local.stop()
            except Exception:  # noqa: BLE001
                pass
        pytest.skip(f"could not reach Neo4j at {profile.uri}")

    yield profile

    if started_local:
        try:
            neo4j_local.stop()
        except Exception:  # noqa: BLE001
            pass


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
    from ki.ingest import queries as Q  # local import; pulls Cypher

    with driver_for(neo4j_profile) as driver:
        with driver.session() as session:
            for vault_uri in to_clean:
                try:
                    session.run(Q.DELETE_VAULT, vaultUri=vault_uri).consume()
                except Exception:  # noqa: BLE001
                    pass


@pytest.fixture
def unique_vault_id() -> str:
    """A stable but unique vault id, in case a test wants to override the marker."""
    return str(uuid.uuid4())
