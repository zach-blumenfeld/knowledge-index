"""Config file: profiles for Neo4j connections.

Location (XDG-first, fallback to ~/.ki/config.yaml):
  - $XDG_CONFIG_HOME/ki/config.yaml
  - ~/.config/ki/config.yaml      (XDG default)
  - ~/.ki/config.yaml             (fallback when XDG dir doesn't exist)

File mode is 0600 (owner read/write only) — passwords stored in plaintext in v1.

Shape:

```yaml
default_profile: local
profiles:
  local:
    uri: "bolt://localhost:7687"
    user: "neo4j"
    password: "..."
    source: "local-podman"    # one of: local-podman | aura | existing
    database: "neo4j"         # optional; omit to use the server's home database
  work:
    uri: "neo4j+s://..."
    user: "neo4j"
    password: "..."
    source: "aura"
```
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILENAME = "config.yaml"
PROFILE_ENV_VAR = "KI_PROFILE"


@dataclass
class Profile:
    name: str
    uri: str
    user: str
    password: str
    source: str = "existing"  # local-podman | aura | existing
    # Which database within the instance. None → use the server's home
    # database (correct for standard Neo4j *and* Aura, whose home db is the
    # instance DBID). Never default this to "neo4j" — that breaks Aura Free.
    database: str | None = None

    def to_dict(self) -> dict:
        d = {
            "uri": self.uri,
            "user": self.user,
            "password": self.password,
            "source": self.source,
        }
        if self.database:
            d["database"] = self.database
        return d

    @classmethod
    def from_dict(cls, name: str, data: dict) -> Profile:
        return cls(
            name=name,
            uri=data["uri"],
            user=data["user"],
            password=data["password"],
            source=data.get("source", "existing"),
            database=data.get("database"),
        )


@dataclass
class Config:
    profiles: dict[str, Profile] = field(default_factory=dict)
    default_profile: str | None = None
    path: Path | None = None  # where this config was loaded from, if any

    def get_profile(self, name: str | None = None) -> Profile:
        """Resolve a profile by name, env-var override, or the default."""
        target = name or os.environ.get(PROFILE_ENV_VAR) or self.default_profile
        if not target:
            if len(self.profiles) == 1:
                return next(iter(self.profiles.values()))
            raise KeyError(
                "no profile selected — pass --profile or set a default_profile in config"
            )
        if target not in self.profiles:
            raise KeyError(f"profile '{target}' not found in {self.path}")
        return self.profiles[target]

    def add_profile(self, profile: Profile, set_default: bool | None = None) -> None:
        """Add or replace a profile. If first profile or set_default=True, becomes default."""
        is_first = len(self.profiles) == 0
        self.profiles[profile.name] = profile
        if set_default is True or (set_default is None and is_first):
            self.default_profile = profile.name

    def to_dict(self) -> dict:
        return {
            "default_profile": self.default_profile,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
        }


# --- path resolution ---------------------------------------------------------


def xdg_config_home() -> Path:
    """Return $XDG_CONFIG_HOME or ~/.config (XDG default)."""
    env = os.environ.get("XDG_CONFIG_HOME")
    if env:
        return Path(env)
    return Path.home() / ".config"


def default_config_path() -> Path:
    """Path to write a new config. XDG-first."""
    return xdg_config_home() / "ki" / CONFIG_FILENAME


def fallback_config_path() -> Path:
    """Non-XDG fallback: ~/.ki/config.yaml."""
    return Path.home() / ".ki" / CONFIG_FILENAME


def find_config_path() -> Path | None:
    """Return the path of an existing config (XDG-first), or None if none exist."""
    primary = default_config_path()
    if primary.exists():
        return primary
    fallback = fallback_config_path()
    if fallback.exists():
        return fallback
    return None


# --- IO ---------------------------------------------------------------------


def load_config(path: Path | None = None) -> Config:
    """Load config from `path`, or auto-discover."""
    resolved = path or find_config_path()
    if resolved is None or not resolved.exists():
        cfg = Config()
        cfg.path = resolved
        return cfg
    with resolved.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    profiles_raw = data.get("profiles") or {}
    profiles = {name: Profile.from_dict(name, p) for name, p in profiles_raw.items()}
    cfg = Config(
        profiles=profiles,
        default_profile=data.get("default_profile"),
        path=resolved,
    )
    return cfg


def save_config(config: Config, path: Path | None = None) -> Path:
    """Write the config to disk with mode 0600. Returns the path written."""
    target = path or config.path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(config.to_dict(), default_flow_style=False, sort_keys=False)
    # Write to a temp file first, then atomic-replace + chmod, so we never
    # leave a world-readable config on disk mid-write.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    os.replace(tmp, target)
    config.path = target
    return target
