#!/usr/bin/env bash
# Commit regenerated radars, but only when the SVG actually changed.
#
# Nightly runs usually produce byte-identical files once a repo stops changing,
# and an empty commit on a public profile is noise.
set -euo pipefail

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git diff --quiet -- assets/radar-dark.svg assets/radar-light.svg \
              assets/radar-langs-dark.svg assets/radar-langs-light.svg; then
  echo "radares sin cambios, nada que commitear"
  exit 0
fi

git add assets/radar-dark.svg assets/radar-light.svg \
        assets/radar-langs-dark.svg assets/radar-langs-light.svg
git commit -m "charts: radares regenerados"
git push
