#!/usr/bin/env bash
# Builds a .deb package wrapping dist/bitchat-tui (the PyInstaller single-file
# binary). Run scripts/build_binary.sh first so dist/bitchat-tui is up to date.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${BITCHAT_TUI_VERSION:-0.1.0}"
ARCH="amd64"
PKG_NAME="bitchat-tui"
STAGING="$PROJECT_ROOT/packaging/deb/${PKG_NAME}_${VERSION}_${ARCH}"

BINARY="$PROJECT_ROOT/dist/bitchat-tui"
if [ ! -x "$BINARY" ]; then
    echo "error: $BINARY not found or not executable — run scripts/build_binary.sh first" >&2
    exit 1
fi

echo "==> Staging package tree at $STAGING"
rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN" "$STAGING/usr/bin" "$STAGING/usr/share/doc/${PKG_NAME}"

install -m 0755 "$BINARY" "$STAGING/usr/bin/${PKG_NAME}"
install -m 0644 "$PROJECT_ROOT/README.md" "$STAGING/usr/share/doc/${PKG_NAME}/README.md"

INSTALLED_SIZE_KB="$(du -sk "$STAGING/usr" | cut -f1)"

cat > "$STAGING/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE_KB}
Recommends: tor
Maintainer: Aaditya Bhatia <aaditya12120205@gmail.com>
Homepage: https://github.com/aadi121205/BITTOR-TUI
Description: Terminal client for bitchat's public geohash channels over Tor
 bitchat-tui is a terminal client for bitchat's public location channels
 (geohash-based chat rooms), connecting over Tor to the same Nostr relays
 real bitchat apps use. Includes real end-to-end encrypted peer-to-peer DMs
 (NIP-17/NIP-59, NIP-44 v2).
EOF

OUT_DEB="$PROJECT_ROOT/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
echo "==> Building $OUT_DEB"
dpkg-deb --root-owner-group --build "$STAGING" "$OUT_DEB"
echo "==> Done: $OUT_DEB"
