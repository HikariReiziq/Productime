#!/usr/bin/env bash
# Build release artifacts into dist/: a source tarball, an RPM when rpmbuild is
# installed, and SHA256SUMS for everything produced.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '%s\n' "$*"; }

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
NAME="productime-$VERSION"
DIST="$ROOT/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf -- "$STAGE"' EXIT

mkdir -p "$STAGE/$NAME"
cp -R src tests scripts packaging Assets pyproject.toml README.md LICENSE "$STAGE/$NAME/"
find "$STAGE/$NAME" -type d \( -name '__pycache__' -o -name '.ruff_cache' \) -prune -exec rm -rf -- {} +

rm -rf -- "$DIST"
mkdir -p "$DIST"
# Fixed ordering and ownership keep the tarball identical across rebuilds of the same tree.
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SOURCE_DATE_EPOCH:-$(date +%s)}" \
    -czf "$DIST/$NAME.tar.gz" -C "$STAGE" "$NAME"
log "Built dist/$NAME.tar.gz"

if command -v rpmbuild >/dev/null 2>&1; then
    TOPDIR="$STAGE/rpmbuild"
    mkdir -p "$TOPDIR/SOURCES" "$TOPDIR/SPECS"
    cp "$DIST/$NAME.tar.gz" "$TOPDIR/SOURCES/"
    cp packaging/rpm/productime.spec "$TOPDIR/SPECS/"
    rpmbuild --define "_topdir $TOPDIR" --define "version_override $VERSION" -bb "$TOPDIR/SPECS/productime.spec"
    find "$TOPDIR/RPMS" -name '*.rpm' -exec cp {} "$DIST/" \;
    log "Built the RPM into dist/"
else
    log "rpmbuild not found: skipped the .rpm (install it with: sudo dnf install rpm-build)"
fi

(cd "$DIST" && sha256sum -- * >SHA256SUMS)
log "Wrote dist/SHA256SUMS"
ls -l "$DIST"
