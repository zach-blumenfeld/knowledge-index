"""Batcher: chunking and Neo4j-OOM auto-recovery."""

from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import TransientError

from ki.ingest.batcher import MIN_BATCH_SIZE, chunks, run_batched


def test_chunks_yields_exact_slices():
    rows = list(range(10))
    assert list(chunks(rows, 3)) == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]


def test_chunks_handles_empty():
    assert list(chunks([], 5)) == []


def test_chunks_rejects_nonpositive_size():
    with pytest.raises(ValueError):
        list(chunks([1, 2, 3], 0))


def _make_session(side_effects):
    """Make a mock session that returns canned side-effects across run() calls."""
    session = MagicMock()
    # session.run().consume() is what we hit. We let the mock chain naturally
    # so each call_args triggers the next side_effect.
    calls = {"n": 0}

    def _run(*_args, **_kwargs):
        side = side_effects[calls["n"]]
        calls["n"] += 1
        if isinstance(side, Exception):
            raise side
        result = MagicMock()
        result.consume.return_value = None
        return result

    session.run.side_effect = _run
    return session, calls


def test_batcher_chunks_at_size():
    session, calls = _make_session([None, None, None])
    rows = [{"i": i} for i in range(7)]
    n = run_batched(session, "MATCH (n) RETURN n", "rows", rows, batch_size=3)
    assert n == 7
    assert calls["n"] == 3  # 3, 3, 1


def test_batcher_halves_on_oom_and_continues_smaller():
    """First call OOMs at size=128; retry succeeds at size=64, subsequent batches use size=64."""
    oom = TransientError("Out of memory while executing query")
    side_effects = [
        oom,    # initial size-128 batch (rows 0..128) → OOM
        None,   # retry size-64 (first 64 rows) succeeds
        None,   # next 64 rows
        None,   # remaining 64 rows
    ]
    session, calls = _make_session(side_effects)
    rows = [{"i": i} for i in range(192)]
    shrink_log: list[int] = []
    n = run_batched(
        session, "MATCH (n) RETURN n", "rows", rows,
        batch_size=128,
        on_shrink=shrink_log.append,
    )
    assert n == 192
    assert calls["n"] == 4  # 1 failed + 3 successes
    assert shrink_log == [64]  # warned exactly once, with new size


def test_batcher_emits_only_one_shrink_warning():
    oom = TransientError("Out of memory")
    # Two OOMs in a row: shrinks twice but warning fires only once.
    side_effects = [oom, oom, None, None, None]
    session, _ = _make_session(side_effects)
    shrink_log: list[int] = []
    rows = [{"i": i} for i in range(128)]
    run_batched(
        session, "Q", "rows", rows, batch_size=256,
        on_shrink=shrink_log.append,
    )
    assert len(shrink_log) == 1  # one user-facing warning


def test_batcher_propagates_non_oom_transient():
    other = TransientError("deadlock detected")
    session, _ = _make_session([other])
    with pytest.raises(TransientError):
        run_batched(session, "Q", "rows", [{"i": 1}], batch_size=10)


def test_batcher_gives_up_when_already_at_min_size():
    """If we're already at MIN_BATCH_SIZE and still OOM, propagate."""
    oom = TransientError("Out of memory")
    side_effects = [oom]
    session, _ = _make_session(side_effects)
    rows = [{"i": i} for i in range(MIN_BATCH_SIZE)]
    with pytest.raises(TransientError):
        run_batched(session, "Q", "rows", rows, batch_size=MIN_BATCH_SIZE)
