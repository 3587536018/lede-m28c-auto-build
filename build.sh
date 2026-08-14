#!/bin/bash -x

# 妫€鏌?lede 鐩綍鏄惁瀛樺湪
if [ ! -d "lede" ]; then
    echo "Error: 'lede' directory not found. Ensure prepare.sh ran successfully."
    exit 1
fi
cd lede || { echo "Failed to enter 'lede' directory"; exit 1; }

echo "Update feeds"
./scripts/feeds update -a || { echo "update feeds failed"; exit 1; }

echo "Install feeds"
./scripts/feeds install -a || { echo "install feeds failed"; exit 1; }


# 绉婚櫎鏈惎鐢ㄧ殑 luci-app-passwall(鍏朵緷璧?ipt2socks/hysteria 绛変笉鍦ㄦ湰鏋勫缓鏍?
# 淇濈暀浼氳Е鍙?"has a dependency on ... which does not exist" WARNING, 姹℃煋鏃ュ織)
rm -rf package/feeds/luci/luci-app-passwall feeds/luci/applications/luci-app-passwall 2>/dev/null
echo "removed luci-app-passwall (avoid dependency warnings)"

echo "Install qmodem feeds"
./scripts/feeds install -a -p qmodem || { echo "install qmodem feeds failed"; exit 1; }  # 鍘绘帀 -f 閫夐」
# patch qmodem Makefile: remove dependencies that don't exist in this tree
# (kmod-mhi-wwan / kmod-mhi-pci-generic / kmod-mhi-wwan-ctrl / kmod-mhi-wwan-mbim / quectel-CM-5G)
# 鍏抽敭: package/feeds/qmodem 涓嬫槸 symlink(feeds install 鐢?ln -sf), GNU find 榛樿涓嶈窡闅?symlink,
# 鍦?package/feeds/qmodem 涓?find 涓嶅埌 Makefile 鈫?patch 闈欓粯澶辨晥(鏇惧鑷?WARNING 娈嬬暀)銆?# 鍥犳鏀逛负鐩存帴 patch feeds/qmodem 婧愮洰褰?鐪熷疄鏂囦欢), package/feeds 涓嬬殑 symlink 鎸囧悜杩欓噷, 鏀瑰姩鑷姩鐢熸晥銆?find feeds/qmodem -path '*/\.git' -prune -o -name Makefile -print0 | while IFS= read -r -d '' QMODEM_MK; do
  sed -i '/kmod-mhi-wwan[[:space:]]*\\$/d; /kmod-mhi-pci-generic[[:space:]]*\\$/d; /kmod-mhi-wwan-ctrl[[:space:]]*\\$/d; /kmod-mhi-wwan-mbim[[:space:]]*\\$/d; /quectel-CM-5G[[:space:]]*\\$/d' "$QMODEM_MK"
  echo "patched qmodem deps in $QMODEM_MK"
done
# 楠岃瘉 patch 鏄惁鐪熸鐢熸晥: 鑻ヤ粛鏈夋畫鐣欎緷璧栧垯鍛婅(涓嶉樆鏂瀯寤? 鍥犵浉鍏虫潯浠朵緷璧栧凡绂佺敤)
LEFTOVER=$(grep -rlE 'kmod-mhi-wwan|kmod-mhi-pci-generic|kmod-mhi-wwan-ctrl|kmod-mhi-wwan-mbim' feeds/qmodem --include=Makefile 2>/dev/null | grep -v '/\.git/')
if [ -n "$LEFTOVER" ]; then
  echo "WARNING: 浠ヤ笅 qmodem Makefile 浠嶅惈搴斿垹闄ょ殑渚濊禆(妫€鏌?sed 妯″紡):"
  echo "$LEFTOVER"
else
  echo "OK: 鎵€鏈?qmodem Makefile 宸叉竻闄?mhi 鐩稿叧渚濊禆"
fi

# 瀵煎叆閰嶇疆鏂囦欢骞舵鏌?if [ ! -f "../m28c.config" ]; then
    echo "Error: 'm28c.config' not found in parent directory"
    exit 1
fi
cat ../m28c.config > .config || { echo "Failed to copy m28c.config to .config"; exit 1; }

echo "Generate defconfig"
# 鎵嬪姩鍏堣窇涓€娆?prepare-tmpinfo 骞惰褰曞畬鏁磋緭鍑? 渚夸簬璇婃柇 22.04 涓?target 鎵弿澶辫触
make prepare-tmpinfo 2>&1 | tee /tmp/tmpinfo.log | tail -30
make defconfig || { echo "defconfig failed"; exit 1; }

echo "Diff between original and generated config:"
diff ../m28c.config .config || echo "Note: Config differences are normal (defconfig琛ュ厖榛樿鍊?"

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

# binutils 鐗堟湰璇存槑: 鏃?DEVEL 鏃?Config.version 鐨?"BINUTILS_VERSION_2_42 default y if !TOOLCHAINOPTS"
# 浼氬己鍒?2.42(鏄惧紡绂佺敤涔熸棤鏁?銆?2.04(瀹樻柟 LEDE CI 鐜) 涓?2.42 缂栬瘧姝ｅ父(Run 66 鍐风紪璇戞垚鍔?, 鏃犻渶骞查銆?grep '^CONFIG_BINUTILS_VERSION=' .config || true

# ===== binutils off64_t 闃插尽鎬т慨澶?(瀵圭О瀹? =====
# readelf.c 浣跨敤 off64_t/fseeko64, 缂栬瘧鏃跺彲鑳芥贩鍏?musl 澶?鏃?_LARGEFILE64_SOURCE 鏃朵笉瀹氫箟 off64_t)銆?# 22.04 runner 闀滃儚鎵规鏇存柊鍚?off64_t 闂鍥炲綊(Run 66 鎴愬姛/Run 70+72 澶辫触, 鍚岄厤缃?銆?# 瀵圭О缁?HOST_CFLAGS(host 缂栬瘧+configure) 鍜?TARGET_CFLAGS(cross 宸ュ叿) 鍔犲畯淇濊瘉涓€鑷存€?
# 鍙姞 HOST 浼氳嚧 cross ar 鐨?configure 涓庣紪璇戜笉涓€鑷?鈫?鎵撳寘 libgcc 鏍堝穿婧?python3 - <<'PYEOF'
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

# ===== binutils off64_t 闃插尽鎬т慨澶?(瀵圭О瀹? =====
# readelf.c 浣跨敤 off64_t/fseeko64, 缂栬瘧鏃跺彲鑳芥贩鍏?musl 澶?鏃?_LARGEFILE64_SOURCE 鏃朵笉瀹氫箟 off64_t)銆?# Run 66 璇佹槑 22.04 cold build 姝ｅ父, 浣?Run 70 鍚岀幆澧冨嵈鎾?off64_t(鐜瀛樺湪鏈槑宸紓)銆?# 瀵圭О缁?HOST_CFLAGS(host 缂栬瘧+configure) 鍜?TARGET_CFLAGS(cross 宸ュ叿) 鍔犲畯, 淇濊瘉涓€鑷存€?
# 鍙姞 HOST 浼氳嚧 cross ar 鐨?configure 涓庣紪璇戜笉涓€鑷?鈫?鎵撳寘 libgcc 鏍堝穿婧?python3 - <<'PYEOF'
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

else
fi

# 2) sed 鐩存敼鑿滃崟鏍囬涓轰腑鏂?淇濆簳, 鍗充娇 lmo 鏍煎紡鏈夐棶棰樹篃鐢熸晥)
  # 鑿滃崟鏍囬鐩存帴涓枃鍖?淇濆簳)
  # 鐢熸垚 zh-cn.lmo(姝ｈ璇█鍖呮柟寮?
  PO2LMO_SRC=$(find feeds/luci -path '*tools/po2lmo.c' 2>/dev/null | head -1)
  if [ -n "$PO2LMO_SRC" ]; then
    gcc -o /tmp/po2lmo "$PO2LMO_SRC" -I "$(dirname "$PO2LMO_SRC")" 2>/dev/null || gcc -o /tmp/po2lmo "$PO2LMO_SRC" 2>/dev/null || true
  fi
  if [ -x /tmp/po2lmo ]; then
    mkdir -p files/usr/lib/lua/luci/i18n
    /tmp/po2lmo /tmp/store.po files/usr/lib/lua/luci/i18n/store.zh-cn.lmo 2>/dev/null \
      || /tmp/po2lmo /tmp/store.po > files/usr/lib/lua/luci/i18n/store.zh-cn.lmo 2>/dev/null \
      || true
  else
    echo "po2lmo compile failed (menu sed fallback already applied)"
  fi
fi

echo "Download dependencies (with retries)"
retry=3
while [ $retry -gt 0 ]; do
    make download -j4 && break  # 闄嶄綆骞惰鏁帮紝澧炲姞绋冲畾鎬?    retry=$((retry - 1))
    echo "Download failed, retrying... (remaining: $retry)"
    sleep 5
done
if [ $retry -eq 0 ]; then
    echo "download failed after 3 retries"
    exit 1
fi

echo "Start compiling with verbose logs"
# -j2 淇濆畧骞惰: runner 鍐呭瓨 7GB, Go 鍖?openclash/adguardhome绛?缂栬瘧鍚冨唴瀛? 骞跺彂杩囬珮浼?OOM
make V=0 -j2 2>&1 | tee /tmp/build.log
MAKE_EXIT=${PIPESTATUS[0]}
if [ $MAKE_EXIT -ne 0 ]; then
  echo "make failed (exit $MAKE_EXIT), re-running with V=s to capture detailed error..."
  # 澧為噺閲嶈窇锛堝凡缂栬瘧鐨勫寘浼氳烦杩囷級锛岀敤璇︾粏妯″紡鎹曡幏鐪熸澶辫触鍘熷洜
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
# 闀滃儚浜х墿妫€鏌ワ細make 鎴愬姛浣嗘湭鐢熸垚 .img.gz 鏃讹紝鍏堝己鍒惰Е鍙戦暅鍍忔瀯寤哄啀鍒ゆ柇
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
# make V=s -j1 || { echo "make failed"; exit 1; }  # 璇︾粏鏃ュ織+鍗曠嚎绋嬶紝渚夸簬鎺掓煡閿欒
