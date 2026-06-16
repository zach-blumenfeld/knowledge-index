"""Neo4j driver lifecycle.

Single sync write session per ingest (Scalability lever 5 — no concurrent
writes). We keep the driver open for the duration of the command, the session
for the duration of the per-vault write batch.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from neo4j import GraphDatabase, Session
from neo4j.exceptions import AuthError, ClientError

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


# Connectivity-probe outcomes for `ki status`. The three failure values are
# the exact state strings `ki status` reports, so a caller can return the
# result verbatim.
CONN_REACHABLE = "REACHABLE"
CONN_DOWN = "NEO4J_DOWN"
CONN_UNRESPONSIVE = "NEO4J_UNRESPONSIVE"
CONN_AUTH_ERROR = "AUTH_ERROR"


def classify_connectivity(profile: Profile, timeout_s: float = 5.0) -> str:
    """Probe `profile` and classify the result for `ki status`.

    Returns one of CONN_REACHABLE / CONN_DOWN / CONN_UNRESPONSIVE /
    CONN_AUTH_ERROR. We can't know reachability without trying, so this
    actually opens a connection (with a short `connection_timeout` so status
    never hangs on the driver's 30s default).

    DOWN vs UNRESPONSIVE is split by elapsed time: a refused connection
    (nothing listening) comes back near-instantly, while a half-open / starting
    server eats most of the timeout before failing. Auth failures are their own
    class regardless of timing.
    """
    driver = GraphDatabase.driver(
        profile.uri,
        auth=(profile.user, profile.password),
        connection_timeout=timeout_s,
    )
    start = time.monotonic()
    try:
        driver.verify_connectivity()
        return CONN_REACHABLE
    except AuthError:
        return CONN_AUTH_ERROR
    except Exception:  # noqa: BLE001 — ServiceUnavailable, OSError, config, etc.
        elapsed = time.monotonic() - start
        if elapsed >= timeout_s * 0.8:
            return CONN_UNRESPONSIVE
        return CONN_DOWN
    finally:
        driver.close()
