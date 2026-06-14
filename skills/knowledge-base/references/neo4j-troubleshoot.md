# Neo4j unreachable — troubleshoot (`NEO4J_DOWN` / `NEO4J_UNRESPONSIVE`)

Agent runbook. `ki status` reported the bound profile's Neo4j as **down** (connection refused — nothing listening) or **unresponsive** (the connection hangs / times out). Fix the backend, then re-run `ki status` until it reads `READY`. Wrong-credentials is a *different* state — `AUTH_ERROR` → [configure-profile.md](configure-profile.md).

## 1. Identify the profile's backend

```sh
ki profile list        # each profile + its source + status
```

The active profile's `source` decides the fix.

## 2a. `local-podman` — a container issue

Most failures are the Neo4j container being stopped (reboot, OOM, manual stop). Diagnose and recover via [neo4j-podman.md](neo4j-podman.md):

- **Down** → usually `podman start neo4j-ki` (*Recovery — case A*). On macOS after a reboot, run `podman machine start` first (*After a reboot*).
- **Container or volume gone** → follow *Recovery* cases B / C (re-create the container; case C loses data → re-index afterward).
- **Unresponsive** → the container is up but Neo4j isn't accepting Bolt yet; **wait** (*Wait for ready*). If it stays wedged, `podman restart neo4j-ki`.

## 2b. `aura` — managed cloud instance

- Aura instances **pause on inactivity** (especially the free tier) and take time to resume — an **unresponsive** result often just means it's resuming; wait and retry `ki status`.
- **Down / unreachable** → check the instance in the Aura console (or `neo4j-cli aura ...`). If it was deleted or its URI changed, re-point the profile with `ki configure` ([configure-profile.md](configure-profile.md)).

## 2c. `existing` — a Neo4j you pointed at

- The instance `ki` was configured against isn't answering on its Bolt URI. Start it, or confirm it's listening on the profile's host:port; check network / firewall / a changed URI.
- If the URI or credentials changed, re-run `ki configure`.

## 3. Re-check

```sh
ki status              # repeat until READY
```
