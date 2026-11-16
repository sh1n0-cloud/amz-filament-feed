#!/bin/sh
# Generate the filament sale feed and publish it if it changed.
set -e
cd "$(dirname "$0")"
python3 generate.py
if [ -n "$(git status --porcelain -- feed.xml)" ]; then
  git add feed.xml
  git commit -m "feed: update $(date +%Y-%m-%dT%H:%M)" >/dev/null
  git push >/dev/null
  echo pushed
else
  echo unchanged
fi
