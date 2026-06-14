"""Build the User and LOADED-edge provenance bags for an ingest.

Everything is best-effort: anything we can't detect becomes `null`. The
philosophy (docs/data-model/schema.md *Provenance philosophy*) is: detect from things
the user has already shared with their OS / git config / agent; never prompt.
"""

from __future__ import annotations

import getpass
import locale as _locale
import os
import platform
import socket
import subprocess
from datetime import UTC, datetime
from typing import Any

from .. import __version__


def detect_user_id() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()


def _git_config(key: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if out.returncode == 0:
            val = out.stdout.strip()
            return val or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


def detect_user_display_name() -> str | None:
    return _git_config("user.name")


def detect_user_email() -> str | None:
    return _git_config("user.email")


def detect_locale() -> str | None:
    try:
        loc = _locale.getlocale()
        if loc and loc[0]:
            return ".".join(x for x in loc if x)
    except Exception:  # noqa: BLE001
        pass
    return None


def detect_timezone() -> str | None:
    try:
        return datetime.now().astimezone().tzname()
    except Exception:  # noqa: BLE001
        return None


def build_user_mutable() -> dict[str, Any]:
    """Return the property bag for the User MERGE update (excludes id)."""
    out: dict[str, Any] = {}
    name = detect_user_display_name()
    email = detect_user_email()
    if name:
        out["displayName"] = name
    if email:
        out["email"] = email
    return out


def build_load_provenance(*, agent_name: str | None = None) -> dict[str, Any]:
    """Build the property bag for a LOADED edge."""
    out: dict[str, Any] = {
        "agentName": agent_name or os.environ.get("KI_AGENT_NAME") or "ki",
        "agentVersion": __version__,
        "graphVaultVersion": __version__,
        "os": platform.system() or None,
        "osVersion": platform.release() or None,
        "hostname": socket.gethostname() or None,
        "pythonVersion": platform.python_version(),
    }
    tz = detect_timezone()
    if tz:
        out["timezone"] = tz
    loc = detect_locale()
    if loc:
        out["locale"] = loc
    model = os.environ.get("KI_MODEL_ID")
    if model:
        out["modelId"] = model
    # Drop falsy keys so we don't write empty strings.
    return {k: v for k, v in out.items() if v is not None}


def now_utc() -> datetime:
    return datetime.now(tz=UTC)
