# Requirements doc prompt template

Reusable prompt for drafting a new
`docs/<version>_<name>/requirements.md` from a feature description.
**Stage 2 of 3** in the pipeline:

- Stage 1 = [`discuss.md`](discuss.md) — produces the deliberation doc that's the **required input** to this stage.
- **Stage 2 (this file)** — consumes that deliberation, produces the implementation spec.
- Stage 3 = [`release.md`](release.md) — consumes this spec, ships the release autonomously.

The discussion doc is **not optional**. The spec's *Out of scope*,
*Direction*, and rejected-options reasoning come straight from it; if
there's no discussion doc, the spec doesn't have a defensible source
of authority and the agent will end up inventing scope. If you don't
have one yet, run `/discuss` first.

> **Status: doc for now; candidate skill.** This is in `docs/` so it's
> easy to iterate. Once the prompt stabilizes and we've used it a few
> times, promote it to `skills/spec-feature/` (or similar) so it's
> invokable as a slash command. Until then, copy-paste from this file.

## Workflow

1. **Confirm the discussion doc exists.** Path is either
   `docs/discussion-<topic>.md` (broad) or
   `docs/<version>_<name>/discussion.md` (scoped). If there isn't one
   yet, **stop and run `/discuss` first** —
   [`discuss.md`](discuss.md).
   This is required, not optional.
2. **Pick what you're spec-ing.** Pick a version (`v0.3.1`,
   `v0.4.0`, …) and a short kebab-case name (`introspect-dedup`,
   `vector-search`, …). Usually inherited from the discussion doc's
   scope. Together they form the target directory:
   `docs/<version>_<short_name>/` (e.g., `docs/v0_3_1_introspect_dedup/`).
3. **Fill the placeholder section** in the prompt below, including the
   path to the discussion doc.
4. **Paste into a fresh Claude Code session** in the repo. The agent
   reads the discussion doc and references, asks clarifying questions,
   drafts the doc, iterates with you until it's release-prep ready.
5. **Sign off.** Once the doc satisfies the *Requirements doc contract*
   in [`release.md`](release.md), hand it off using that template's prompt.

## The prompt

Copy the block below, fill in the four `<...>` user-input fields at
the top, paste into a fresh session.

```
Help me write a requirements doc for the next knowledge-index
release. Final output: a self-contained spec at the target path
below that another agent can implement end-to-end.

==========================================================
USER INPUTS (fill these in)
==========================================================

Feature short name (kebab-case): <NAME>
Target version: <VERSION>            # e.g., v0.3.1
Target file:    docs/<version_no_dots>_<name_underscored>/requirements.md
                                     # e.g., docs/v0_3_1_introspect_dedup/requirements.md

REQUIRED — Discussion doc path (path must exist; this is the
spec's source of authority for scope, direction, and rejected
options. If none exists, stop and run /discuss first):
<DISCUSSION_DOC_PATH>

What we're building (1–3 paragraphs — focus on the WHAT and
the WHY, not the HOW; the agent figures out the HOW with you.
You can keep this short if the discussion doc already covers
the description — point at the relevant section instead):
<FEATURE DESCRIPTION>

Additional source material beyond the discussion doc (related
PRs, prior decisions, external links; leave empty if none):
<LINKS>

Things I already know are OUT OF SCOPE for this release
(deferrals beyond what the discussion doc already lists; leave
empty to inherit the discussion's deferred list as-is):
<EXPLICIT DEFERRALS>

Constraints I want enforced (e.g., "no new deps", "must work
on Aura Free", "no schema migration"; leave empty to inherit
from the discussion doc's constraints):
<CONSTRAINTS>

==========================================================
AGENT INSTRUCTIONS (do not edit)
==========================================================

1. Read these references before drafting anything:

   - **<DISCUSSION_DOC_PATH>** — REQUIRED, read in full. This
     is the spec's source of authority. The scope, Direction,
     rejected options, open questions, and constraints in
     your output should trace back to this doc. If the user
     left it blank or the file doesn't exist, STOP and tell
     them to run /discuss first — do not draft a spec
     without it.
   - docs/workflow/release.md — specifically the
     "Requirements doc contract" section. The output must
     satisfy every checkbox there.
   - AGENTS.md — non-negotiable design principles, project
     map, conventions, "Don't" list. The output cannot violate
     these.
   - docs/v0_3_0_semantic_search/requirements.md — the
     reference shape. Match its section structure exactly.
   - docs/requirements_v01_mvp.md — v0.1 base spec; still
     load-bearing for everything not changed by this release.
   - Any additional source material the user linked above.
   - If the feature touches schema / ingest / search:
     docs/data-model.md, docs/ingest-cypher.md,
     docs/retrieval-queries.md.

2. Ask the user clarifying questions BEFORE drafting:

   - Implementation choices the user input didn't pin down
     (where in src/ the code lives, which existing helpers
     to reuse, etc.).
   - Schema decisions (new properties, new node/edge labels,
     index changes).
   - Test coverage scope (unit only? integration too? new
     fixtures needed?).
   - Anything where two reasonable interpretations of the
     user input exist.

   Ask in one batch if possible (use AskUserQuestion when
   available). Don't ask trivia the user obviously delegated
   — pick reasonable defaults and note them. Wait for answers
   before writing the file.

3. Draft the requirements doc at the target path.

   - Match the section structure of v0.3.0's requirements.md:
     Status & context · Scope (what to build) · Out of scope ·
     File manifest · Stale-reference cleanup · Release prep
     (version, CHANGELOG, branch, PR) · Hard constraints ·
     Acceptance criteria · Open questions to surface.
   - **Status & context** must link to the discussion doc as
     the FIRST reference, named as the source of authority
     for the scope decisions.
   - **Scope (what to build)** = the chosen Direction from
     the discussion doc, made concrete. Don't re-litigate
     options — that's what the discussion doc is for. If the
     discussion doc has a "Direction" section, the spec's
     scope is that direction; if it has "Tentative lean,"
     ask the user to confirm before treating it as final.
   - **Out of scope** = the discussion doc's deferred list +
     any additional deferrals the user added. Carry both in
     verbatim. Add a "stop and flag" instruction telling the
     implementing agent not to silently expand into them.
   - **Open questions to surface** = the discussion doc's
     open questions filtered to the ones that need answers
     at implementation time (some are revisit-later; those
     stay in the discussion doc, not the spec).
   - Be concrete: name actual file paths, actual
     function/class names you expect the implementing agent
     to touch, actual test files, actual schema additions.
     Vague specs produce vague implementations.
   - Cite cross-references with relative links
     (`[../data-model.md](../data-model.md)`). Don't duplicate
     content from cross-referenced docs — link to it.
   - Hard rules section must say: no merge, no push to main,
     no force-push, no --no-verify. Mirrors the
     release-prompt-template's prompt-side rules.
   - Acceptance criteria must be a real checkbox list —
     concrete enough that the implementing agent can walk
     top-to-bottom and self-verify.

4. After the file exists, summarize back to me:

   - Deliverables (1-line each).
   - Files the implementing agent will touch.
   - Open questions you flagged inside the doc that you'd
     escalate at implementation time.
   - Anything you punted on or assumed; I'll correct.

5. Iterate. I'll send feedback; you update the file. We're
   done when the Requirements doc contract checklist
   (docs/workflow/release.md) is fully satisfied.

Hard rules for this drafting task:

- Don't commit, don't push, don't open a PR. This produces a
  spec doc, nothing else.
- Don't touch implementation files (src/, tests/, pyproject.toml,
  CHANGELOG.md, src/ki/__init__.py). Only write the
  requirements doc at the target path. The implementing agent
  (a future session) does those.
- Don't expand scope beyond the user's description. If you
  think the feature needs something the user didn't mention,
  flag it as an open question or push back — don't silently
  add it to scope.
- If the feature is too vague to spec (no clear deliverables,
  no acceptance criteria possible), say so and recommend
  writing a discussion doc first — don't paper over ambiguity
  with structure.
```

## When this template fits vs. doesn't

**Fits:**
- Feature where the *what* is clear but the *how* needs to be
  pinned down before implementation.
- Features that need cross-file changes, schema additions,
  CHANGELOG entries, version bumps.
- Any release-shaped work where you'll hand off implementation
  to a separate session.

**Doesn't fit:**
- **No discussion doc yet.** This template requires one — run
  [`discuss.md`](discuss.md) (`/discuss`) first. The spec
  inherits scope, direction, and deferrals from the discussion;
  without that source of authority, the spec ends up with invented
  scope.
- Pre-decision work more broadly — same answer: write the
  discussion doc first.
- Single-file tweaks (typo, README polish, config change). Just
  edit and commit.
- Hotfixes. If you're firefighting, work directly.

## Promotion path

When this prompt stabilizes (we've used it for 3+ features without
major rewording), consider moving it to `skills/spec-feature/SKILL.md`
so it's invokable as `/spec-feature` and the user inputs become
arguments rather than a placeholder block. The structure already
maps cleanly: the placeholder section becomes the user-prompt
arguments; the agent-instructions block becomes the skill body.

Leaving as a doc for now because:
- Skills are harder to iterate than docs.
- The skill catalog already has the right peer (`update-config`,
  `fewer-permission-prompts`, the in-repo `ki` skill) — adding
  another should wait until we're confident this *is* the right
  shape.
- The "paste this block" UX is genuinely simple for an
  experimental tool.

Cross-link from this file once promoted.
