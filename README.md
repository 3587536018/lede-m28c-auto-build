# MangoPi M28C 中文定制固件

为 MangoPi M28C（RK3528 / ARM64）构建公开 LEDE 固件。配置参考 2026-10-15 实机的软件清单，源码固定到最近成功发布的 Linux 6.12.104 / QModem 3.2.0 基线。

## 定制内容

- Argon 中文界面与新的 M28C 控制台：实时运行状态、存储容量、5G / 应用 / Docker / 文件共享 / 网络设置快捷入口，适配手机。
- iStore 原生中文：修复 LuCI 构建语言别名，正确生成商店和任务弹窗的简体中文 LMO，保留主界面语言兼容补丁。
- QModem、OpenClash、Docker / Compose / Dockerman、Homebox、DDNS / DDNS-Go、Samba、FTP、TurboACC、OpenSSH、常用诊断工具。
- 5G USB 槽位自适应：按固定物理接口识别模块，重启后总线或串口编号变化时保留拨号设置，并刷新拨号进程与断线监控。适用范围与验证方式见 `docs/QMODEM-USB-REBIND.md`。
- 1 GiB 根分区，ext4 与 squashfs 两种镜像；首次启动使用尾部空闲空间建立 `/opt`，或复用已有 `opt` / `optdata` 分区。
- DockerMan 恢复刷机前的 Lua 0.5.13.20241008 界面；OpenClash 0.48.002 从固定源码编译，内置官方 Mihomo 1.19.32 ARM64 内核。
- 软件源保留签名验证，补齐官方 24.10 公钥；同时交付同次编译的签名 IPK 归档。
- Docker 数据目录 `/opt/docker`，512 MiB swap 只在数据分区正确挂载后创建。
- Docker 默认使用原生 nftables，与 firewall4 配合处理容器出网、LAN 发布端口和 WAN 入站限制，避免生成旧版 iptables 规则；配置与限制见 `docs/DOCKER-FIREWALL.md`。
- 插入 USB 存储或外置存储卡后自动挂载受支持的数据分区，保留已有挂载配置和卡内文件；系统盘、固件启动分区及 swap 不作为普通存储挂载，见 `docs/AUTOMOUNT.md`。

## 首次使用

1. 备份 TF 卡后，将 `*.img.gz` 解压并用 balenaEtcher 等工具写入 TF 卡。整卡刷写会覆盖卡上现有数据。
2. 用网线连接，浏览器打开 `http://192.168.1.1`。用户名 `root`，固件没有预设密码，在 LuCI 中先设置管理密码。
3. 全新镜像默认启用 Wi-Fi，名称为 `openwr`，开放网络、无密码。可在无线设置中自行修改；保留配置升级不会改写已有无线设置。SSH 不接受空密码。
4. 在 QModem 中确认 modem；自行导入 OpenClash 订阅、DDNS 凭据，并配置共享目录后启用相应服务。

固件不包含实机的密码、Wi-Fi 密钥、订阅、DDNS 令牌、SIM 标识、SSH 私钥或 `/opt` 运行数据。OpenClash、Samba、FTP、DDNS 默认等待用户配置。自建 m3u8 / WorkBuddy Hub 程序及其数据需独立迁移。

## 构建

使用专用 Ubuntu 22.04 构建环境。`install-env.sh` 会修改 APT 源及安装系统依赖。

```sh
sudo ./install-env.sh
./prepare.sh
./build.sh
```

构建前会检查锁定版本、必要软件包和公开默认设置，构建后验证两种镜像的 gzip 与软件 manifest。生成目录：`lede/bin/targets/rockchip/armv8/`。

GitHub Actions 支持手动、main 推送及北京时间每周五 02:00 定时构建。定制开发分支只上传 Actions 固件产物，不修改现有 Release；main 自动构建成功后发布唯一的新标签。手动触发默认不发布，须明确选择 publish。

默认额外上传 `m28c-matching-packages-构建号`，包含同次构建 IPK、索引、签名及公开构建密钥；内核模块只能使用与固件完全匹配的构建产物，具体见 `docs/PACKAGE-FEEDS.md`。

下载源码缓存与工具链分离，工具链每次冷编译。`sources.lock.json` 记录全部 LEDE/feed 版本。升级版本时需更新锁文件并重新验证驱动；此前跟随 master 的构建因 Linux 6.18 移除 `hrtimer_init` 导致蜂窝驱动失败。

## 检查与资料

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/expand-data-test.sh
bash tests/automount-test.sh
```

- `docs/DEVICE-2026-10-15.md`：实机环境与定制取舍。
- `docs/device-inventory-2026-10-15.json`：604 个已安装包及筛选后的通用配置。
- `docs/HANDOFF.md`：维护和验证入口。
- `docs/QMODEM-USB-REBIND.md`：5G 槽位识别、配置保留和热插拔恢复。
- `docs/DOCKER-FIREWALL.md`：Docker nftables、容器 DNS 和 firewall4 策略。
- `docs/AUTOMOUNT.md`：外置存储自动挂载、显式 fstab 优先级和数据保护。
- `package/UPSTREAM.md`：额外软件来源。

首次启动脚本只会格式化本次明确新建且无文件系统签名的数据分区。遇到不明分区、挂载失败或已有未迁移文件会停止并保留脚本，供检查后重试。整卡刷写与保留配置升级是不同操作，不应依赖初始化脚本保护刷卡前的数据。
