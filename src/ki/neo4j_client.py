"""Neo4j driver lifecycle.

Single sync write session per ingest (Scalability lever 5 — no concurrent
writes). We keep the driver open for the duration of the command, the session
for the duration of the per-vault write batch.
"""

from __future__ import annotations

from contextlib import contextmanager

from neo4j import GraphDatabase, Session
from neo4j.exceptions import ClientError

from .config import Profile
from .ingest.queries import SCHEMA_STATEMENTS


@contextmanager
def driver_for(profile: Profile):
    driver = GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password))
    try:
        yield driver
    finally:
        driver.close()


def ensure_schema(session: Session) -> None:
    """Apply constraints and the fulltext index. Idempotent.

    Each statement uses `IF NOT EXISTS` so it can be safely re-run.
    """
    for stmt in SCHEMA_STATEMENTS:
        try:
            session.run(stmt).consume()
        except ClientError as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "equivalent" in msg:
                continue
            raise


def verify_connectivity(profile: Profile) -> None:
    """Raise the underlying exception if the profile can't connect."""
    with driver_for(profile) as driver:
        driver.verify_connectivity()
