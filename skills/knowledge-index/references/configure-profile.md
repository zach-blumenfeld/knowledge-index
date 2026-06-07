# Configure a Neo4j profile (`ki configure`)

Agent runbook. Reached when there are **no profiles yet**, when credentials are wrong (`AUTH_ERROR`), or when the user wants a new backend. A *profile* is a named Neo4j connection stored in `~/.config/ki/config.yaml`; `ki configure` creates one and, if it's the first, sets it as the default.

## Pick a backend

`ki configure` offers three. Choose by where the graph should live:

| Backend | Use when                                                                  | Notes |
|---|---------------------------------------------------------------------------|---|
| **1) Local (Podman)** | solo / on-this-laptop work                                                | self-managed `neo4j:latest` container. Bring-up + recovery: [neo4j-podman.md](neo4j-podman.md). |
| **3) Existing** | User already has a Neo4j running (another container, a service, env vars) | point `ki` at its Bolt URI + credentials. |
| **2) Aura** | sharing one index across machines / a team                                | **billable cloud** — wraps `neo4j-cli aura create`; requires `neo4j-cli`. |

## Auto-mode (choosing for the user)

The choice is driven by whether a profile already exists — **not** by what happens to be listening on a port.

- **A profile already exists** in `config.yaml` → use it; you're not configuring.
- **No profile → default to Local (Podman)** (auto-fire only if `podman` is on PATH):
  - `:7687` free → bring Local up there (see neo4j-podman.md *Preflight*).
  - `:7687` busy → it's some other service you have no credentials for (an unrelated Neo4j, or not Neo4j at all) — **don't adopt it**; bring Local up on the next free port.
  - `podman` missing → surface the install step; don't guess.
- **Existing (`3`) is never inferred** from an open port — use it only when the user names a Neo4j they run and gives its Bolt URI + credentials.
- **Aura is never silent** — pick `2) Aura` only if the user explicitly asked for cloud / Aura. "Build me a knowledge base" is consent for the goal, not for a billable resource.

## Run it

```sh
ki configure                 # interactive: pick a backend, write the profile
ki configure --yes           # non-interactive: auto-pick Local without prompting
```

`ki configure` probes the backend, writes the profile into `config.yaml`, and makes it the default if it's the first one.

## Which database

A Neo4j instance can hold several databases. `ki` connects to the instance's **home database** unless a profile names a specific one — and crucially it does **not** assume `neo4j`:

- **Local** → pinned to `neo4j` (ki created the container, so it's known).
- **Existing / Aura** → leave the `Database` prompt **blank** to use the server's home database. That's correct for standard Neo4j *and* Aura, whose home db is the instance DBID — forcing `neo4j` would fail on Aura Free. Only enter a name to target a *specific* non-home database on a multi-db server.

(Stored as `database:` on the profile when set; absent means "home database.")

## Wrong credentials (`AUTH_ERROR`)

The database is up; the profile's stored credentials are wrong. **Re-run `ki configure`** for that profile to re-enter them — do **not** restart Neo4j. (For Local/Podman the canonical creds are `neo4j` / `password`; see neo4j-podman.md.)

## After configuring

Re-run `ki status` to confirm the vault reaches `READY`.
