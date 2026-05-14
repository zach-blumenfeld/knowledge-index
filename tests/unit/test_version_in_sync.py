"""The two version sources (pyproject.toml + src/ki/__init__.py) must agree.

Previous releases shipped with the two drifted (pyproject at 0.2.0 while
`ki.__version__` stayed at 0.1.0). This test makes the drift a CI failure.
"""

from __future__ import annotations

import pathlib
import tomllib

import ki

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_version_in_sync():
    py = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert py["project"]["version"] == ki.__version__
