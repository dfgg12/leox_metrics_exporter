#!/bin/sh
# Build luci-app-leoxgpon .ipk without an OpenWrt buildroot.
# Usage: scripts/build-ipk.sh [output-dir]
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PKG_DIR="$REPO_DIR/luci-app-leoxgpon"
OUT_DIR="${1:-$REPO_DIR/bin}"

PKG_NAME=luci-app-leoxgpon
PKG_VERSION=$(sed -n 's/^PKG_VERSION:=//p' "$PKG_DIR/Makefile")
PKG_RELEASE=$(sed -n 's/^PKG_RELEASE:=//p' "$PKG_DIR/Makefile")

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# --- data ---
DATA="$WORK/data"
mkdir -p \
    "$DATA/usr/lib/leoxgpon" \
    "$DATA/usr/bin" \
    "$DATA/etc/init.d" \
    "$DATA/etc/config" \
    "$DATA/usr/lib/lua/luci/controller" \
    "$DATA/usr/lib/lua/luci/view/leoxgpon"

cp "$REPO_DIR/scraper.py"                       "$DATA/usr/lib/leoxgpon/"
cp "$PKG_DIR/root/usr/bin/leoxgpon"             "$DATA/usr/bin/"
cp "$PKG_DIR/root/etc/init.d/leoxgpon"          "$DATA/etc/init.d/"
cp "$PKG_DIR/root/etc/config/leoxgpon"          "$DATA/etc/config/"
cp "$PKG_DIR/luasrc/controller/leoxgpon.lua"    "$DATA/usr/lib/lua/luci/controller/"
cp "$PKG_DIR/luasrc/view/leoxgpon/status.htm"   "$DATA/usr/lib/lua/luci/view/leoxgpon/"

chmod 755 "$DATA/usr/bin/leoxgpon" "$DATA/etc/init.d/leoxgpon"
chmod 644 "$DATA/etc/config/leoxgpon"

INSTALLED_SIZE=$(du -sb "$DATA" | cut -f1)

# --- control ---
CTRL="$WORK/control"
mkdir -p "$CTRL"

cat > "$CTRL/control" <<EOF
Package: $PKG_NAME
Version: $PKG_VERSION-$PKG_RELEASE
Depends: luci-base, python3, python3-sqlite3, curl
Architecture: all
Installed-Size: $INSTALLED_SIZE
Section: luci
Priority: optional
Maintainer: Damian K <damian@niom.pl>
Description: LuCI interface and scraper service for LeoX GPON ONT metrics
EOF

cat > "$CTRL/conffiles" <<EOF
/etc/config/leoxgpon
EOF

cat > "$CTRL/postinst" <<'EOF'
#!/bin/sh
[ -n "${IPKG_INSTROOT:-}" ] && exit 0
/etc/init.d/leoxgpon enable
/etc/init.d/leoxgpon restart
rm -rf /tmp/luci-indexcache /tmp/luci-modulecache
exit 0
EOF

cat > "$CTRL/prerm" <<'EOF'
#!/bin/sh
[ -n "${IPKG_INSTROOT:-}" ] && exit 0
/etc/init.d/leoxgpon stop || true
/etc/init.d/leoxgpon disable || true
exit 0
EOF

chmod 755 "$CTRL/postinst" "$CTRL/prerm"

# --- assemble ipk (tar.gz of debian-binary + control.tar.gz + data.tar.gz) ---
echo "2.0" > "$WORK/debian-binary"
tar -C "$CTRL" -czf "$WORK/control.tar.gz" --owner=0 --group=0 .
tar -C "$DATA" -czf "$WORK/data.tar.gz" --owner=0 --group=0 .

mkdir -p "$OUT_DIR"
IPK="$OUT_DIR/${PKG_NAME}_${PKG_VERSION}-${PKG_RELEASE}_all.ipk"
tar -C "$WORK" -czf "$IPK" --owner=0 --group=0 \
    ./debian-binary ./control.tar.gz ./data.tar.gz

echo "built: $IPK"
