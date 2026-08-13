#!/bin/sh
# install-env.sh - LEDE 构建环境依赖安装 (Ubuntu 22.04/24.04 自适应)
# v3: 强制官方源 + update/install 重试 + 失败诊断输出

# ---------- 0. 架构适配 ----------
ARCH=$(uname -m)
PKGS="ack antlr3 asciidoc autoconf automake autopoint binutils bison build-essential \
bzip2 ccache clang cmake cpio curl device-tree-compiler flex gawk gettext \
genisoimage git gperf haveged help2man intltool libelf-dev libfuse-dev libglib2.0-dev \
libgmp3-dev libltdl-dev libmpc-dev libmpfr-dev libncurses-dev libpython3-dev \
libreadline-dev libssl-dev libtool llvm lrzsz msmtp ninja-build p7zip p7zip-full patch pkgconf \
python3 python3-pyelftools python3-setuptools qemu-utils rsync scons squashfs-tools subversion \
swig texinfo uglifyjs upx-ucl unzip vim wget xmlto xxd zlib1g-dev"
# amd64 专属的 multilib 工具链 (arm64 无此包, LEDE rockchip 不需要)
if [ "$ARCH" = "x86_64" ]; then
  PKGS="$PKGS gcc-multilib g++-multilib libc6-dev-i386"
fi

# ---------- 1. 强制使用官方 Ubuntu 源 (runner 镜像源可能不稳定/失效) ----------
# 自适应 codename: 22.04=jammy / 24.04=noble
CODENAME=$(lsb_release -cs 2>/dev/null || grep -oP '(?<=VERSION_CODENAME=)\w+' /etc/os-release | tr -d '"')
[ -n "$CODENAME" ] || CODENAME=noble
cat > /etc/apt/sources.list.d/ubuntu-official.sources <<EOF
Types: deb
URIs: https://archive.ubuntu.com/ubuntu/
Suites: $CODENAME $CODENAME-updates $CODENAME-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

# ---------- 2. apt-get update (重试 3 次) ----------
update_ok=0
for i in 1 2 3; do
  echo "=== apt-get update (attempt $i/3) ==="
  if apt-get update -y; then
    update_ok=1
    break
  fi
  sleep $((i * 10))
done
if [ $update_ok -eq 0 ]; then
  echo "FATAL: apt-get update failed 3 times"
  df -h
  exit 1
fi

# ---------- 3. apt-get install (失败重试 + 详细诊断) ----------
echo "=== apt-get install (attempt 1/2) ==="
if ! apt-get install -y $PKGS; then
  echo "=== apt-get install failed, retrying after 10s ==="
  sleep 10
  echo "=== apt-get install (attempt 2/2) ==="
  if ! apt-get install -y $PKGS; then
    echo "=== ENV SETUP FAILED - diagnostics ==="
    echo "--- arch ---"; uname -m
    echo "--- disk ---"; df -h
    echo "--- apt sources ---"; cat /etc/apt/sources.list.d/*.sources 2>/dev/null | head -20
    echo "--- failing packages (apt-cache policy) ---"
    for p in $PKGS; do
      c=$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate/{print $2}')
      if [ -z "$c" ] || [ "$c" = "(none)" ]; then
        echo "MISSING-PACKAGE: $p"
      fi
    done
    echo "--- last apt errors ---"
    apt-get install -y $PKGS 2>&1 | tail -40
    echo "=== diagnostics end ==="
    exit 1
  fi
fi

echo "=== environment ready ==="