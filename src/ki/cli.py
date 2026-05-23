"""Click entry point — wires up `ki <command>`.

User-visible commands:
  ki configure              one-time Neo4j connection setup
  ki index <path>           sync a folder of markdown into the graph (re-index = full nuke + re-ingest)
  ki search <query>         fulltext across {Document,Section,Vault} (B.1 / B.2 / B.11)
  ki outline [<uri>]        render the containment tree (B.12). `ki tree` is a kept alias.
  ki get <uri> ...          fetch metadata + content for a Document / Section URI
  ki rm <vault>             remove an entire vault from the index (vault-only — see docs/index_rm_behavior.md)
  ki nuke                   reset the entire graph and drop all schema (typed confirmation required)
  ki vault list             list every indexed vault with its description
  ki init <path>            (advanced) write `.ki/vault.yaml` without indexing
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from . import __version__
from .commands.configure import configure as configure_flow
from .commands.get import cmd_get
from .commands.index import cmd_index
from .commands.init import cmd_init
from .commands.nuke import cmd_nuke
from .commands.outline import cmd_outline
from .commands.rm import cmd_rm
from .commands.search import cmd_search
from .commands.skill import cmd_install as cmd_skill_install
from .commands.skill import cmd_list as cmd_skill_list
from .commands.skill import cmd_print as cmd_skill_print
from .commands.skill import cmd_remove as cmd_skill_remove
from .commands.vault import cmd_vault_list
from .ingest.batcher import DEFAULT_BATCH_SIZE
from .ingest.pipeline import DEFAULT_CONCURRENCY, DEFAULT_MAX_FILE_SIZE


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="knowledge-index — search index for agent memory",
)
@click.version_option(__version__, "-V", "--version", prog_name="ki")
@click.option("-v", "--verbose", count=True, help="increase log verbosity (-v, -vv)")
def main(verbose: int) -> None:
    level = logging.WARNING - 10 * min(verbose, 2)
    logging.basicConfig(level=max(level, logging.DEBUG), format="%(levelname)s %(message)s")


@main.command("configure", help="Set up a Neo4j connection profile.")
@click.option("--profile", "profile_name", default=None, help="Profile name to write")
@click.option(
    "--yes", "yes_flag", is_flag=True, default=False,
    help="Skip prompts — pick the default (Local) and proceed.",
)
@click.option(
    "--set-default", is_flag=True, default=False,
    help="Make this profile the default even if others exist.",
)
def configure_cmd(profile_name: str | None, yes_flag: bool, set_default: bool) -> None:
    configure_flow(profile_name=profile_name, yes=yes_flag, set_default=set_default)


@main.command("index", help="Sync a folder of markdown into the graph.")
@click.argument("path", type=click.Path(file_okay=False, dir_okay=True, path_type=Path))
@click.option("--profile", default=None, help="Profile name (overrides KI_PROFILE / default)")
@click.option(
    "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
    help=f"Rows per UNWIND batch (default: {DEFAULT_BATCH_SIZE})",
)
@click.option(
    "--max-file-size", type=int, default=DEFAULT_MAX_FILE_SIZE,
    help=f"Skip files larger than this many bytes (default: {DEFAULT_MAX_FILE_SIZE})",
)
@click.option(
    "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
    help=f"Concurrent file reads (default: {DEFAULT_CONCURRENCY})",
)
@click.option(
    "--yes", "yes_flag", is_flag=True, default=False,
    help="If no config exists, auto-pick Local Neo4j without prompting. "
         "Also suppresses the first-run description prompt.",
)
@click.option(
    "--description", "description", default=None,
    help="Set the vault's `description:` (in .ki/vault.yaml) before indexing. "
         "Refuses to overwrite an existing description unless --force-description.",
)
@click.option(
    "--force-description", "force_description", is_flag=True, default=False,
    help="Allow --description to overwrite an existing description.",
)
@click.option(
    "--chunk-size", "chunk_size", type=int, default=1000,
    help="Rows per batched-remove transaction during the pre-ingest vault nuke "
         "(default 1000). Only matters when re-indexing an existing vault. "
         "Lower it (e.g. 200) if you see Neo4j OOM during removal.",
)
def index_cmd(
    path: Path,
    profile: str | None,
    batch_size: int,
    max_file_size: int,
    concurrency: int,
    yes_flag: bool,
    description: str | None,
    force_description: bool,
    chunk_size: int,
) -> None:
    sys.exit(
        cmd_index(
            path,
            profile=profile,
            batch_size=batch_size,
            max_file_size=max_file_size,
            concurrency=concurrency,
            yes=yes_flag,
            description=description,
            force_description=force_description,
            chunk_size=chunk_size,
        )
    )


@main.command(
    "search",
    help="Fulltext search across documents, sections, and vaults. "
         "By default returns all three types; narrow with --types.",
)
@click.argument("query")
@click.option("--profile", default=None)
@click.option(
    "--types", "types_csv",
    default="document,section,vault",
    show_default=True,
    help="Comma-separated subset of {document,section,vault}. "
         "Default: all three. Examples: --types section / --types section,document.",
)
@click.option(
    "--k", "k", type=int, default=10,
    help="Total result cap across all selected types (default: 10).",
)
@click.option("--json", "as_json", is_flag=True, default=False)
def search_cmd(
    query: str,
    profile: str | None,
    types_csv: str,
    k: int,
    as_json: bool,
) -> None:
    sys.exit(
        cmd_search(
            query,
            profile=profile,
            types_csv=types_csv,
            k=k,
            as_json=as_json,
        )
    )


def _outline_options(f):
    """Shared option/argument stack for `ki outline` and its `ki tree` alias."""
    f = click.option(
        "--full", "full", is_flag=True, default=False,
        help="Show the vault description sub-line under each Vault row.",
    )(f)
    f = click.option(
        "--depth", "depth", type=int, default=4,
        help="Max HAS steps from each root (default: 4).",
    )(f)
    f = click.option(
        "--at", "at_flag", default=None,
        help="Back-compat alias for the positional URI argument. "
             "Prefer `ki outline <uri>`.",
    )(f)
    f = click.option(
        "--profile", default=None,
        help="Profile name (overrides KI_PROFILE / default)",
    )(f)
    f = click.argument("uri", required=False)(f)
    return f


def _run_outline(
    uri: str | None,
    at_flag: str | None,
    profile: str | None,
    depth: int,
    full: bool,
) -> None:
    sys.exit(cmd_outline(profile=profile, at=(uri or at_flag), depth=depth, full=full))


@main.command(
    "outline",
    help="Render the containment outline of indexed vaults (Vault → Folder → "
         "Document → Section). `ki tree` is a kept alias. "
         "See docs/outline-format.md for the rendered format.",
)
@_outline_options
def outline_cmd(
    uri: str | None,
    at_flag: str | None,
    profile: str | None,
    depth: int,
    full: bool,
) -> None:
    _run_outline(uri, at_flag, profile, depth, full)


@main.command(
    "tree",
    hidden=True,
    help="Alias for `ki outline`. Kept so existing skill bundles, docs, and "
         "muscle memory keep working.",
)
@_outline_options
def tree_cmd(
    uri: str | None,
    at_flag: str | None,
    profile: str | None,
    depth: int,
    full: bool,
) -> None:
    _run_outline(uri, at_flag, profile, depth, full)


@main.command(
    "get",
    help="Fetch metadata + content at a Document / Section URI. "
         "Use `ki tree` / `ki search` to find URIs first.",
)
@click.argument("uris", nargs=-1, required=True)
@click.option("--profile", default=None, help="Profile name (overrides KI_PROFILE / default)")
@click.option(
    "--type", "get_type",
    type=click.Choice(["path", "content", "full"]),
    default="content",
    help="path = metadata shell only; content = node's stored content "
         "(preamble + child URI pointers per Rule 1); full = reconstructed "
         "reading-order body via B.4 / B.14.",
)
@click.option("--json", "as_json", is_flag=True, default=False)
def get_cmd(
    uris: tuple[str, ...],
    profile: str | None,
    get_type: str,
    as_json: bool,
) -> None:
    sys.exit(
        cmd_get(
            uris,
            profile=profile,
            get_type=get_type,
            as_json=as_json,
        )
    )


@main.command(
    "rm",
    help="Remove an entire vault from the index. Source files untouched. "
         "Sub-vault granularity is not supported — see `docs/index_rm_behavior.md`.",
)
@click.argument("target")
@click.option("--profile", default=None)
@click.option("--dry-run", is_flag=True, default=False, help="Report only; no Neo4j writes.")
@click.option("--yes", "yes_flag", is_flag=True, default=False, help="Skip the typed-confirmation prompt.")
@click.option(
    "--keep-marker", is_flag=True, default=False,
    help="Keep .ki/vault.yaml on disk so the next `ki index` rebuilds onto the same Vault.uri.",
)
@click.option(
    "--chunk-size", "chunk_size", type=int, default=1000,
    help="Rows per batched-remove transaction (default 1000). "
         "Lower it (e.g. 200) if you see Neo4j OOM during removal; "
         "raise it on small graphs to cut transaction overhead.",
)
def rm_cmd(
    target: str,
    profile: str | None,
    dry_run: bool,
    yes_flag: bool,
    keep_marker: bool,
    chunk_size: int,
) -> None:
    sys.exit(
        cmd_rm(
            target,
            profile=profile,
            dry_run=dry_run,
            yes=yes_flag,
            keep_marker=keep_marker,
            chunk_size=chunk_size,
        )
    )


@main.command(
    "nuke",
    help="Reset the entire ki graph: remove every vault, drop all indexes and "
         "constraints, and remove every .ki/vault.yaml ki knows about. "
         "Typed confirmation required. Source files untouched.",
)
@click.option("--profile", default=None, help="Profile name (overrides KI_PROFILE / default)")
@click.option("--dry-run", is_flag=True, default=False, help="Report only; no changes.")
@click.option("--yes", "yes_flag", is_flag=True, default=False, help="Skip the typed-confirmation prompt.")
@click.option(
    "--keep-marker", is_flag=True, default=False,
    help="Keep .ki/vault.yaml on disk for every vault so the next `ki index` "
         "rebuilds onto the same Vault.uri.",
)
@click.option(
    "--chunk-size", "chunk_size", type=int, default=1000,
    help="Rows per batched-remove transaction (default 1000). "
         "Lower it (e.g. 200) if you see Neo4j OOM during removal; "
         "raise it on small graphs to cut transaction overhead.",
)
def nuke_cmd(
    profile: str | None,
    dry_run: bool,
    yes_flag: bool,
    keep_marker: bool,
    chunk_size: int,
) -> None:
    sys.exit(
        cmd_nuke(
            profile=profile,
            dry_run=dry_run,
            yes=yes_flag,
            keep_marker=keep_marker,
            chunk_size=chunk_size,
        )
    )


@main.command(
    "init",
    help="(Advanced) write .ki/vault.yaml without indexing. "
         "Needs a configured Neo4j profile for the slug-collision check.",
)
@click.argument("path", type=click.Path(file_okay=False, dir_okay=True, path_type=Path))
@click.option("--profile", default=None, help="Profile name (overrides KI_PROFILE / default)")
def init_cmd(path: Path, profile: str | None) -> None:
    sys.exit(cmd_init(path, profile=profile))


@main.group("vault", help="Inspect indexed vaults.")
def vault_group() -> None:
    pass


@vault_group.command("list", help="List every indexed vault with its description.")
@click.option("--profile", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def vault_list_cmd(profile: str | None, as_json: bool) -> None:
    sys.exit(cmd_vault_list(profile=profile, as_json=as_json))


@main.group("skill", help="Install / remove the agent-skill bundle for supported AI agents.")
def skill_group() -> None:
    pass


@skill_group.command("list", help="Show supported agents and per-agent install state.")
def skill_list_cmd() -> None:
    sys.exit(cmd_skill_list())


@skill_group.command(
    "install",
    help="Install the skill into one agent, or all detected agents if omitted. "
         "Pass --path to write to an arbitrary location (escape hatch for unsupported agents).",
)
@click.argument("agent", required=False)
@click.option(
    "--path", "path",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help="Write the skill to this exact file path (overrides catalog lookup).",
)
def skill_install_cmd(agent: str | None, path: Path | None) -> None:
    sys.exit(cmd_skill_install(agent, path=path))


@skill_group.command("remove", help="Remove the installed skill from one agent, or all installed.")
@click.argument("agent", required=False)
def skill_remove_cmd(agent: str | None) -> None:
    sys.exit(cmd_skill_remove(agent))


@skill_group.command("print", help="Write the bundled SKILL.md to stdout.")
def skill_print_cmd() -> None:
    sys.exit(cmd_skill_print())


if __name__ == "__main__":
    main()
