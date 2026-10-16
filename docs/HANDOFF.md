# M28C 定制固件维护交接

更新：2026-10-16。以当前代码、锁文件和实际构建结果为准。

## 架构

`prepare.sh` → `scripts/prepare-sources.py` 获取 `sources.lock.json` 中固定的 LEDE/feeds；`build.sh` 安装 feeds，复制 `package/*`，运行公开默认设置与 iStore 补丁，导入 `m28c.config`，校验必需包并编译。

源码基线来自现有 Release v0.260815.012002 在 2026-08-28 更新的资产，不能从旧标签日期判断版本。LEDE 为 `8e247d214f071ef0cb3a31c879d4daf224849d3e`，Linux 6.12.104。此前失败的 #100 使用流动 master / Linux 6.18.55，失败点是 `fibocom_QMI_WWAN` 的 `hrtimer_init`。

## 维护入口

- `m28c.config`：成功发布的精简 config.buildinfo 加上实机通用软件。
- `configs/required-packages.txt`：必须在 defconfig 和最终 manifest 中存在的软件，防止静默漏包。
- `scripts/customize-public.py`：删除共享工厂密码注入、不兼容旧 LuCI 源及禁用签名验证的命令，按用户最新要求将新生成的无线接口设为启用、SSID `openwr`、无密码；不改已有无线配置。
- `files/etc/uci-defaults/zzzz-custom`：通用语言、主机名、CPU、QModem 自动检测、服务默认状态。不能加入实机账号和订阅。
- `files/etc/uci-defaults/99-expand-data`：幂等存储初始化，仅对明确新建分区格式化，挂载确认后配置 Docker/swap。
- `package/luci-app-m28c`：基于 LuCI RPC 的中文首页和响应式样式。
- `scripts/customize-ui.py`：iStore 控制器的严格、幂等语言补丁。修复商店及 taskd 的原生 LMO 语言别名漏编问题；必须验收 `iStore.zh-cn.lmo` 和 `lib-tasks.zh-cn.lmo` 的实际词条。
- `scripts/customize-qmodem.py` 与 `patches/qmodem/`：固定物理 USB 接口身份、槽位变更后复用 UCI 段，以及拨号/监控进程重新绑定。完整说明见 `docs/QMODEM-USB-REBIND.md`。升级 QModem 时补丁哈希不匹配会停止构建。
- `scripts/verify-firmware.py`：只读检查 gzip 压缩数据与 fwtool 升级元数据的完整性、CRC 和目标设备；构建和最终下载验收使用同一校验器。
- `.github/workflows/build.yml`：Ubuntu22.04、下载缓存、固件及失败日志 artifact、唯一Release标签。

## 默认访问

新固件 LAN 为 192.168.1.1，root 无预设密码，必须先通过有线 LuCI 设置密码，再使用 SSH。Wi-Fi 首次默认启用，SSID `openwr`，开放网络且无密码（2026-10-16 用户明确更改了之前禁用策略）；镜像不含私人 Wi-Fi 密钥。已有用户密码不在首次启动时清空。

## 构建/验证注意

项目本地是 Windows，完整交叉编译通过 GitHub Actions。修改后先运行 Python 单元测试、存储 mock、Shell语法检查。UI预览 `tests/ui-preview.html` 只使用明确标记的模拟数据；不能替代真固件验收。

手动构建默认只生成 artifact，定制开发分支不发布。main 定时/推送构建和显式 publish 的main手动构建成功后才创建独立Release。源下载缓存可复用，staging_dir/build_dir 不跨 runner 缓存。

构建 #101 已生成两种镜像，但普通 `gzip -t` 把 OpenWrt 在 gzip 后附加的 fwtool 升级元数据判为尾部垃圾并返回 2。必须使用元数据感知校验器，不能删除元数据或忽略所有 gzip 错误。定制开发分支推送新修复时会取消过时构建，其他分支的并发策略不变。

完整源码或feeds版本升级后，应重新检查Modem/Wi-Fi驱动、LuCI包名、iStore控制器、必需包manifest和ext4/squashfs启动。真实设备刷写需要独立明确指令；检查设备不等于刷写授权。

## #103 实机反馈后的修复

- 自动挂载必须允许固件预置的唯一空 `/opt/docker`，但在分区操作前拒绝真实数据、其他目录及符号链接。旧设备已经积累的 `/opt` 数据不能自动隐藏，见 `docs/POST-FLASH-2026-10-16.md`。
- DockerMan 使用原设备同源的固定旧 Lua 包；见 `docs/DOCKERMAN.md`，构建时排除上游同名 JS 包。
- 用户要求最新版 OpenClash 插件和代理内核：唯一更新的已有 feed 是 OpenClash v0.48.002 的固定提交，Mihomo ARM64 1.19.32 单独锁定哈希；其余 LEDE/LuCI/QModem 与 Linux 6.12.104 保持锁定。见 `docs/OPENCLASH.md`。
- 软件源和同次构建 IPK 归档见 `docs/PACKAGE-FEEDS.md`，禁止关闭签名或强装不匹配 kmod。
- QModem AT 报告未激活但 QMI/QMAP 数据接口已 up 时，按 netifd 地址及真实 sysfs 归属补判连接；不改拨号、APN、模块状态或 USB 自适应。
