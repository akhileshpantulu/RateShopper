#!/usr/bin/env bash
# Runs each time the codespace starts: take the latest code from main and refresh the install.
# Never fails the start: if the update cannot apply (no network, local edits), the current version keeps running.
cd "$(dirname "$0")/.." || exit 0
if git pull --ff-only --quiet origin main; then
  echo "Rate Shopper code is up to date with main."
else
  echo "Could not update from main; starting the current version."
fi
pip install --no-cache-dir --quiet -e . || echo "Could not refresh the install; starting the current version."
exit 0
