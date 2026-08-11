#!/bin/bash -x

# 检查 lede 目录是否存在
if [ ! -d "lede" ]; then
    echo "Error: 'lede' directory not found. Ensure prepare.sh ran successfully."
    exit 1
fi
cd lede || { echo "Failed to enter 'lede' directory"; exit 1; }

echo "Update feeds"
./scripts/feeds update -a || { echo "update feeds failed"; exit 1; }

echo "Install feeds"
./scripts/feeds install -a || { echo "install feeds failed"; exit 1; }

echo "Install qmodem feeds"
./scripts/feeds install -a -p qmodem || { echo "install qmodem feeds failed"; exit 1; }  # 去掉 -f 选项
# patch qmodem Makefile: remove dependencies that don't exist in this tree
# (kmod-mhi-wwan / kmod-mhi-pci-generic / kmod-mhi-wwan-ctrl / kmod-mhi-wwan-mbim / quectel-CM-5G)
QMODEM_MK=$(find package/feeds/qmodem -name Makefile | head -1)
if [ -n "$QMODEM_MK" ]; then
  sed -i '/kmod-mhi-wwan[[:space:]]*\\$/d; /kmod-mhi-pci-generic[[:space:]]*\\$/d; /kmod-mhi-wwan-ctrl[[:space:]]*\\$/d; /kmod-mhi-wwan-mbim[[:space:]]*\\$/d; /quectel-CM-5G[[:space:]]*\\$/d' "$QMODEM_MK"
  echo "patched qmodem deps in $QMODEM_MK"
  grep -cE 'kmod-mhi-wwan|quectel-CM-5G' "$QMODEM_MK" || true
fi

# 导入配置文件并检查
if [ ! -f "../m28c.config" ]; then
    echo "Error: 'm28c.config' not found in parent directory"
    exit 1
fi
cat ../m28c.config > .config || { echo "Failed to copy m28c.config to .config"; exit 1; }

echo "Generate defconfig"
make defconfig || { echo "defconfig failed"; exit 1; }

echo "Diff between original and generated config:"
diff ../m28c.config .config || echo "Note: Config differences are normal (defconfig补充默认值)"

echo "=== Verify target device after defconfig ==="
grep -E '^CONFIG_TARGET_(rockchip|BOARD|SUBTARGET|PROFILE)' .config
if ! grep -q '^CONFIG_TARGET_rockchip_armv8_DEVICE_widora_mangopi-m28c=y' .config; then
  echo "ERROR: device widora_mangopi-m28c MISSING after defconfig!"
  echo "If the device was removed/renamed in lede upstream, no image will be built."
  exit 1
fi
echo "Device OK: widora_mangopi-m28c enabled"

echo "=== Disk before make ==="
df -h

echo "Download dependencies (with retries)"
retry=3
while [ $retry -gt 0 ]; do
    make download -j4 && break  # 降低并行数，增加稳定性
    retry=$((retry - 1))
    echo "Download failed, retrying... (remaining: $retry)"
    sleep 5
done
if [ $retry -eq 0 ]; then
    echo "download failed after 3 retries"
    exit 1
fi

echo "Start compiling with verbose logs"
# -j2 保守并行: runner 内存 7GB, Go 包(openclash/adguardhome等)编译吃内存, 并发过高会 OOM
make V=0 -j2 2>&1 | tee /tmp/build.log
MAKE_EXIT=${PIPESTATUS[0]}
if [ $MAKE_EXIT -ne 0 ]; then
  echo "make failed (exit $MAKE_EXIT), extracting diagnostics..."
  {
    echo "=== make failed exit=$MAKE_EXIT ==="
    echo "--- errors from build.log ---"
    grep -iE 'error|Error [0-9]|failed|No space' /tmp/build.log | tail -60
    echo "--- last 30 lines ---"
    tail -30 /tmp/build.log
    echo "--- disk ---"
    df -h
  } > /tmp/buildfail.log
  exit $MAKE_EXIT
fi
# 镜像产物检查：make 成功但未生成 .img.gz 时，先强制触发镜像构建再判断
IMG_FILE=$(ls bin/targets/rockchip/armv8/*.img.gz 2>/dev/null | head -1)
if [ -z "$IMG_FILE" ]; then
  echo "=== WARNING: no image after make, forcing make image ==="
  make V=0 -j2 image 2>&1 | tee -a /tmp/build.log
  IMG_FILE=$(ls bin/targets/rockchip/armv8/*.img.gz 2>/dev/null | head -1)
fi

if [ -z "$IMG_FILE" ]; then
  echo "=== WARNING: make succeeded but NO image generated! collecting diagnostics ==="
  {
    echo "=== NO IMAGE GENERATED (make exit 0) ==="
    echo "--- bin/targets/rockchip/armv8/ ---"
    ls -la bin/targets/rockchip/armv8/ 2>&1
    echo "--- all img/gz files (excluding dl/staging) ---"
    find . -path ./dl -prune -o -path ./staging_dir -prune -o \( -name '*.img' -o -name '*.img.gz' -o -name '*.gz' \) -print 2>/dev/null | head -30
    echo "--- manifest/profiles exist? ---"
    find . -name '*.manifest' -o -name 'profiles.json' -o -name 'sha256sums' 2>/dev/null | head -10
    echo "--- kernel config check ---"
    grep -E '^CONFIG_TARGET_(rockchip|BOARD|SUBTARGET|PROFILE)' .config | head -6
    echo "--- disk ---"
    df -h
    echo "--- build.log last 60 lines ---"
    tail -60 /tmp/build.log
  } > /tmp/buildfail.log
  exit 1
fi
echo "Image OK: $IMG_FILE"
# make V=s -j1 || { echo "make failed"; exit 1; }  # 详细日志+单线程，便于排查错误
