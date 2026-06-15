"""Bridge `ki` profiles into `neo4j-cli`'s dbms credential store.

So an agent can run delegated **graph-reasoning** Cypher with
`neo4j-cli query "<cypher>" --credential <profile-name>` — without ever
handling the password. `ki` registers the credential here once; the password
flows `ki config` → `neo4j-cli`'s store via a 0600 temp `--env` file (never on
argv, never in `ps`, never in the agent's session). The agent only ever uses
the credential *name* (= the profile name, which is non-secret).

`neo4j-cli` is an **optional** dependency — every entry point here is
best-effort and degrades cleanly when it isn't installed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile

from .config import Profile

NEO4J_CLI = "neo4j-cli"


def is_available() -> bool:
    """Whether the `neo4j-cli` binary is on PATH."""
    return shutil.which(NEO4J_CLI) is not None


def register_credential(profile: Profile) -> None:
    """(Re)register `profile` as a `neo4j-cli` dbms credential named `profile.name`.

    Idempotent upsert: drop any existing credential of that name, then add the
    current connection. The password is written to a 0600 temp file and passed
    via `--env` — it never appears on the command line.

    Raises `FileNotFoundError` if `neo4j-cli` isn't installed, or `RuntimeError`
    if the `add` fails (carrying neo4j-cli's stderr). Callers choose whether to
    treat those as fatal (`ki profile sync`) or best-effort (`ki configure`).
    """
    if not is_available():
        raise FileNotFoundError("neo4j-cli not found on PATH")

    # Aura-export format (neo4j-cli `credential dbms add --env` recognises these).
    lines = [
        f"NEO4J_URI={profile.uri}",
        f"NEO4J_USERNAME={profile.user}",
        f"NEO4J_PASSWORD={profile.password}",
    ]
    if profile.database:
        lines.append(f"NEO4J_DATABASE={profile.database}")

    fd, env_path = tempfile.mkstemp(prefix="ki-neo4j-cli-", suffix=".env")
    try:
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 before writing secrets
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        # Drop an existing cred of this name first (ignore "not found").
        subprocess.run(
            [NEO4J_CLI, "credential", "dbms", "remove", profile.name,
             "--rw", "--yes", "--force"],
            check=False, capture_output=True, text=True,
        )
        res = subprocess.run(
            [NEO4J_CLI, "credential", "dbms", "add",
             "--name", profile.name, "--env", env_path, "--rw"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(
                (res.stderr or res.stdout).strip()
                or "neo4j-cli credential dbms add failed"
            )
    finally:
        os.unlink(env_path)
