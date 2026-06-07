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


class _ProfiledDriver:
    """Wraps a Neo4j driver so every `session()` targets the profile's database.

    When the profile's `database` is None we pass nothing — the driver then
    uses the server's *home* database, which is correct for standard Neo4j
    (`neo4j`) and for Aura (the instance DBID). Forcing `database="neo4j"`
    ourselves would break Aura Free, so we only set it when explicitly known.
    Everything else (verify_connectivity, close, …) delegates to the driver.
    """

    def __init__(self, driver, database: str | None):
        self._driver = driver
        self._database = database

    def session(self, **kwargs):
        if self._database and "database" not in kwargs:
            kwargs["database"] = self._database
        return self._driver.session(**kwargs)

    def __getattr__(self, name):
        return getattr(self._driver, name)


@contextmanager
def driver_for(profile: Profile):
    driver = GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password))
    try:
        yield _ProfiledDriver(driver, profile.database)
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
