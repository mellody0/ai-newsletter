#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Daily pipeline: generate digest → publish to Google Docs
# Scheduled via launchd LaunchAgent at 9:00 AM ET
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load secrets from .env
set -a
# shellcheck source=.env
source "$SCRIPT_DIR/.env"
set +a

PYTHON="$SCRIPT_DIR/.venv/bin/python3"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

log "════════════════════════════════════════════════════"
log "  Pipeline start"
log "════════════════════════════════════════════════════"

cd "$SCRIPT_DIR"

# ── Step 1: Generate digest ───────────────────────────────────────────────────
log "Step 1: Generating digest..."
"$PYTHON" digest.py 2>&1

# ── Step 2: Publish to Google Docs ───────────────────────────────────────────
log "Step 2: Publishing to Google Docs..."
"$PYTHON" publish_google.py 2>&1

log "════════════════════════════════════════════════════"
log "  Pipeline complete"
log "════════════════════════════════════════════════════"

# ── Step 3: Push to GitHub (triggers Render redeploy) ────────────────────────
log "Step 3: Pushing to GitHub..."
git add outputs/
git diff --cached --quiet || git commit -m "digest: $(date '+%Y-%m-%d')"
git push
log "GitHub push complete."
