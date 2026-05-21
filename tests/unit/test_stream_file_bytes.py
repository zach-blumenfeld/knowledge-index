"""#54 Fix 1 — `_stream_file_bytes` streams reads in batches."""

from __future__ import annotations

from pathlib import Path

from ki.ingest.pipeline import _stream_file_bytes


def _make_files(tmp_path: Path, n: int) -> list[Path]:
    paths: list[Path] = []
    for i in range(n):
        p = tmp_path / f"file-{i:03d}.md"
        p.write_bytes(f"contents of {i}\n".encode())
        paths.append(p)
    return paths


def test_yields_every_path_in_input_order(tmp_path: Path) -> None:
    paths = _make_files(tmp_path, 5)
    got = list(_stream_file_bytes(paths, concurrency=2, batch_size=2))
    assert [p for p, _ in got] == paths
    assert [b.decode() for _, b in got] == [
        "contents of 0\n",
        "contents of 1\n",
        "contents of 2\n",
        "contents of 3\n",
        "contents of 4\n",
    ]


def test_empty_input_yields_nothing(tmp_path: Path) -> None:
    assert list(_stream_file_bytes([], concurrency=4, batch_size=4)) == []


def test_on_batch_read_callback_fires_once_per_batch(tmp_path: Path) -> None:
    paths = _make_files(tmp_path, 10)
    seen: list[int] = []
    list(
        _stream_file_bytes(
            paths, concurrency=2, batch_size=4, on_batch_read=seen.append,
        )
    )
    # 10 files / batch_size=4 → batches of 4, 4, 2
    assert seen == [4, 4, 2]


def test_batch_size_larger_than_input_reads_one_batch(tmp_path: Path) -> None:
    paths = _make_files(tmp_path, 3)
    seen: list[int] = []
    got = list(
        _stream_file_bytes(
            paths, concurrency=4, batch_size=64, on_batch_read=seen.append,
        )
    )
    assert len(got) == 3
    assert seen == [3]
