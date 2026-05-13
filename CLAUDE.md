# CLAUDE.md

See `AGENTS.md` for the canonical contributor instructions — everything in there applies to Claude Code too.

## Claude-Code-specific notes

- This repo uses `uv`, not `pip`. Always run via `uv run …` so the locked environment is honored.
- When modifying the tool's external behavior (CLI flags, command names, output shape), update **all three** in the same change:
  1. `docs/requirements.md` (design spec)
  2. `skills/ki/SKILL.md` (agent-as-user routing rules)
  3. The implementation under `src/ki/`
  Drift between these is the #1 source of agent-routing bugs.
- If a user asks Claude Code to *use* `ki` to index or search their notes, route via `skills/ki/SKILL.md` (the user-facing skill spec), not via this file.
- The `neo4j-cli` skill is the natural dependency for the `ki configure → Aura` path. The `neo4j-local` npm package handles the local path. Don't reimplement either.
