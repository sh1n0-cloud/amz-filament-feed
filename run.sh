#!/bin/sh
set -e
cd "$(dirname "$0")"
python3 generate.py
if git diff --quiet -- feed.xml; then
  echo unchanged
else
  git add feed.xml
  git commit -m "feed update $(date +%F)" -- feed.xml
  git push origin main
fi
