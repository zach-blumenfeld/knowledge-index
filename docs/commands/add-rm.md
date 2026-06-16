# How `ki add` and `ki rm` work

`ki add` and `ki rm` are the **incremental write surface** — the way you keep the
index in step with one document or folder after you create, edit, rename, or
delete it, without rebuilding the whole vault. They are the per-target siblings
of the two whole-vault commands: `ki index` (build/refresh an entire vault) and
`ki drop` (remove an entire vault). For the profile/vault model they share with
every other command, see `docs/scoping.md`.

---

## 1. The one idea behind both: the index is a cache over your files

`ki` keeps a Neo4j graph that mirrors a folder of markdown. That graph is a
**disposable, rebuildable cache** — your markdown files are the source of truth,
and `ki` *reflects* what's on disk. It never owns your content and **never writes
to your files**.

Two consequences fall straight out of that, and they're the whole reason these
commands behave the way they do:

- **`ki rm` does not delete files.** It removes nodes from the index; your
  markdown stays exactly where it is. If you know git: `ki rm` is `git rm
  --cached`, *not* plain `git rm`. The file lives on; only ki's view of it goes
  away.
- **`ki add` does not move or create files.** It reads files you've already
  written and (re)indexes them. You edit on disk; `ki add` syncs the index.

So the write surface is *index-only*. You change your files with your normal
tools (editor, `mv`, `rm`, Obsidian, git); then you tell `ki` what changed so its
graph catches up.

---

## 2. `ki rm` — drop a document or folder from the index

```sh
ki rm <doc-or-folder>           # remove it (and everything under it) from the index
ki rm <doc-or-folder> --dry-run # show what would be removed; change nothing
```

`ki rm` removes one **document or folder** and its whole subtree — a folder takes
its files and subfolders with it; a document takes its sections with it. Your
files are untouched.

**What you can point it at.** A uri (the kind `ki search` / `ki outline` hand
you) or a path to the file/folder. Either resolves to the same place.

**What it refuses, and why.** `ki rm` is deliberately *file-or-above*:

| Target | Result |
|---|---|
| a **document** or **folder** | removed (with its subtree) |
| a **whole vault** | error → use `ki drop` (vaults are a different unit) |
| a single **section** | error → a section isn't a file you can delete on disk |

The section rule is the cache principle in action: a heading inside a document
isn't a thing that exists on disk on its own, so letting you delete *just* its
index node would put the index out of sync with the file (whose text still has
that section). To drop a section, edit the document and re-index it with
`ki add`.

**Inbound links.** When you `ki rm` something, links *into* it from other notes
are dropped along with it — which is correct: the target is gone, so those
references are genuinely dangling now.

**Where it runs.** Like `ki search`, `ki rm` works on the vault you're in (it
walks up from the current directory, or `-C <dir>`), or against a remote profile
by uri with `--profile <name>`.

---

## 3. `ki add` — (re)index a document or folder you've created or edited

```sh
ki add <path>            # index a new file/folder, or refresh an edited one
ki add <path> --dry-run  # list the markdown that would be (re)indexed
```

`ki add` brings the index up to date for one path: a new document, an edited
document, or a new/changed subfolder. Under the hood it clears that subtree from
the index and re-ingests just those files — so it's an **upsert**: run it on a
brand-new file to add it, or on an edited file to refresh it. Everything outside
the path you name is left alone.

**Local only, path only.** `ki add` reads files off disk, so it always operates
on the vault you're standing in (cwd, or `-C <dir>`) — there's no remote mode.
And you give it a **path**, not a uri: a uri can't be reliably turned back into
an on-disk path, and the point of `add` is to read the file there.

**What it refuses:**

| Target | Result |
|---|---|
| a markdown file / a folder of them, inside the vault | (re)indexed |
| the **whole vault root** | error → use `ki index` to rebuild a whole vault |
| a path **outside** the vault | error |
| a non-`.md` file | error → other files are captured as link stubs when a document references them, not added directly |
| a path that doesn't exist on disk | error |

**Links resolve against the whole vault.** A newly-added note that contains
`[[some-existing-note]]` links up correctly — `ki add` resolves its outbound
links against every document in the vault, not just the subtree you added.

**Still-valid inbound links survive a refresh.** This is the subtle part. Say
note `B` contains `[[A]]`, and you edit `A` and run `ki add A`. Even though
`ki add` only re-ingests `A`, the `B → A` link is **preserved** — because `B`
still says `[[A]]` on disk and `A` still exists, so dropping that link would
disagree with what's actually written. (Mechanically: links into the subtree are
snapshotted before the refresh and restored afterward, but only for targets that
still exist — so a link whose target genuinely went away is *not* resurrected.)
The result matches what a full `ki index` would produce, without the cost of one.

---

## 4. Renaming or moving a file: `rm` then `add` (there is no `ki mv`)

There's deliberately **no `ki mv`**. The reason follows from §1: `ki` can't move
your files (it doesn't own them), so a `ki mv` couldn't actually move anything —
it could only re-sync the index for a rename you'd already done yourself. To `ki`,
that "move" is just a remove plus an add, so that's the workflow:

```sh
# you rename the file with your own tools:
mv notes/draft.md notes/final.md

# then tell ki:
ki rm  notes/draft.md     # or the old uri — drop the old entry
ki add notes/final.md     # index the file at its new home
```

One thing to know about renames: other notes that linked to the old name (`[[draft]]`)
now point at something that no longer exists. `ki` will **not** silently rewrite
those links to the new name — doing so would mean inventing links you didn't
write, and `ki` only ever reflects what's actually in your markdown. So after a
rename, fix the `[[draft]]` references in your *source* notes (then `ki add` those
notes, or re-index). `ki` reports the link as broken until the source is fixed,
which is the honest state of things.

(If you'd rather not think about subtrees at all after a big reshuffle, a full
`ki index <vault>` always reconciles everything — links included — from scratch.)

---

## 5. When to use which

| You did this | Use |
|---|---|
| created or edited **one file / folder** | `ki add <path>` |
| **deleted** a file / folder | `ki rm <path-or-uri>` |
| **renamed / moved** a file / folder | `ki rm <old>` then `ki add <new>` |
| edited **lots** of files / did a big refactor | `ki index <vault>` (full rebuild) |
| want to remove an **entire vault** from the index | `ki drop <vault>` |

`ki add` / `ki rm` exist precisely so you *don't* reach for a full `ki index`
after every small change — they touch only what changed, so they stay fast as a
vault grows. Reserve the whole-vault commands for bulk edits or a clean reset.

---

## See also

- `docs/general-philosophy.md` — why the index is a cache and `ki` never writes
  your files.
- `docs/scoping.md` — profiles, vaults, and how commands resolve them (local vs
  `--profile`).
- `docs/data-model/index_rm_behavior.md` — the removal/sync model `ki drop`,
  `ki rm`, and re-index share.
- `docs/commands/search.md`, `docs/commands/get.md`, `docs/commands/outline.md` —
  the read surface these write commands keep in sync.
