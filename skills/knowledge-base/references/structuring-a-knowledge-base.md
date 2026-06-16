# Structuring a knowledge base from scratch

Reach for this when the user is **starting a knowledge base from (near) empty**, or
has no strong opinions on how to lay it out. It's a starting-point sketch — **general
guidance, not a mandate.** Taste varies by user; if they already have
a structure or preference, follow theirs. Propose this, don't impose it.

## The one principle that matters

`ki` indexes documents, their sections, **and the wikilinks between them** — the links
are what turn a folder of files into a navigable graph. So the single highest-leverage
habit is: **write many focused notes and link them liberally.** A `[[concept]]`
reference that doesn't resolve to a file yet is fine — it marks a page worth creating.
Dense interlinking is what makes `ki search`, `ki outline`, and graph reasoning pay off;
a few giant unlinked files don't.

Bias toward **one idea per page**, human-readable filenames, and a `[[link]]` wherever
one note mentions something another note is (or should be) about.

## A light layout to propose

```
<kb-root>/
├── raw/                 # source material — the agent treats this as READ-ONLY
│   ├── inbox/           #   drop zone: user dumps files here; agent files them into raw/
│   └── ...              #   articles / papers / notes / transcripts, as the corpus needs
├── refs/                # golden reference documents — the authoritative inputs others derive from
├── <your wiki>/         # LLM-authored, interconnected pages (the heart of the KB):
│                        #   concepts, entities, insights — small, linked, one idea each
└── outputs/             # specific deliverables the agent produces (drafts, answers, reports)
```

The folder *names* don't matter; the **roles** do:

- **`raw/` — untouched source.** Whatever the knowledge is built *from*: pasted articles,
  papers, notes, transcripts. The agent reads these but **does not edit them** — they're
  the record. Keep large binaries (videos, datasets, big PDFs) *out* of the tree; drop a
  small pointer file describing where they live instead (symlinks break under cloud sync
  and confuse indexing).
- **`inbox/` — the drop zone.** The user dumps files here (any format). On the next pass
  the agent converts non-markdown to `.md` (`pandoc` or `markitdown` on PATH), files the
  results into `raw/`, empties the inbox, and re-indexes (`ki add`/`ki index`).
- **Golden references — the load-bearing inputs.** Whatever is *authoritative* for this
  KB (a spec, a positioning doc, a source of truth) and gets cited repeatedly. Worth
  calling out so derivative pages can link back to it. A `refs/` folder is one way; a
  convention/marker is another.
- **The wiki — LLM-authored, interconnected.** The pages the agent writes and grows:
  concepts, entities (people/tools/orgs/papers), insights/themes. **This is where the
  heavy linking lives.** Small pages, one idea each, cross-linked with `[[wikilinks]]`.
- **`outputs/` — deliverables.** Concrete artifacts produced *for* the user (a draft, an
  answer, a report), as opposed to the durable knowledge in the wiki.

## What you do NOT need

- **No `index.md` / master catalog / map-of-content.** `ki outline` generates the
  table of contents on demand from the graph — don't spend effort hand-maintaining a
  catalog that goes stale. (`ki outline <vault uri>` *is* your index — depth-capped, drill in as needed.)
- **De-prioritize "connect everything" hub notes and key logs.** A giant
  manually-curated MOC or a running activity log is high-maintenance and low-value here:
  the graph already captures how things connect through the links you wrote. Spend that
  effort on linking the real pages instead.

## Once it's laid out

Index it and use it like any vault — get the directory to `READY` (`ki index . --profile <p>`),
then `ki outline` / `ki search` / `ki get`. As the user drops new material in `inbox/`
or you author new pages, sync per-target with `ki add` (see the *Adding, Updating &
Removing Content* section of the skill).
