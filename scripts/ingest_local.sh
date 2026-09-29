#!/bin/zsh
# Ingesta de las tiendas que GitHub no alcanza (gt_compare/ingest/hosts.py:
# LOCAL_STORES), escribiendo a Turso. La corre launchd; ver docs/ingesta-programada.md.
# Las credenciales se cargan de ~/.gt-compare/turso.env y nunca se imprimen.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$HOME/.gt-compare/turso.env"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ingesta local"
if [ ! -f "$ENV_FILE" ]; then
  echo "falta $ENV_FILE" >&2
  exit 1
fi
if [ "$(stat -f %Lp "$ENV_FILE")" != "600" ]; then
  echo "aviso: $ENV_FILE debería tener permisos 600" >&2
fi

set -a
source "$ENV_FILE"
set +a

# Sin estas variables la ingesta escribiría a la base local sin avisar.
if [ -z "${GT_COMPARE_DB_URL:-}" ] || [ -z "${TURSO_AUTH_TOKEN:-}" ]; then
  echo "turso.env no define GT_COMPARE_DB_URL y TURSO_AUTH_TOKEN" >&2
  exit 1
fi

cd "$REPO"
# Sin post-proceso: categorías, grupos y detector los corre Actions después.
exec ./.venv/bin/python -m gt_compare.ingest run --where local --no-post
