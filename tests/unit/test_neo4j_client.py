"""`driver_for` binds the profile's database onto every session(); Profile db round-trip."""

from __future__ import annotations

from ki import neo4j_client
from ki.config import Profile


class _FakeDriver:
    def __init__(self):
        self.session_kwargs = []

    def session(self, **kwargs):
        self.session_kwargs.append(kwargs)
        return kwargs

    def close(self):
        pass


def test_profiled_driver_injects_database_when_set():
    fake = _FakeDriver()
    neo4j_client._ProfiledDriver(fake, "mydb").session()
    assert fake.session_kwargs[-1].get("database") == "mydb"


def test_profiled_driver_omits_database_when_none():
    fake = _FakeDriver()
    neo4j_client._ProfiledDriver(fake, None).session()
    assert "database" not in fake.session_kwargs[-1]


def test_profiled_driver_respects_explicit_database_arg():
    fake = _FakeDriver()
    neo4j_client._ProfiledDriver(fake, "mydb").session(database="other")
    assert fake.session_kwargs[-1]["database"] == "other"


def test_profile_database_roundtrips_and_omits_when_none():
    p = Profile(
        name="x", uri="bolt://h:7687", user="neo4j", password="pw",
        source="existing", database="proj",
    )
    assert p.to_dict()["database"] == "proj"
    assert Profile.from_dict("x", p.to_dict()).database == "proj"

    bare = Profile(name="y", uri="u", user="neo4j", password="pw")
    assert "database" not in bare.to_dict()
    assert Profile.from_dict("y", bare.to_dict()).database is None
