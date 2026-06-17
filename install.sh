#!/usr/bin/env bash
#
# One-command installer for ki (knowledge-index).
# Installs both CLIs (ki + neo4j-cli) and the agent skills for driving them:
#
#   curl -sSfL https://knowledge-index.ai/install.sh | bash
#
# Idempotent — safe to re-run (it upgrades ki and refreshes skills).
set -euo pipefail

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n'  "$*"; }
warn() { printf '\033[33m!\033[0m %s\n'  "$*" >&2; }

# Freshly-installed tool shims commonly land here; make them reachable now.
export PATH="$HOME/.local/bin:$PATH"

# 1. uv — the Python tool manager that installs ki.
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv"

# 2. ki (knowledge-index) CLI.
info "Installing ki…"
uv tool install --force knowledge-index
hash -r 2>/dev/null || true
ok "ki"

# 3. neo4j-cli CLI — Aura provisioning + ad-hoc Cypher behind ki's graph queries.
if ! command -v neo4j-cli >/dev/null 2>&1; then
  info "Installing neo4j-cli…"
  curl -sSfL https://neo4j.sh/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
  hash -r 2>/dev/null || true
fi
if command -v neo4j-cli >/dev/null 2>&1; then
  ok "neo4j-cli"
else
  warn "neo4j-cli installed but not on PATH — open a new shell (or add its bin dir to PATH), then re-run this installer to finish the skill step."
fi

# 4. Skills — install into every detected agent (Claude Code, Cursor, …).
#    Non-fatal: a machine with no agent dir yet shouldn't fail the whole install.
if command -v neo4j-cli >/dev/null 2>&1; then
  info "Installing neo4j-cli Cypher skills…"
  neo4j-cli skill install --all --rw || warn "neo4j-cli skill install skipped (no supported agent detected yet)."
fi
info "Installing ki skill…"
ki skill install || warn "ki skill install skipped (no supported agent detected — run 'ki skill install <agent>' later; see 'ki skill list')."

echo
ok "ki ready."
echo "Next: run 'ki configure' to set up a Neo4j backend, then 'ki status' in any markdown folder."
