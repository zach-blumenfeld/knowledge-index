"""Click entry point — wires up `ki <command>`.

User-visible commands:
  ki configure              one-time Neo4j connection setup
  ki index <path>           sync a folder of markdown into the graph
  ki search <query>         retrieve via fulltext + graph (B.1 / B.2 / B.3 / B.11)
  ki rm <path>              remove a doc / subtree / vault from the index
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
from .commands.index import cmd_index
from .commands.init import cmd_init
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
    help="If no config exists, auto-pick Local Neo4j without prompting.",
)
def index_cmd(
    path: Path,
    profile: str | None,
    batch_size: int,
    max_file_size: int,
    concurrency: int,
    yes_flag: bool,
) -> None:
    sys.exit(
        cmd_index(
            path,
            profile=profile,
            batch_size=batch_size,
            max_file_size=max_file_size,
            concurrency=concurrency,
            yes=yes_flag,
        )
    )


@main.command("search", help="Search the index. Default --type section (B.2).")
@click.argument("query")
@click.option("--profile", default=None)
@click.option(
    "--type", "search_type",
    type=click.Choice(["section", "document", "neighbors", "vault"]),
    default="section",
    help="section=B.2, document=B.1, neighbors=B.3, vault=B.11",
)
@click.option("--k", "k", type=int, default=10, help="result limit / depth")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--doc-uri", default=None, help="(--type neighbors) start document URI")
def search_cmd(
    query: str,
    profile: str | None,
    search_type: str,
    k: int,
    as_json: bool,
    doc_uri: str | None,
) -> None:
    sys.exit(
        cmd_search(
            query,
            profile=profile,
            search_type=search_type,
            k=k,
            as_json=as_json,
            doc_uri=doc_uri,
        )
    )


@main.command("rm", help="Remove a document / subtree / vault from the index.")
@click.argument("target")
@click.option("--profile", default=None)
@click.option(
    "--vault", "vault_flag", is_flag=True, default=False,
    help="Remove an entire vault. Requires typed display-name confirmation.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Report only; no Neo4j writes.")
@click.option("--yes", "yes_flag", is_flag=True, default=False, help="Skip prompts.")
@click.option(
    "--keep-marker", is_flag=True, default=False,
    help="(--vault) keep .ki/vault.yaml so the next `ki index` rebuilds the same Vault.uri.",
)
def rm_cmd(
    target: str,
    profile: str | None,
    vault_flag: bool,
    dry_run: bool,
    yes_flag: bool,
    keep_marker: bool,
) -> None:
    sys.exit(
        cmd_rm(
            target,
            profile=profile,
            vault_flag=vault_flag,
            dry_run=dry_run,
            yes=yes_flag,
            keep_marker=keep_marker,
        )
    )


@main.command("init", help="(Advanced) write .ki/vault.yaml without indexing.")
@click.argument("path", type=click.Path(file_okay=False, dir_okay=True, path_type=Path))
def init_cmd(path: Path) -> None:
    sys.exit(cmd_init(path))


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
