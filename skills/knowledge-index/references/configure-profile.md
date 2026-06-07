# Configure a Neo4j profile (`ki configure`)

Agent runbook. Reached when there are **no profiles yet**, when credentials are wrong (`AUTH_ERROR`), or when the user wants a new backend. A *profile* is a named Neo4j connection stored in `~/.config/ki/config.yaml`; `ki configure` creates one and, if it's the first, sets it as the default.

## Pick a backend

`ki configure` offers three. Choose by where the graph should live:

| Backend | Use when | Notes |
|---|---|---|
| **1) Local (Podman)** | solo / on-this-laptop work | self-managed `neo4j:latest` container. Bring-up + recovery: [neo4j-podman.md](neo4j-podman.md). |
| **3) Existing** | a Neo4j is already running (another container, a service, env vars) | point `ki` at its Bolt URI + credentials; don't double-bind `:7687`. |
| **2) Aura** | sharing one index across machines / a team | **billable cloud** — wraps `neo4j-cli aura create`; requires `neo4j-cli`. |

## Auto-mode (when you're choosing for the user)

- **Existing reachable Neo4j** (a profile already in `config.yaml`, or something already answering on `:7687`) → use `3) Existing`; report what you connected to.
- **Otherwise Local (Podman)** → `1) Local`. Reversible and local, so auto-fire **if** `podman` is on PATH and `:7687` is free (see neo4j-podman.md *Preflight*). If `podman` is missing, surface the install step; don't guess.
- **Aura is never silent.** Only pick `2) Aura` if the user explicitly asked for cloud / Aura. "Build me a knowledge base" is consent for the goal, not for creating a billable resource.

## Run it

```sh
ki configure                 # interactive: pick a backend, write the profile
ki configure --yes           # non-interactive: auto-pick Local without prompting
```

`ki configure` probes the backend, writes the profile into `config.yaml`, and makes it the default if it's the first one.

## Wrong credentials (`AUTH_ERROR`)

The database is up; the profile's stored credentials are wrong. **Re-run `ki configure`** for that profile to re-enter them — do **not** restart Neo4j. (For Local/Podman the canonical creds are `neo4j` / `password`; see neo4j-podman.md.)

## After configuring

Re-run `ki status` to confirm the vault reaches `READY`.
