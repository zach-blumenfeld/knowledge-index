# Run Neo4j locally for `ki` (Podman)

Agent runbook. Brings up a local Neo4j container that `ki configure → Local` connects to. Idempotent — re-running any block is safe.

## Canonical values

- Container name: `neo4j-ki`
- Volume: `neo4j-ki-data` (named volume → data survives container removal)
- Image: `neo4j:latest` (Community)
- Plugins: `apoc`, `genai`
- Auth: `neo4j` / `password` (plaintext, local-only)
- Bolt: `bolt://localhost:7687` · Browser: `http://localhost:7474`
- JVM heap (max): `1G` · Page cache: `512M` — total Neo4j footprint ~2 GB. Sized as a personal-laptop citizen; the batcher's OOM auto-recovery covers occasional fat transactions on bigger vaults. See `docs/architecture.md` *Scalability envelopes* for when to bump.

If you change any of these, change them everywhere — `src/ki/neo4j_podman.py`, this doc, and the user's `~/.config/ki/config.yaml` profile must agree.

## Preflight

```bash
command -v podman
```

If that errors, install Podman first:

```bash
brew install podman
podman machine init --memory 4096 --cpus 4
podman machine start
```

`--memory 4096` (4 GB) gives the VM headroom for Neo4j's ~2 GB committed memory (1 GB heap + 512 MB page cache + JVM native overhead) plus container overhead. The Podman machine RAM is the outer constraint — Neo4j's pre-flight refuses to start if `heap + pagecache + native > container memory`. If you already initialized the machine with the default 2 GB, see *Resizing the Podman machine* below.

(Linux: use the distro package — `apt install podman` / `dnf install podman` / etc. No `machine` step needed; containers run against host RAM directly.)

Then confirm `:7687` is free:

```bash
lsof -i :7687
```

If something already answers there, find out whether it's ki's own container:

```bash
podman ps --filter name=neo4j-ki
```

- **`neo4j-ki` is running** → Local is already up; skip to *Verify*.
- **Something else holds the port** (an unrelated Neo4j, or a non-Neo4j service) → don't double-bind it and don't assume it's yours. Bring Local up on a free port instead; only use `ki configure → 3) Existing` if the user confirms it's their Neo4j and hands over the credentials.

## Bring up Neo4j

One container, one volume, detached, auto-restart:

```bash
podman run -d --name neo4j-ki --restart unless-stopped -p 7474:7474 -p 7687:7687 -v neo4j-ki-data:/data -e NEO4J_AUTH=neo4j/password -e 'NEO4J_PLUGINS=["apoc","genai"]' -e NEO4J_server_memory_heap_max__size=1G -e NEO4J_server_memory_pagecache_size=512M neo4j:latest
```

**If `:7687` (or `:7474`) is busy**, substitute a free host port in the `-p` mapping — e.g. `-p 7688:7687`. The container's *internal* ports stay `7474`/`7687`; only the host side moves. (`ki configure → Local` does this automatically and records the chosen port in the profile's `uri`.)

Setting heap + pagecache together is mandatory: Neo4j's pre-flight refuses to start if their sum (plus native overhead) exceeds container memory. `1G + 512M` keeps Neo4j around ~2 GB total — fits comfortably in a 4 GB Podman VM and leaves the user's laptop usable for everything else. Without these, the JVM auto-tunes from container memory and runs with a much smaller heap, causing mid-ingest OOMs on multi-thousand-doc vaults (see #54).

If the container already exists you'll get `container name "neo4j-ki" already in use` — that's expected. Skip to *Start an existing container* (no heap upgrade) or *Upgrade an existing container* (if the running container predates the 4G heap default).

## Start an existing container

```bash
podman start neo4j-ki
```

## Resizing the Podman machine (macOS)

The Podman VM's RAM is fixed at `init` time. To enlarge an existing machine (e.g. from the default 2 GB up to 4 GB):

```bash
podman machine stop
podman machine set --memory 4096 --cpus 4
podman machine start
```

`set` works on a stopped machine and preserves volumes/containers. (Linux: no machine layer — skip this section.)

## Wait for ready

Neo4j takes 10–30 s to accept Bolt connections after start. Poll:

```bash
until curl -sf http://localhost:7474 >/dev/null; do sleep 1; done
```

## Verify

```bash
ki configure
```

Pick `1) Local (neo4j w/ podman)` — `ki` detects the running container and writes the profile.

To verify outside `ki`:

```bash
podman exec -it neo4j-ki cypher-shell -u neo4j -p password "RETURN 1"
```

## After a reboot

macOS only — the Podman VM doesn't auto-start:

```bash
podman machine start
podman start neo4j-ki
```

Linux: `--restart unless-stopped` brings the container back automatically when the Podman service starts. No manual step.

## Recovery — graph went away

`ki` will emit a connection error pointing at this section. Diagnose with:

```bash
podman ps -a --filter name=neo4j-ki
podman volume ls --filter name=neo4j-ki-data
```

Three cases, each handled by an idempotent command:

**A. Container stopped, volume intact** (most common — reboot, OOM, manual stop). Data is fine.

```bash
podman start neo4j-ki
```

Then *Wait for ready*. No re-indexing needed.

**B. Container removed, volume intact** (`podman rm neo4j-ki` was run, volume wasn't). Data is fine — the new container picks the existing volume up by name.

Run the *Bring up Neo4j* block again. Then *Wait for ready*. No re-indexing needed.

**C. Volume gone** (`podman volume rm neo4j-ki-data` was run, or Podman was wiped). Data is lost.

Run the *Bring up Neo4j* block again. Then *Wait for ready*. Then re-index every vault the user had indexed before — `ki` does not track vault paths across volume loss, so the agent must either remember them from prior conversation or ask the user.

```bash
ki index <path-to-vault-1>
ki index <path-to-vault-2>
```

## Teardown

Stop but keep data (the default — next `podman start neo4j-ki` resumes):

```bash
podman stop neo4j-ki
```

Remove container but keep data (re-running *Bring up Neo4j* restores the index):

```bash
podman stop neo4j-ki
podman rm neo4j-ki
```

Wipe everything including the index:

```bash
podman stop neo4j-ki
podman rm neo4j-ki
podman volume rm neo4j-ki-data
```
