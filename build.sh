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

# 移除未启用的 luci-app-passwall(其依赖 ipt2socks/hysteria 等不在本构建树,
# 保留会触发 "has a dependency on ... which does not exist" WARNING, 污染日志)
rm -rf package/feeds/luci/luci-app-passwall feeds/luci/applications/luci-app-passwall 2>/dev/null
echo "removed luci-app-passwall (avoid dependency warnings)"

echo "Install qmodem feeds"
./scripts/feeds install -a -p qmodem || { echo "install qmodem feeds failed"; exit 1; }  # 去掉 -f 选项
# patch qmodem Makefile: remove dependencies that don't exist in this tree
# (kmod-mhi-wwan / kmod-mhi-pci-generic / kmod-mhi-wwan-ctrl / kmod-mhi-wwan-mbim / quectel-CM-5G)
# 关键: package/feeds/qmodem 下是 symlink(feeds install 用 ln -sf), GNU find 默认不跟随 symlink,
# 在 package/feeds/qmodem 下 find 不到 Makefile → patch 静默失效(曾导致 WARNING 残留)。
# 因此改为直接 patch feeds/qmodem 源目录(真实文件), package/feeds 下的 symlink 指向这里, 改动自动生效。
find feeds/qmodem -path '*/\.git' -prune -o -name Makefile -print0 | while IFS= read -r -d '' QMODEM_MK; do
  sed -i '/kmod-mhi-wwan[[:space:]]*\\$/d; /kmod-mhi-pci-generic[[:space:]]*\\$/d; /kmod-mhi-wwan-ctrl[[:space:]]*\\$/d; /kmod-mhi-wwan-mbim[[:space:]]*\\$/d; /quectel-CM-5G[[:space:]]*\\$/d' "$QMODEM_MK"
  echo "patched qmodem deps in $QMODEM_MK"
done
# 验证 patch 是否真正生效: 若仍有残留依赖则告警(不阻断构建, 因相关条件依赖已禁用)
LEFTOVER=$(grep -rlE 'kmod-mhi-wwan|kmod-mhi-pci-generic|kmod-mhi-wwan-ctrl|kmod-mhi-wwan-mbim' feeds/qmodem --include=Makefile 2>/dev/null | grep -v '/\.git/')
if [ -n "$LEFTOVER" ]; then
  echo "WARNING: 以下 qmodem Makefile 仍含应删除的依赖(检查 sed 模式):"
  echo "$LEFTOVER"
else
  echo "OK: 所有 qmodem Makefile 已清除 mhi 相关依赖"
fi

# 导入配置文件并检查
if [ ! -f "../m28c.config" ]; then
    echo "Error: 'm28c.config' not found in parent directory"
    exit 1
fi
cat ../m28c.config > .config || { echo "Failed to copy m28c.config to .config"; exit 1; }

echo "Generate defconfig"
# 手动先跑一次 prepare-tmpinfo 并记录完整输出, 便于诊断 22.04 上 target 扫描失败
make prepare-tmpinfo 2>&1 | tee /tmp/tmpinfo.log | tail -30
make defconfig || { echo "defconfig failed"; exit 1; }

echo "Diff between original and generated config:"
diff ../m28c.config .config || echo "Note: Config differences are normal (defconfig补充默认值)"

echo "=== Verify target device after defconfig ==="
grep -E '^CONFIG_TARGET_(rockchip|BOARD|SUBTARGET|PROFILE)' .config
if ! grep -q '^CONFIG_TARGET_rockchip_armv8_DEVICE_widora_mangopi-m28c=y' .config; then
  echo "ERROR: device widora_mangopi-m28c MISSING after defconfig!"
  echo "--- diagnostics ---"
  head -30 tmp/.config-target.in 2>/dev/null || echo "(tmp/.config-target.in missing)"
  echo "--- tmp/.targetinfo size ---"
  wc -l tmp/.targetinfo 2>/dev/null || echo "(tmp/.targetinfo missing)"
  echo "--- tmp/.targetinfo head ---"
  head -20 tmp/.targetinfo 2>/dev/null
  echo "--- FILELIST size ---"
  wc -l tmp/info/.files-targetinfo* 2>/dev/null | head -3
  echo "--- scan dump failure logs ---"
  ls logs/target/linux/*/dump.txt 2>/dev/null | head -5
  head -30 logs/target/linux/*/dump.txt 2>/dev/null | head -30
  echo "--- prepare-tmpinfo tail ---"
  tail -30 /tmp/tmpinfo.log 2>/dev/null
  exit 1
fi
echo "Device OK: widora_mangopi-m28c enabled"

# binutils 版本说明: 无 DEVEL 时 Config.version 的 "BINUTILS_VERSION_2_42 default y if !TOOLCHAINOPTS"
# 会强制 2.42(显式禁用也无效)。22.04(官方 LEDE CI 环境) 上 2.42 编译正常(Run 66 冷编译成功), 无需干预。
grep '^CONFIG_BINUTILS_VERSION=' .config || true

# ===== binutils off64_t 防御性修复 (对称宏) =====
# readelf.c 使用 off64_t/fseeko64, 编译时可能混入 musl 头(无 _LARGEFILE64_SOURCE 时不定义 off64_t)。
# 22.04 runner 镜像批次更新后 off64_t 问题回归(Run 66 成功/Run 70+72 失败, 同配置)。
# 对称给 HOST_CFLAGS(host 编译+configure) 和 TARGET_CFLAGS(cross 工具) 加宏保证一致性:
# 只加 HOST 会致 cross ar 的 configure 与编译不一致 → 打包 libgcc 栈崩溃
python3 - <<'PYEOF'
p = 'toolchain/binutils/Makefile'
try:
    s = open(p).read()
except FileNotFoundError:
    print('binutils Makefile not found, skip patch')
else:
    if 'LARGEFILE64_SOURCE' not in s:
        s = s.replace('HOST_CONFIGURE_VARS += \\',
                      'HOST_CFLAGS += -D_LARGEFILE64_SOURCE\nTARGET_CFLAGS += -D_LARGEFILE64_SOURCE\nHOST_CONFIGURE_VARS += \\', 1)
        open(p, 'w').write(s)
        print('patched binutils Makefile: HOST_CFLAGS + TARGET_CFLAGS += -D_LARGEFILE64_SOURCE')
    else:
        print('binutils already patched')
PYEOF

# ===== binutils off64_t 防御性修复 (对称宏) =====
# readelf.c 使用 off64_t/fseeko64, 编译时可能混入 musl 头(无 _LARGEFILE64_SOURCE 时不定义 off64_t)。
# Run 66 证明 22.04 cold build 正常, 但 Run 70 同环境却撞 off64_t(环境存在未明差异)。
# 对称给 HOST_CFLAGS(host 编译+configure) 和 TARGET_CFLAGS(cross 工具) 加宏, 保证一致性:
# 只加 HOST 会致 cross ar 的 configure 与编译不一致 → 打包 libgcc 栈崩溃
python3 - <<'PYEOF'
p = 'toolchain/binutils/Makefile'
try:
    s = open(p).read()
except FileNotFoundError:
    print('binutils Makefile not found, skip patch')
else:
    if 'LARGEFILE64_SOURCE' not in s:
        s = s.replace('HOST_CONFIGURE_VARS += \\',
                      'HOST_CFLAGS += -D_LARGEFILE64_SOURCE\nTARGET_CFLAGS += -D_LARGEFILE64_SOURCE\nHOST_CONFIGURE_VARS += \\', 1)
        open(p, 'w').write(s)
        print('patched binutils Makefile: HOST_CFLAGS + TARGET_CFLAGS += -D_LARGEFILE64_SOURCE')
    else:
        print('binutils already patched')
PYEOF

echo "=== Disk before make ==="
df -h

# ===== 修复 iStore 中文界面 =====
# luci-app-store 的 store.lua: vue_lang() 用 i18n.translate("istore_vue_lang") 判断前端语言,
# 但 luci-app-store 只打包了 zh-tw.lmo(没有 zh-cn), 翻译不到 → 回退 "en" → iStore 前端英文。
# 修复: 回退语言从 "en" 改为 "zh-cn"(zh-cn.json 语言文件已在 /www/luci-static/istore/i18n/)
ISTORE_CTRL=$(find feeds/istore -path '*luci-app-store*' -name store.lua 2>/dev/null | head -1)
if [ -n "$ISTORE_CTRL" ] && grep -q 'lang = "en"' "$ISTORE_CTRL"; then
  sed -i 's/lang = "en"/lang = "zh-cn"/' "$ISTORE_CTRL"
  echo "patched istore vue_lang fallback to zh-cn: $ISTORE_CTRL"
else
  echo "istore store.lua not found or already patched"
fi

# ===== iStore 中文语言包: 生成 store.zh-cn.lmo =====
# luci-app-store 没有 po/ 目录, luci 菜单标题 _("iStore") 无 .lmo 翻译, 永远显示英文。
# 1) 直接用 po2lmo(luci feed 自带源码) 编译生成 zh-cn lmo 放入固件
# 2) sed 直改菜单标题为中文(保底, 即使 lmo 格式有问题也生效)
if [ -n "$ISTORE_CTRL" ]; then
  # 菜单标题直接中文化(保底)
  sed -i 's/_("iStore"), 31)/"iStore 应用商店", 31)/' "$ISTORE_CTRL"
  echo "patched istore menu title to Chinese"
  # 生成 zh-cn.lmo(正规语言包方式)
  PO2LMO_SRC=$(find feeds/luci -path '*tools/po2lmo.c' 2>/dev/null | head -1)
  if [ -n "$PO2LMO_SRC" ]; then
    gcc -o /tmp/po2lmo "$PO2LMO_SRC" -I "$(dirname "$PO2LMO_SRC")" 2>/dev/null || gcc -o /tmp/po2lmo "$PO2LMO_SRC" 2>/dev/null || true
  fi
  if [ -x /tmp/po2lmo ]; then
    mkdir -p files/usr/lib/lua/luci/i18n
    printf 'msgid "iStore"\nmsgstr "iStore 应用商店"\n' > /tmp/store.po
    /tmp/po2lmo /tmp/store.po files/usr/lib/lua/luci/i18n/store.zh-cn.lmo 2>/dev/null \
      || /tmp/po2lmo /tmp/store.po > files/usr/lib/lua/luci/i18n/store.zh-cn.lmo 2>/dev/null \
      || true
    [ -s files/usr/lib/lua/luci/i18n/store.zh-cn.lmo ] && echo "generated istore zh-cn lmo" || echo "lmo generation failed (menu sed fallback already applied)"
  else
    echo "po2lmo compile failed (menu sed fallback already applied)"
  fi
fi

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
  echo "make failed (exit $MAKE_EXIT), re-running with V=s to capture detailed error..."
  # 增量重跑（已编译的包会跳过），用详细模式捕获真正失败原因
  make V=s -j1 2>&1 | tee -a /tmp/build.log || true
  {
    echo "=== make failed exit=$MAKE_EXIT ==="
    echo "--- errors from build.log ---"
    grep -iE 'error|Error [0-9]|failed|No space|not found|undefined|Cannot|cannot' /tmp/build.log | tail -80
    echo "--- last 40 lines ---"
    tail -40 /tmp/build.log
    echo "--- disk ---"
    df -h
    echo "--- bin/targets/rockchip/armv8 ---"
    ls -la bin/targets/rockchip/armv8/ 2>&1 | head -30
  } | tee /tmp/buildfail.log
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
