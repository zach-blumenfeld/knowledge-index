# Discussion prompt template

Reusable prompt for the **first stage** of the feature pipeline:
turning an idea / observation / problem statement into a structured
deliberation doc the team can react to. Pairs with
[`spec.md`](spec.md) (stage 2) and [`release.md`](release.md)
(stage 3).

> **Status: doc for now; candidate skill.** Same promotion plan as its
> siblings — once the trio has been used for 2-3 features and the
> wording stabilizes, fold all three into skills as a single batch.

## Where this fits in the pipeline

```
   idea / observation / bug
            │
            ▼
   ┌──────────────────┐
   │  /discuss        │  ← this template
   │  (human ⇄ agent) │  ← iterative, opinion-having
   └──────────────────┘
            │
            ▼   produces: docs/discussion-<topic>.md
                       or  docs/<version>_<name>/discussion.md
            │
            ▼
   ┌──────────────────┐
   │  /spec           │  ← spec.md
   │  (human ⇄ agent) │  ← consumes discussion doc, produces spec
   └──────────────────┘
            │
            ▼   produces: docs/<version>_<name>/requirements.md
            │
            ▼
   ┌──────────────────┐
   │  /release        │  ← release.md
   │  (agent solo,    │  ← autonomous; consumes requirements,
   │   human reviews) │     produces PR
   └──────────────────┘
            │
            ▼   produces: shipped feature, bug fix, etc.
```

The discussion stage is the **lightest** of the three — fewest
deliverables, most exploration, most opinion-having. The agent's job
isn't to transcribe your thinking; it's to surface options you didn't
mention, push back on framing that's too narrow or too broad, and help
you reach a tentative lean you can defend.

## Workflow

1. **Have a thing to think about.** A problem, an observation, a
   feature idea, a "should we…", a "what about X." It does not need
   to be well-formed — that's what the discussion is *for*.
2. **Pick where the doc will live.**
   - Broad / cross-feature: `docs/discussion-<topic>.md`
     (e.g., `docs/discussion-vector-indexing.md`).
   - Clearly scoped to one upcoming version:
     `docs/<version_no_dots>_<name_underscored>/discussion.md`
     (e.g., `docs/v0_3_1_introspect_dedup/discussion.md`).
3. **Paste the prompt below** into a fresh Claude Code session, filling
   the placeholder block at the top.
4. **Iterate.** The discussion doc is conversational — expect 3-10
   exchanges. The agent drafts, you push back; the agent surfaces a
   complication you hadn't considered; you adjust the lean; the doc
   updates. Done when you have a *Direction* or *Tentative lean*
   section you'd defend in a PR review.
5. **Hand off to `/spec`.** Once the discussion lands, paste the
   requirements-prompt-template prompt to generate the implementation
   spec from this doc.

## The prompt

```
Help me think through and draft a discussion doc for the
knowledge-index project. Final output: a working deliberation
doc at the target path that someone else (or future-me) can
react to and that the requirements-doc stage can consume.

==========================================================
USER INPUTS (fill these in)
==========================================================

Topic / short name (kebab-case): <TOPIC>
Target file: <PATH>
                # Either:
                #   docs/discussion-<topic>.md  (broad / cross-feature)
                # Or:
                #   docs/<version_no_dots>_<name>/discussion.md
                #     (scoped to a specific upcoming version)

The thing I want to think through (problem, observation,
question, feature idea — does not need to be well-formed):
<DESCRIPTION>

What triggered this — what did I just see / hit / realize that
makes this worth deliberating now? (1-3 sentences; helps anchor
the doc in concrete context, not abstract speculation):
<TRIGGER>

Constraints or non-negotiables (e.g., "must not break v0.x",
"no new credentials", "stays opt-in"; leave empty if none):
<CONSTRAINTS>

Options I'm already considering (1 per line, can be vague;
leave empty if you want me to enumerate from scratch):
<OPTIONS>

==========================================================
AGENT INSTRUCTIONS (do not edit)
==========================================================

1. Read these references before drafting anything:

   - AGENTS.md — non-negotiable principles. Options that
     violate these are out — reject them, don't engineer
     workarounds.
   - docs/requirements_v01_mvp.md — v0.1 base spec.
   - docs/discussion-vector-indexing.md — the gold-standard
     reference shape for a deliberation doc (multi-option
     deliberation, honest pros/cons, tentative-lean
     section, open questions).
   - docs/v0_3_1_introspect_dedup/discussion.md — a second
     reference, smaller scope, two co-located items.
   - docs/data-model.md, docs/ingest-cypher.md,
     docs/retrieval-queries.md — only if the topic touches
     schema, ingest, or search.

2. Your posture for this task — different from the spec
   and release stages:

   - Be opinion-having. The user wants you to push back,
     not transcribe. If their framing is too narrow, say
     so. If they're about to lock in something that has a
     real complication, surface it.
   - Surface options the user did NOT list. Often the most
     valuable contribution is "here's a third path you
     didn't mention." Don't pad with bad options for the
     sake of having more bullets.
   - Enumerate honest pros AND cons for every option. A
     pro-only or con-only option list is a failure mode —
     it signals you're either selling or dismissing
     instead of weighing.
   - When the user proposes something with hidden costs,
     name the costs concretely (specific files affected,
     specific behavioral changes, specific failure modes).
     "Has complications" is not enough; "Section.uri
     parsing breaks because X" is.
   - Have a tentative lean with reasoning. Not "it
     depends" — pick the option you'd defend and explain
     why, while acknowledging what could change your mind.

3. Draft the discussion doc at the target path.

   Match the section structure used in existing discussion
   docs (vector-indexing and introspect-dedup):

   - **Status banner** — "deliberation, not spec." Future
     readers must not confuse this with a build contract.
   - **What triggered this** — concrete context. The
     observation, bug, or question that made this worth
     writing down now. Quote real numbers / paths / file
     references when available.
   - **Constraints any option must satisfy** — pulled from
     AGENTS.md non-negotiables + the user's stated
     constraints. Options that violate these are out.
   - **The options** — one subsection per option. For each:
     Shape (how it would work, concretely) · Pros · Cons ·
     Verdict (or "tentative" if the doc isn't decided yet).
   - **Cross-cutting concerns** — issues that affect
     multiple options (e.g., "X is load-bearing under
     options 1 and 2 but irrelevant under option 3").
     Optional — include when relevant.
   - **Tentative lean / Direction** — your recommendation
     with reasoning. Note what would change the answer.
     Mirror the "Direction" structure from
     discussion-vector-indexing.md if the doc is
     decision-shaped, or keep "Tentative lean" if it's
     still exploratory.
   - **Open questions** — items that need answers before
     this becomes a spec. Distinguish "blocks the
     direction" from "spec-time decision."

4. Iterate with the user.

   - Ask 1-3 clarifying questions BEFORE drafting if the
     user's input is ambiguous in load-bearing ways. Use
     AskUserQuestion when available. Don't ask trivia —
     pick reasonable defaults and note them inline.
   - After the first draft: summarize what you wrote, what
     options you added beyond the user's list, and any
     spots where you took a position the user might want
     to push back on.
   - On every subsequent message: update the doc, don't
     just argue in chat. The doc is the artifact; the chat
     is the working memory.
   - Don't be sycophantic. If the user lands on something
     you think is wrong, say so once with reasoning, then
     defer if they confirm. Don't keep re-litigating.

5. Done when:

   - There's a defensible Direction / Tentative lean.
   - Rejected options are listed with the reason for
     rejection — not omitted.
   - Open questions are honestly enumerated.
   - The user confirms the doc is ready to hand off to
     /spec (or that they want to keep it as exploration
     for now and not spec anything yet).

Hard rules for this drafting task:

- Don't commit, don't push, don't open a PR. This produces
  a thinking doc, nothing else.
- Don't touch implementation files (src/, tests/, etc.) or
  release-relevant files (pyproject.toml, CHANGELOG.md,
  src/ki/__init__.py). Discussion is not implementation.
- Don't draft requirements doc content. If the discussion
  resolves cleanly toward a spec, say "this looks ready
  for /spec" — don't pre-emptively write requirements
  sections inside the discussion doc.
- If the topic is too small to deliberate (one obvious
  answer, no real tradeoff), say so and recommend
  skipping straight to /spec or just editing directly.
  Don't manufacture deliberation for its own sake.
```

## When this template fits vs. doesn't

**Fits:**
- Open questions with multiple plausible answers.
- Schema or architectural decisions where the cost of
  picking wrong is meaningful.
- Cross-cutting concerns that affect multiple future features.
- "We just hit X — what should we do about it?" observations.
- Pre-spec exploration where you don't yet know what to spec.

**Doesn't fit:**
- Implementation choices with one obvious answer. Skip to
  `/spec` (requirements doc) directly, or just do it.
- Bugs with a known fix. Edit, commit, move on.
- Features you've already decided how to build. The
  deliberation is done; you need a spec, not a discussion.
- Anything you'd be embarrassed to share half-finished —
  discussion docs are intentionally rough; if you want
  polished prose, you're in the wrong stage.

## The pipeline, summarized

| Stage      | Template                   | Mode               | Output                                           | Human role     |
|------------|----------------------------|--------------------|--------------------------------------------------|----------------|
| 1. Discuss | [`discuss.md`](discuss.md) | Human ⇄ agent sync | `docs/discussion-*.md` or scoped `discussion.md` | Drive thinking |
| 2. Spec    | [`spec.md`](spec.md)       | Human ⇄ agent sync | `docs/<version>_<name>/requirements.md`          | Sign off       |
| 3. Release | [`release.md`](release.md) | Agent solo (async) | PR open against `main`, ready for review         | Review + merge |

The split is load-bearing: stages 1 and 2 use your judgment, stage 3
shouldn't need it. If you find yourself making decisions during stage 3,
that's a signal the requirements doc was incomplete — fix the spec, not
the agent.

## Promotion path

Same as the sibling templates. Promote all three to skills
(`/discuss`, `/spec`, `/release`) as a single follow-up release once
the trio has been used end-to-end for 2-3 features. The mapping is
mechanical: placeholder block → skill args; agent-instructions block →
skill body; "fits / doesn't fit" → skill description's trigger/skip
clauses.

Cross-link from this file once promoted.
