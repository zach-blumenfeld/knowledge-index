#!/usr/bin/env bash
#
# Build + deploy the knowledge-index.ai landing page to Cloudflare Pages.
#
# Manual direct-upload deploy (we deliberately did NOT wire Cloudflare's
# Git integration, so there is no auto-deploy-on-push). Run this whenever
# site/ or install.sh changes and you want the change live.
#
# Prereqs (one-time):
#   npx wrangler login          # authenticate to the Cloudflare account
# The Pages project "knowledge-index" and the knowledge-index.ai custom
# domain are already configured in the Cloudflare dashboard.
#
# Usage:
#   ./scripts/deploy-site.sh
#
# Assembles _site/ from the three sources of truth (site/ is the page,
# install.sh is the locked installer, img/ki.png is the logo) and uploads
# it. _site/ and .wrangler/ are build/cache output — gitignored.
set -euo pipefail

cd "$(dirname "$0")/.."

rm -rf _site && mkdir -p _site
cp install.sh _site/install.sh
cp site/* _site/
cp img/ki.png _site/ki.png

npx --yes wrangler@latest pages deploy _site \
  --project-name knowledge-index \
  --branch main
