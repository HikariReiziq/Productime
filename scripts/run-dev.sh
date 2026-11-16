#!/usr/bin/env bash
# Launch Productime straight from this checkout, without installing it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# -P keeps the current directory off sys.path, so a stray file there cannot shadow a module.
exec env PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m productime "$@"
