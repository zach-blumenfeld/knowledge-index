"""`ki skill <install|remove|list|print>` — manage the agent-skill bundle.

The skill bundle is the markdown file at `skills/knowledge-index/SKILL.md` (shipped inside
the wheel as `ki/_resources/SKILL.md`). It tells AI agents when to invoke
`ki` and how. `ki skill install` drops it into each supported agent's
well-known config path so the agent picks it up without the user copying
files by hand.

UX shape mirrors `neo4j-cli skill` and the supported-agent catalog
(detection paths + skills directories) tracks the same set:

    ki skill list                  # supported agents + detection / install state
    ki skill install [agent]       # one agent, or all detected if omitted
    ki skill install [agent] --path <FILE>   # write to an arbitrary path
    ki skill remove  [agent]       # idempotent removal
    ki skill print                 # write the bundled SKILL.md to stdout

Adding a new agent is a one-line append to `AGENTS` below. Paths follow
`<detect_dir>` (the marker we check for the agent's presence) and
`<skills_dir>` (where bundles live). The actual file written is
`<skills_dir>/ki/SKILL.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

SKILL_RESOURCE_PACKAGE = "ki._resources"
SKILL_RESOURCE_NAME = "SKILL.md"
TOOL_NAME = "knowledge-index"


# --- agent catalog ----------------------------------------------------------


@dataclass(frozen=True)
class AgentEntry:
    """One row of the supported-agents table.

    `detect_dir` / `skills_dir` are template strings (with `~` and
    `$XDG_CONFIG_HOME`) — `_expand()` resolves them at call time so the
    `HOME`/`XDG_CONFIG_HOME` env can be swapped in tests.
    """

    name: str  # canonical lowercase id, e.g. "claude-code"
    display_name: str
    detect_dir: str  # unexpanded marker path
    skills_dir: str  # unexpanded skills-bundle root

    def detect_path(self) -> Path | None:
        return _expand(self.detect_dir)

    def skills_path(self) -> Path | None:
        return _expand(self.skills_dir)

    def skill_path(self) -> Path | None:
        """Where this tool's SKILL.md lives for this agent."""
        sk = self.skills_path()
        return sk / TOOL_NAME / SKILL_RESOURCE_NAME if sk else None

    def detected(self) -> bool:
        p = self.detect_path()
        return p is not None and p.is_dir()

    def installed(self) -> bool:
        p = self.skill_path()
        return p is not None and p.is_file()


def _expand(path_template: str) -> Path | None:
    """Resolve `~` and `$XDG_CONFIG_HOME` against the live environment.

    Mirrors neo4j-cli's `expandPath`: `~` → $HOME, `$XDG_CONFIG_HOME` falls
    back to `$HOME/.config` when unset. Returns None when $HOME is needed
    but missing (very rare; signals "treat as not detected").
    """
    home = os.environ.get("HOME")
    if path_template == "~":
        return Path(home) if home else None
    if path_template.startswith("~/"):
        if not home:
            return None
        return Path(home) / path_template[2:]
    if "$XDG_CONFIG_HOME" in path_template:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if not xdg:
            if not home:
                return None
            xdg = str(Path(home) / ".config")
        return Path(path_template.replace("$XDG_CONFIG_HOME", xdg))
    return Path(path_template)


# Supported agents. Mirrors neo4j-cli's catalog so users have one mental
# model across both tools. Order is preserved for stable list output.
AGENTS: dict[str, AgentEntry] = {
    e.name: e
    for e in [
        AgentEntry("claude-code", "Claude Code", "~/.claude", "~/.claude/skills"),
        AgentEntry("cursor", "Cursor", "~/.cursor", "~/.cursor/skills"),
        AgentEntry(
            "windsurf", "Windsurf",
            "~/.codeium/windsurf", "~/.codeium/windsurf/skills",
        ),
        AgentEntry("copilot", "Copilot", "~/.copilot", "~/.copilot/skills"),
        AgentEntry("gemini-cli", "Gemini CLI", "~/.gemini", "~/.gemini/skills"),
        # Cline historically uses `~/.agents/skills` despite the `~/.cline`
        # marker — see neo4j-cli/common/skill/agents.go.
        AgentEntry("cline", "Cline", "~/.cline", "~/.agents/skills"),
        AgentEntry("codex", "Codex", "~/.codex", "~/.codex/skills"),
        AgentEntry("pi", "Pi", "~/.pi/agent", "~/.pi/agent/skills"),
        AgentEntry(
            "opencode", "OpenCode",
            "$XDG_CONFIG_HOME/opencode", "$XDG_CONFIG_HOME/opencode/skills",
        ),
        AgentEntry("junie", "Junie", "~/.junie", "~/.junie/skills"),
    ]
}


# --- bundled skill IO -------------------------------------------------------


def read_bundled_skill() -> str:
    """Read SKILL.md.

    In an installed wheel: hatchling `force-include` puts the file at
    `ki/_resources/SKILL.md` so `importlib.resources` finds it.

    In a dev checkout (editable install): the wheel hasn't been built, so
    fall back to the canonical repo path `skills/knowledge-index/SKILL.md`, located by
    walking up from this module to the repo root. The fallback never fires
    in production.
    """
    try:
        return (resources.files(SKILL_RESOURCE_PACKAGE) / SKILL_RESOURCE_NAME).read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    # Dev fallback: src/ki/commands/skill.py → src/ki/commands → src/ki → src → <repo>.
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "skills" / "knowledge-index" / "SKILL.md"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "could not locate the bundled SKILL.md — neither "
        f"`{SKILL_RESOURCE_PACKAGE}.{SKILL_RESOURCE_NAME}` nor `{candidate}` exists."
    )


# --- command entry points ---------------------------------------------------


def _lookup(agent: str) -> AgentEntry:
    """Look up an agent by name (case-insensitive). Exit non-zero if unknown."""
    key = agent.lower()
    if key not in AGENTS:
        raise SystemExit(
            f"unknown agent {agent!r}. Supported: {', '.join(AGENTS)}"
        )
    return AGENTS[key]


def _detected_agents() -> list[AgentEntry]:
    return [a for a in AGENTS.values() if a.detected()]


def cmd_install(agent: str | None, path: Path | None = None) -> int:
    """Install the skill into one agent, or all detected agents.

    `path` is an escape hatch for agents not in the catalog (or non-standard
    locations). When given with an `agent` name, it overrides that agent's
    target path; when given without `agent`, it just writes to `path` and
    labels the action with the path as the "display name".
    """
    body = read_bundled_skill()

    if path is not None:
        # Explicit-path mode: ignore catalog, just write to where the user said.
        target = path.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        label = agent or "custom"
        console.print(f"[green]✓[/green] installed [bold]{label}[/bold] skill → {target}")
        return 0

    if agent is None:
        targets = _detected_agents()
        if not targets:
            raise SystemExit(
                "no supported agents detected. Pass an agent name explicitly "
                f"(one of: {', '.join(AGENTS)}), or use --path <FILE> to "
                "install to a custom location."
            )
    else:
        targets = [_lookup(agent)]

    for t in targets:
        skill_path = t.skill_path()
        if skill_path is None:
            console.print(
                f"[yellow]![/yellow] skipping {t.display_name}: "
                "could not resolve install path (HOME / XDG_CONFIG_HOME unset)."
            )
            continue
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(body, encoding="utf-8")
        console.print(
            f"[green]✓[/green] installed [bold]{t.display_name}[/bold] skill → {skill_path}"
        )
    return 0


def cmd_remove(agent: str | None) -> int:
    """Remove the installed skill from one agent, or every agent with one installed."""
    targets = [_lookup(agent)] if agent else [a for a in AGENTS.values() if a.installed()]
    if not targets:
        console.print("[dim](no installed skills to remove)[/dim]")
        return 0
    for t in targets:
        skill_path = t.skill_path()
        if skill_path is not None and skill_path.exists():
            skill_path.unlink()
            console.print(f"[green]✓[/green] removed {skill_path}")
            # Clean up the per-tool dir we created on install (but not the
            # agent's top-level `skills/` — other tools may live there).
            parent = skill_path.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        else:
            console.print(f"[dim](not installed: {t.display_name})[/dim]")
    return 0


def cmd_list() -> int:
    table = Table(show_header=True, header_style="bold")
    table.add_column("agent")
    table.add_column("display name")
    table.add_column("detected", justify="center")
    table.add_column("installed", justify="center")
    table.add_column("path", style="dim", overflow="fold")
    for t in AGENTS.values():
        skill_path = t.skill_path()
        table.add_row(
            t.name,
            t.display_name,
            "[green]yes[/green]" if t.detected() else "no",
            "[green]yes[/green]" if t.installed() else "no",
            str(skill_path) if skill_path else "(unresolved)",
        )
    console.print(table)
    return 0


def cmd_print() -> int:
    # Plain stdout, no rich formatting — so it pipes cleanly into a file or pager.
    print(read_bundled_skill(), end="")
    return 0
