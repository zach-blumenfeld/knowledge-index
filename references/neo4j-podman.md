# Run Neo4j locally for `ki` (Podman)

Agent runbook. Brings up a local Neo4j container that `ki configure → Local` connects to. Idempotent — re-running any block is safe.

## Canonical values

- Container name: `neo4j-ki`
- Volume: `neo4j-ki-data` (named volume → data survives container removal)
- Image: `neo4j:latest` (Community)
- Plugins: `apoc`, `genai`
- Auth: `neo4j` / `password` (plaintext, local-only)
- Bolt: `bolt://localhost:7687` · Browser: `http://localhost:7474`

If you change any of these, change them everywhere — `src/ki/neo4j_podman.py`, this doc, and the user's `~/.config/ki/config.yaml` profile must agree.

## Preflight

```bash
command -v podman
```

If that errors, install Podman first:

```bash
brew install podman
podman machine init
podman machine start
```

(Linux: use the distro package — `apt install podman` / `dnf install podman` / etc. No `machine` step needed.)

Then confirm `:7687` is free:

```bash
lsof -i :7687
```

If something already answers there, it's either an existing `neo4j-ki` container (skip to *Verify*) or a different Neo4j (use `ki configure → 3) Existing` to point at it instead — don't double-bind the port).

## Bring up Neo4j

One container, one volume, detached, auto-restart:

```bash
podman run -d --name neo4j-ki --restart unless-stopped -p 7474:7474 -p 7687:7687 -v neo4j-ki-data:/data -e NEO4J_AUTH=neo4j/password -e 'NEO4J_PLUGINS=["apoc","genai"]' neo4j:latest
```

If the container already exists you'll get `container name "neo4j-ki" already in use` — that's expected. Skip to *Start an existing container*.

## Start an existing container

```bash
podman start neo4j-ki
```

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
