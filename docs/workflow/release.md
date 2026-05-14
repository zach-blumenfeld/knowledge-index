
# Release prompt template

Reusable handoff prompt for unattended feature work — drop into a fresh
Claude Code session inside `/Users/zachblumenfeld/demo/knowledge-index`
(or any clone of this repo) after you've written a requirements doc for
the feature.

The first version of this pattern shipped with
[`v0_3_0_semantic_search/requirements.md`](../v0_3_0_semantic_search/requirements.md);
the workflow generalized once we wrote a second one
([`v0_3_1_introspect_dedup/discussion.md`](../v0_3_1_introspect_dedup/discussion.md)).

## Workflow

1. Write the **requirements doc** for the feature at
   `docs/<version>_<name>/requirements.md`. See the *Requirements doc
   contract* below for what it must contain.
2. Verify the **pre-flight checks** (clean `main`, `gh` authed, Neo4j
   reachable if integration tests should run).
3. Confirm the **harness settings** — `.claude/settings.local.json` has
   `defaultMode: "acceptEdits"` and the deny list that blocks
   `main`-targeted pushes, merges, force-pushes, hook-skipping, etc.
   (See *Harness setup* below if it's missing or you want to refresh.)
4. Open a fresh Claude Code session in the repo and paste the **prompt
   below**, filling the two `<...>` placeholders.
5. Walk away. Come back to a PR you can review and merge yourself.

## The prompt

```
Implement the <VERSION> release of knowledge-index per
<REQUIREMENTS_DOC_PATH>.

Read that file end-to-end before starting — it has the scope,
file manifest, normalization rules (if any), version-bump steps,
CHANGELOG format, branch name, and acceptance criteria. Follow
its cross-references (AGENTS.md and the docs/ specs it points at)
as needed.

Work the acceptance-criteria checklist top to bottom. When every
item is green, open the PR against main with `gh pr create`.

Hard rules: do NOT merge the PR, do NOT push to main, do NOT
force-push, do NOT skip commit hooks. Stop after PR creation.

If you hit an item in the doc's "Open questions to surface"
section, or a scope ambiguity not covered, stop and ask before
deciding.
```

Fill the placeholders:

| Placeholder                 | Example                                          |
|-----------------------------|--------------------------------------------------|
| `<VERSION>`                 | `v0.3.0` / `v0.3.1` / `v0.4.0`                   |
| `<REQUIREMENTS_DOC_PATH>`   | `docs/v0_3_0_semantic_search/requirements.md`    |

Don't pad the prompt with content the doc already covers. Drift between
the prompt and the doc is how features ship the wrong thing.

## Requirements doc contract

For the prompt to be sufficient, the `docs/<version>_<name>/requirements.md`
must be **self-contained** for an agent who hasn't seen this conversation.
The v0.3.0 doc is the reference shape. At minimum the doc needs:

- [ ] **Status and context** — link to any discussion doc, link to the
      [`AGENTS.md`](../../AGENTS.md) non-negotiable principles, link to
      [`requirements_v01_mvp.md`](../requirements_v01_mvp.md), and the
      ordered list of files the agent must read before starting.
- [ ] **Scope — what to build.** Each deliverable spelled out with:
      problem statement, solution sketch, schema changes (if any),
      parser/ingest/search changes, normalization rules (if any), and
      tests (unit + integration).
- [ ] **Out of scope.** An explicit deferred list (cribbed from the
      relevant discussion doc's *Direction → deferred* section). The
      doc must say "if you discover you need one of these to ship,
      stop and flag — don't expand scope."
- [ ] **File manifest** — table of expected-touched files with a
      one-line change description per row. Plus any stale-reference
      cleanup chore that piggybacks on the release.
- [ ] **Release prep.** Concrete `pyproject.toml` and
      `src/ki/__init__.py` version-bump steps. CHANGELOG format
      (load-bearing per the comment block at the top of
      [`CHANGELOG.md`](../../CHANGELOG.md)). Branch name
      (`feat/<version>-<name>`). `gh pr create` invocation pattern.
- [ ] **Hard constraints** — no merge, no push to main, no
      force-push, no `--no-verify`, no scope expansion to deferred
      items. (Mirrors the prompt's hard rules; both must say the same
      thing.)
- [ ] **Acceptance criteria** — a checkbox list the agent walks top
      to bottom: tests pass, ruff clean, version sources in sync,
      CHANGELOG entry present, schema docs updated, SKILL.md updated
      if relevant, PR open against `main` (NOT merged).
- [ ] **Open questions to surface.** Items the agent must escalate
      rather than decide unilaterally. The prompt's "stop and ask"
      clause anchors here.

If any of these sections is missing or vague, the prompt won't recover
for you — the agent will either pad with assumptions or stall. **Write
the doc fully before pasting the prompt.**

## Pre-flight checks

Before pasting the prompt, verify:

- `git status` clean, on `main`, latest pulled. The agent will branch
  off `main`; an unclean tree contaminates the branch.
- `git branch -a | grep feat/<version>` returns nothing — the branch
  name from the requirements doc isn't already used on the remote.
- `gh auth status` shows logged in. Otherwise `gh pr create` fails near
  the end of the run.
- `uv --version` works. (You have it.)
- The Neo4j your default `ki` profile points at is reachable — only
  matters if the feature's integration tests should actually run.
  Integration tests auto-skip when no Neo4j is reachable, per
  [`AGENTS.md`](../../AGENTS.md) *Project map*.

## Harness setup (`.claude/settings.local.json`)

The harness file already exists in this repo with the right shape — see
[`.claude/settings.local.json`](../../.claude/settings.local.json).
Key bits the prompt above relies on:

- `"defaultMode": "acceptEdits"` — auto-approves Edit / Write so the
  agent doesn't pause on every file change.
- A permissive **allow** list covering `uv *`, read-only `git ...`, the
  safe `git checkout -b *` / `git push origin feat/*` writes, `gh pr
  create | view | checks`, `ki *`, and standard exploration
  (`grep`, `rg`, `ls`, `find`, `cat`, `head`, `tail`, `wc`).
- A **deny** list that backstops the prompt's hard rules:
  any `git push *main*`, `git push --force*` / `-f`, `git push *--no-verify*`,
  `git checkout main`, `gh pr merge*`, `git reset --hard*`, `git rebase*`,
  `git commit --amend*`, `git commit --no-verify*`.

If you want to refresh or extend the harness for a particular feature
(say, adding allow for a new tool the feature needs), the cleanest path
is to invoke the `update-config` skill in a session and describe the
delta — it knows the exact `.claude/settings.json` schema and won't
drift if the format evolves.

## Optional: when to *not* use this template

Skip the requirements-doc + prompt-template flow for:

- **Single-file tweaks.** A typo fix, a one-liner config change, a
  README edit — describe in chat, edit, commit. No release.
- **Investigations / spikes.** When you don't yet know the scope, write
  a `docs/discussion-*.md` first (like
  [`discussion-vector-indexing.md`](../discussion-vector-indexing.md)) to
  pin down the *what* before you spec the *how*.
- **Hotfixes with a deadline.** A spec doc + handoff is a multi-hour
  loop. For a same-day fix, work it directly.

Use the template when: the feature is ≥ half a day of work, touches
multiple files, needs schema/CHANGELOG/version changes, or you want
the agent to drive the release-prep mechanics end-to-end.
