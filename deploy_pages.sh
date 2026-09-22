#!/usr/bin/env bash
# Publica en GitHub Pages la página que dice dónde vive Ariad. NADA MÁS.
#
#   ./deploy_pages.sh
#
# ─────────────────────────────────────────────────────────────────────────────
# ESTE SCRIPT PUBLICABA EL TABLERO ENTERO, Y ESE ERA EL PROBLEMA
#
# Hasta el 2026-09-22 copiaba `tablero/datos.js` tal cual a la rama gh-pages.
# Ese archivo lleva dentro el bloque `contactos`: 20.398 personas con nombre,
# cargo, correo y teléfono. El repositorio es público y Pages no pide sesión,
# así que estuvo servido en abierto en
#
#     https://juannavia-byte.github.io/reps-ips-colombia/datos.js
#
# 6,8 MB de datos personales, a un GET de distancia y sin autenticación.
#
# No era un descuido del despliegue sino del diseño: el mismo HTML se usaba en
# dos sitios con requisitos opuestos —uno detrás de login y otro público— y se
# desplegaba con los mismos datos en ambos.
#
# Ariad vive en /portal/ariad, en proactivos-website, que valida la sesión de
# Supabase en cada petición y sirve con `Cache-Control: private, no-store`.
# Esa es la única vía. Lo que se publica acá es un letrero que lleva allá.
#
# Si algún día hace falta un tablero público de verdad, no se resuelve
# recortando este script: se resuelve con un payload construido aparte y sin
# el bloque `contactos`, y la comprobación de que no lo trae tiene que ser
# parte del despliegue, no de la memoria de quien lo corre.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")" && pwd)"

WT="$(mktemp -d)"
git -C "$RAIZ" worktree add -q --detach "$WT"
cd "$WT"
TMP="pages-$(date +%s)"
git checkout -q --orphan "$TMP"
git rm -rq --cached . 2>/dev/null || true
rm -rf ./* .gitignore 2>/dev/null || true

# `datos.js` se publica vacío y a propósito. El CDN de Pages siguió sirviendo
# la versión de 6,8 MB después de borrar la rama —el archivo estaba cacheado y
# borrar no purga—, y la única forma de sacarlo de circulación fue
# sobrescribirlo. Si se dejara de publicar, volvería a aparecer el viejo.
printf 'window.DATOS={"generado":"","cols":[],"rows":[],"contactos":{},"pesos_icp":{}};\n' > datos.js

cat > index.html <<'HTML'
<!doctype html><meta charset="utf-8"><title>Ariad</title>
<meta name="robots" content="noindex,nofollow">
<style>body{font:16px/1.6 system-ui;max-width:34rem;margin:15vh auto;padding:0 1.5rem;color:#121C18}
a{color:#1F6F5C}h1{font-size:1.4rem;margin:0 0 .75rem}p{color:#57685F}</style>
<h1>Ariad se movió</h1>
<p>La herramienta ahora vive en el portal de Proactivos, detrás de inicio de sesión.</p>
<p><a href="https://proactivos.com.co/portal/ariad">Entrar a Ariad</a></p>
HTML

touch .nojekyll
git add -A
git commit -q -m "Publicar el letrero de Ariad · $(date +%Y-%m-%d)"
git push -q -f origin "HEAD:gh-pages"
cd "$RAIZ" && git worktree remove --force "$WT"
git branch -D "$TMP" 2>/dev/null || true
echo "· publicado el letrero (sin datos) en gh-pages"
