#!/usr/bin/env bash
# Regenera los datos del tablero y publica en GitHub Pages (rama gh-pages).
#
#   DSN=postgresql://reps:reps@localhost:55432/reps ./deploy_pages.sh
#
# La rama gh-pages es huérfana y solo contiene index.html, datos.js y .nojekyll.
set -euo pipefail
DSN="${DSN:-postgresql://reps:reps@localhost:55432/reps}"
PY="${PY:-.venv/bin/python}"
RAIZ="$(cd "$(dirname "$0")" && pwd)"

"$PY" "$RAIZ/src/build_tablero.py" --dsn "$DSN" --salida "$RAIZ/dist"

WT="$(mktemp -d)"
git -C "$RAIZ" worktree add -q --detach "$WT"
cd "$WT"
# Rama huérfana con nombre temporal: `--orphan gh-pages` falla en la segunda
# corrida porque la rama local ya existe. Se empuja con --force a gh-pages.
TMP="pages-$(date +%s)"
git checkout -q --orphan "$TMP"
git rm -rq --cached . 2>/dev/null || true
rm -rf ./* .gitignore 2>/dev/null || true
cp "$RAIZ/tablero/index.html" index.html
cp "$RAIZ/tablero/datos.js" datos.js
touch .nojekyll
git add -A
git commit -q -m "Actualizar tablero · $(date +%Y-%m-%d)"
git push -q -f origin "HEAD:gh-pages"
cd "$RAIZ" && git worktree remove --force "$WT"
git branch -D "$TMP" 2>/dev/null || true
echo "publicado -> https://juannavia-byte.github.io/reps-ips-colombia/"
