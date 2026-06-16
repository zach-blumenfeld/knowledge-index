"""Unit tests for the podman port-selection helpers (no podman required)."""

from __future__ import annotations

import socket

from ki import neo4j_podman


def test_find_free_port_returns_start_when_nothing_listening():
    # Grab an ephemeral port number, then release it so nothing is listening.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert neo4j_podman._find_free_port(free) == free


def test_find_free_port_skips_a_bound_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen()
    bound = s.getsockname()[1]
    try:
        got = neo4j_podman._find_free_port(bound)
        assert got != bound
        assert got > bound
    finally:
        s.close()
