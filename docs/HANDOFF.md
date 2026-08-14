# LEDE M28C 固件项目 —— 交接与使用文档

> 最后更新：2026-08-14
> 适用对象：接手本项目的其他 agent / 开发者

---

## 1. 项目概况

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/3587536018/lede-m28c-auto-build` |
| 本地路径 | `C:\Users\wuming\Desktop\md\lede-m28c-auto-build` |
| 用途 | 为 MangoPi M28C（RK3528 / armv8 / aarch64）自动编译定制 LEDE 固件（GitHub Actions） |
| 源码 | `coolsnowwolf/lede`（master，内核 6.12，luci openwrt-25.12，gcc 13.3.0，musl） |
| 设备 | M28C，TF 卡 58GB，LAN `192.168.1.1`，SSH `root` / 密码 `wuming` |
| 成功产物 | release `v0.260814.054429`（Run 66，ext4+squashfs，但为旧功能集） |

---

## 2. 分支结构

```
main (fa5eec2)   = 成功配置 16be226 + 1G 镜像 + 宏 patch + 强制 binutils 2.40
                   → Run 74 验证中（2.40 的 ar 打包 libgcc 是否通过）
backup-main-fullfeature (0e8a94a)
                 = 全部功能修改完整版：dockerman、1G、/opt 扩容、
                   iStore 全套修复、OpenSSH 默认化
backup-before-reset
                 = 早期 2.40+宏+22.04 折腾史（可忽略）
```

---

## 3. 构建系统（GitHub Actions）

### 3.1 最终有效配置

- `runs-on: ubuntu-22.04`（与官方 LEDE CI 一致）
- 磁盘清理：官方 apt purge 风格（**禁用 easimon**，会破坏 runner 环境）
- swap：完全容错脚本（22.04 镜像自带 immutable `/swapfile`）
- cache key：**必须含 `-22.04-` 标识**（staging_dir 与 glibc 版本强绑定）
- 发布：softprops/action-gh-release（API 创建 tag，避免 workflow 文件保护问题）

### 3.2 两大编译障碍与解法

| 障碍 | 根因 | 解法 |
|---|---|---|
| readelf.c `off64_t` 未定义 | binutils 编译混入 musl 头；**runner 镜像批次会变**（同配置 Run 66 成功 / 70 失败） | build.sh 内 Python 幂等 patch：`HOST_CFLAGS += -D_LARGEFILE64_SOURCE` + `TARGET_CFLAGS += ...`（必须对称，否则 ar configure/编译不一致） |
| aarch64 cross ar 打包 libgcc.a `stack smashing` | binutils 2.42/2.43.1 的 aarch64 ar 缺陷；**官方 CI 只构建 x86 从未暴露** | 强制 binutils **2.40**（见 3.3） |

### 3.3 强制 binutils 2.40 的正确写法

无 DEVEL 时 binutils choice 不可见，`BINUTILS_VERSION_2_42` 有
`default y if !TOOLCHAINOPTS`，defconfig 会强制 2.42。
`# CONFIG_BINUTILS_VERSION_2_42 is not set` 对此**无效**。

`m28c.config` 必须同时包含：

```
CONFIG_DEVEL=y
CONFIG_TOOLCHAINOPTS=y
CONFIG_BINUTILS_USE_VERSION_2_40=y
# CONFIG_BINUTILS_VERSION_2_42 is not set
# CONFIG_BINUTILS_VERSION_2_43 is not set
CONFIG_BINUTILS_VERSION="2.40"
```

`build.sh` 在 defconfig 后验证版本，不匹配立即失败（1 分钟，不浪费 30 分钟）。

### 3.4 环境安装

`install-env.sh`：apt 源自适应 codename（jammy/noble）、full-upgrade、
清第三方源、官方包列表（含 `libncurses5-dev libncursesw5-dev`、llvm 等）。

### 3.5 build.sh 内嵌 patch 清单

1. qmodem：删除不存在依赖（kmod-mhi-wwan* / quectel-CM-5G），
   直接 patch `feeds/qmodem` 源目录（package/feeds 下是 symlink）
2. iStore：vue_lang 回退 zh-cn；菜单中文化；生成 zh-cn.lmo
3. binutils：对称宏 patch（见 3.2）
4. defconfig 后验证：设备存在 + binutils 2.40
5. 失败时自动推送诊断到 `debug-env` 分支（debug-build.log）

---

## 4. 功能定制清单

| # | 功能 | 状态 | 细节 |
|---|---|---|---|
| 1 | 全中文界面 | ✅ | LUCI 强制 zh_cn + 各插件中文包 |
| 2 | QModem 新版界面 + 短信 + 监控 | ✅ | luci-app-qmodem-next / qmodem-monitor / sms-forwarder-next |
| 3 | 科学上网 | ✅ | OpenClash + SSR-Plus（Xray/MosDNS 等全套） |
| 4 | Docker | ✅ | dockerd + docker-compose + luci-app-docker（顶层菜单） |
| 5 | dockerman + 中文 | ✅(backup) | luci-app-dockerman + luci-i18n-dockerman-zh-cn |
| 6 | iStore | ✅ | 见第 5 节 |
| 7 | 镜像 1G | ✅(main) | `CONFIG_TARGET_ROOTFS_PARTSIZE=1024` |
| 8 | 存储扩容 | ✅(backup) | rootfs 1G 不扩 + p3 吃满剩余挂 `/opt` |
| 9 | OpenSSH 默认 | ✅(backup) | 移除 dropbear，sshd 接管 22 端口 |
| 10 | 网络服务 | ✅ | AdGuard、ddns-go、TurboACC(BBR+FlowOffload)、UPnP、samba4、rclone、vlmcsd、WOL、nlbwmon、ttyd、arpbind |
| 11 | 系统管理 | ✅ | Argon 主题、diskman、cpufreq、cpulimit、autoreboot、watchcat、filetransfer、mtr/iperf3/nload |

---

## 5. iStore 修复（4 项，均在 backup 分支）

| 问题 | 根因 | 修复 |
|---|---|---|
| 前端英文 | 无 zh-cn.lmo → 回退 en | store.lua `lang = "en"` → `"zh-cn"` |
| 菜单 "iStore" 英文 | 无 luci .po | 菜单改 "iStore 应用商店" + po2lmo 生成 `files/usr/lib/lua/luci/i18n/store.zh-cn.lmo` |
| 应用装不了（Unknown package app-meta-xxx） | 缺 meta feed | `files/etc/opkg/istore-meta.conf`：`src/gz is_meta https://istore.istoreos.com/repo/all/meta`（134 个 meta 包） |
| compat 兼容 feed | 正常镜像有，下载失败无碍 | 暂未加（避免覆盖系统包） |

设备临时修复（不等新固件）：
```sh
echo "src/gz is_meta https://istore.istoreos.com/repo/all/meta" >> /etc/opkg/istore-meta.conf
```

---

## 6. 存储扩容方案（99-expand-data）

```
磁盘布局:
┌─────────┬──────────────────────────────┐
│ rootfs  │  p3 数据分区 (挂载 /opt)     │
│ 1G      │  剩余全部 ~57G               │
└─────────┴──────────────────────────────┘
```

- 首次启动：创建 p3（1.2GiB 起）→ mkfs.ext4 → fstab UUID 挂 `/opt`
- docker data_root = `/opt/docker`；swap = `/opt/swapfile`（512M）
- 老固件升级：/opt 已有内容先迁移到 p3 再挂载
- 根分区为何不扩：LEDE ext4 镜像 resize2fs 上限约 6G（reserve_backup_gdb），
  squashfs 只读——所以用数据分区方案绕开

---

## 7. 构建与发布流程

1. `git push` 到 main → 自动触发 build（paths-ignore：README/docs）
2. build 成功 → release job 用 softprops 发布（tag 格式 `v0.YYMMDD.HHMMSS`）
3. 产物：`openwrt-rockchip-armv8-widora_mangopi-m28c-{ext4,squashfs}-sysupgrade.img.gz`
4. 下载地址：仓库 Releases 页

刷机：TF 卡写 img（balenaEtcher / dd），插卡上电，等首次启动脚本执行完成
（扩容格式化约 1-2 分钟），登录 `192.168.1.1`（root / wuming）。

---

## 8. 运维速查

```powershell
# 查构建状态（匿名 API）
python -c "import urllib.request,json; d=json.load(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/3587536018/lede-m28c-auto-build/actions/runs?per_page=5',headers={'User-Agent':'Mozilla/5.0'}))); [print(r['run_number'],r['conclusion'],r['created_at'][11:19],r['head_sha'][:8]) for r in d['workflow_runs']]"

# 诊断日志（build.sh 自动推送 debug-env 分支）
# https://raw.githubusercontent.com/3587536018/lede-m28c-auto-build/debug-env/debug-build.log

# 设备
ssh root@192.168.1.1      # 密码 wuming
```

---

## 9. 坑与教训（接手必读）

1. **官方 LEDE CI 只验证 x86**——aarch64 的 binutils ar / off64_t 全部要自己兜底
2. **runner 镜像是快速移动目标**——"同配置一次成功一次失败"先怀疑镜像批次与缓存
3. **cache 与 glibc 版本绑定**——staging_dir 跨系统命中会污染（GLIBC_2.38 not found）
4. GitHub cache 7 天未访问即驱逐；失败 run 不保存；取消 run 的 post 可能存下不完整缓存
5. Kconfig：`prompt "x" if COND` 条件假时选项不可见；`default y if !TOOLCHAINOPTS` 优先于显式 `# is not set`
6. feeds install 的 package/feeds 是 symlink，find/sed patch 要走 feeds 源目录
7. 22.04 runner 自带 immutable /swapfile（chattr +i），swap 操作先容错
8. easimon/maximize-build-space 在新镜像布局下会破坏环境，用官方 apt purge 清理
9. LEDE master 当前最新 `8882f211bb`（08-12）；构建系统文件 7 月底以来零变化
10. PowerShell 下 git push 报 NativeCommandError（exit 1）是 stderr 误报，看 `xxxx..xxxx main -> main` 即成功

---

## 10. 当前状态与下一步

1. **Run 74 进行中**：验证 binutils 2.40 的 ar 是否通过 libgcc 打包
   （关键节点：开始后约 30-40 分钟）
2. Run 74 成功后：把 `backup-main-fullfeature` 的功能修改合并回 main，编译最终全功能固件
   （合并要点：m28c.config 的功能包段、files/ 的 99-expand-data / zzzz-custom / istore-meta.conf、build.sh 的 iStore patch）
3. 若 2.40 的 ar 仍崩：给 binutils TARGET_CFLAGS 加 `-fno-stack-protector`（构建工具可接受），或找上游 ar 补丁
4. 若 1G 镜像装不下全部包（镜像生成阶段失败）：调大 `CONFIG_TARGET_ROOTFS_PARTSIZE` 至 1.5~2G
