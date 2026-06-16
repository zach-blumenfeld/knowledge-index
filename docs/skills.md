# Agent skills

`ki` ships an **agent skill** — a `SKILL.md` bundle that tells AI agents when and
how to use `ki`. `ki skill install` drops it into each detected agent's config
(see `ki skill --help`).

## Current: `knowledge-base`

The shipped skill. Covers **mode 1** of [`scoping.md`](scoping.md) §3.2 — the
**local, single-vault** workflow: set up a vault, keep it in sync, and search /
navigate / retrieve / answer over it. This is the 95% case and the only mode with
agent-skill coverage today. (Bundle: `skills/knowledge-base/`.)

## Planned: `knowledge-base-reader`

A second skill for **modes 2 & 3** — read-only access to **remote** vaults and
**cross-vault** search, where you don't have the source locally. Deferred
post-release; tracked in
[#66](https://github.com/zach-blumenfeld/knowledge-index/issues/66). The two skills
will share some `references/` (Neo4j connection + troubleshooting).

## Why two skills

Mode 1 is *personal knowledge management* — build and use **your own** notes. Modes
2–3 are *reading / serving* a KB you don't own (production, multi-user, read-only):
a different audience and mental model. Splitting keeps each skill focused instead of
one bundle trying to be both. See [`scoping.md`](scoping.md) §3.2 for the mode
definitions.
