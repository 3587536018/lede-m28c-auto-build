#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR/lede" || { echo "Run prepare.sh before build.sh" >&2; exit 1; }
: > /tmp/build.log

collect_failure() {
    local status=$1
    local line=$2
    trap - ERR
    set +e
    {
        echo "=== build failed exit=$status at build.sh:$line ==="
        echo "--- errors from build.log ---"
        grep -iE 'error:|Error [0-9]|failed|No space|not found|undefined|Cannot' /tmp/build.log | tail -80
        echo "--- last 60 lines ---"
        tail -60 /tmp/build.log
        echo "--- prepare-tmpinfo ---"
        tail -30 /tmp/tmpinfo.log 2>/dev/null
        echo "--- disk ---"
        df -h
        echo "--- images ---"
        ls -la bin/targets/rockchip/armv8/ 2>/dev/null
    } > /tmp/buildfail.log
    cat /tmp/buildfail.log
    exit "$status"
}
trap 'collect_failure "$?" "$LINENO"' ERR

# feeds.conf uses complete commit IDs from sources.lock.json.
echo "Update and verify pinned feeds"
python3 "$ROOT_DIR/scripts/prepare-sources.py" --root "$ROOT_DIR" --verify-baseline
./scripts/feeds update -a
python3 "$ROOT_DIR/scripts/prepare-sources.py" --root "$ROOT_DIR" --verify-feeds
./scripts/feeds install -a
./scripts/feeds install -d y -p istore luci-app-store
./scripts/feeds install -a -p qmodem

# Passwall is not selected and brings dependencies absent from this build tree.
rm -rf package/feeds/luci/luci-app-passwall feeds/luci/applications/luci-app-passwall
# Patch the actual feed files; package/feeds/qmodem contains symlinks.
find feeds/qmodem -path '*/.git' -prune -o -name Makefile -print0 | while IFS= read -r -d '' qmodem_makefile; do
    sed -i '/kmod-mhi-wwan[[:space:]]*\\$/d; /kmod-mhi-pci-generic[[:space:]]*\\$/d; /kmod-mhi-wwan-ctrl[[:space:]]*\\$/d; /kmod-mhi-wwan-mbim[[:space:]]*\\$/d; /quectel-CM-5G[[:space:]]*\\$/d' "$qmodem_makefile"
done

# Restore the original Lua DockerMan UI without changing the locked LuCI feed.
# Remove only these two upstream package definitions before package scanning.
rm -rf package/feeds/luci/luci-app-dockerman package/feeds/luci/luci-lib-docker \
    feeds/luci/applications/luci-app-dockerman feeds/luci/collections/luci-lib-docker \
    feeds/luci/libs/luci-lib-docker

# Add every project package before OpenWrt scans package metadata.
test -f "$ROOT_DIR/package/luci-app-m28c/Makefile"
for custom_package in "$ROOT_DIR"/package/*; do
    test -d "$custom_package" || continue
    test -f "$custom_package/Makefile"
    package_name=$(basename -- "$custom_package")
    mkdir -p "package/custom/$package_name"
    cp -a "$custom_package/." "package/custom/$package_name/"
done
python3 "$ROOT_DIR/scripts/prepare-openclash-core.py" "$ROOT_DIR/lede"
python3 "$ROOT_DIR/scripts/customize-public.py" "$ROOT_DIR/lede"
python3 "$ROOT_DIR/scripts/customize-ui.py" "$ROOT_DIR/lede"
python3 "$ROOT_DIR/scripts/customize-qmodem.py" "$ROOT_DIR/lede"
# Catch scanner integration errors before starting the cross-toolchain build.
cc -fsyntax-only -std=c99 -D_GNU_SOURCE -Wall -Wextra \
    feeds/qmodem/application/modem_scan/src/modem_scand.c
cp "$ROOT_DIR/m28c.config" .config

echo "Generate defconfig"
make prepare-tmpinfo 2>&1 | tee /tmp/tmpinfo.log
make defconfig
if ! grep -q '^CONFIG_TARGET_rockchip_armv8_DEVICE_widora_mangopi-m28c=y' .config; then
    echo "ERROR: target widora_mangopi-m28c disappeared after defconfig" >&2
    head -30 tmp/.config-target.in 2>/dev/null || true
    head -20 tmp/.targetinfo 2>/dev/null || true
    collect_failure 1 "$LINENO"
fi
# Unknown/unavailable packages are silently dropped by defconfig without this check.
python3 "$ROOT_DIR/scripts/prepare-sources.py" --root "$ROOT_DIR" --verify-config .config

grep -E '^CONFIG_TARGET_(rockchip|BOARD|SUBTARGET|PROFILE)' .config
grep '^CONFIG_BINUTILS_VERSION=' .config || true

# Keep host and cross-tool feature macros consistent; apply exactly once.
python3 - <<'PYEOF'
from pathlib import Path
path = Path('toolchain/binutils/Makefile')
source = path.read_text()
if 'LARGEFILE64_SOURCE' not in source:
    anchor = 'HOST_CONFIGURE_VARS += \\'
    if source.count(anchor) != 1:
        raise SystemExit('Unexpected binutils Makefile: cannot apply large-file compatibility patch')
    source = source.replace(anchor,
        'HOST_CFLAGS += -D_LARGEFILE64_SOURCE\n'
        'TARGET_CFLAGS += -D_LARGEFILE64_SOURCE\n' + anchor, 1)
    path.write_text(source)
PYEOF

df -h
for attempt in 1 2 3; do
    if make download -j4; then
        break
    fi
    if [ "$attempt" -eq 3 ]; then
        echo "Downloads failed after 3 attempts" >&2
        collect_failure 1 "$LINENO"
    fi
    echo "Download attempt $attempt failed; retrying in 5 seconds"
    sleep 5
done

# Keep the conservative parallelism of the last successful baseline.
if make V=0 -j2 2>&1 | tee /tmp/build.log; then
    echo "Compilation completed"
else
    make_exit=$?
    echo "Compilation failed; retrying serially to capture the underlying error"
    make V=s -j1 2>&1 | tee -a /tmp/build.log || true
    collect_failure "$make_exit" "$LINENO"
fi

image_dir=bin/targets/rockchip/armv8
image_base=openwrt-rockchip-armv8-widora_mangopi-m28c
if [ ! -s "$image_dir/$image_base-ext4-sysupgrade.img.gz" ] || [ ! -s "$image_dir/$image_base-squashfs-sysupgrade.img.gz" ]; then
    echo "Missing image after make; retrying the image target"
    make V=0 -j2 image 2>&1 | tee -a /tmp/build.log
fi
for filesystem in ext4 squashfs; do
    image="$image_dir/$image_base-$filesystem-sysupgrade.img.gz"
    test -s "$image"
    python3 "$ROOT_DIR/scripts/verify-firmware.py" "$image"
    echo "Image OK: $image"
done
python3 "$ROOT_DIR/scripts/prepare-sources.py" --root "$ROOT_DIR" \
    --verify-manifest "$image_dir/$image_base.manifest"
echo "Both images and their required packages have been verified"
