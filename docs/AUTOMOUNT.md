# USB / SD 存储卡自动挂载

构建调用 `scripts/customize-automount.py`，直接修改锁定的 LEDE `automount`、
`block-mount` 和 Samba4 包源码。每份原始文件有完整 SHA256 门禁，重复调用幂等；
来源漂移、已有补丁或 runtime 源文件被另行修改都会中止构建。

旧 `15-automount` 会把固件卡的 kernel 分区当作文件系统尝试挂载，又会跳过
`block info` 已识别的普通分区；搭配 `anon_mount=0`，真正的数据分区就没有挂载。
新包不再安装旧 hook 和延迟搬移脚本，直接安装受控 hook、helper 和启动扫描服务。

## 实际行为

- 支持 USB 存储或 sysfs 明确标记为可移除 `SD` 的卡，挂载到
  `/mnt/removable/<设备分区名>`，例如 `/mnt/removable/sda3`。
- 插入时执行 hotplug；开机时由 `S95m28c-automount` 再扫描已插入的卡。
- 允许 ext2/ext3/ext4、f2fs、btrfs、FAT、exFAT、NTFS 数据文件系统；仍需相应
  内核驱动。NTFS 使用 ntfs3。参数为 `rw,noatime,nodev,nosuid`，不强加 discard。
- 不创建分区、不格式化、不运行修复工具、不添加 swap、不修改 Docker 数据根目录。
- `/rom` 或直接根挂载无法解析为真实系统盘时停止匿名挂载；系统盘，以及 `/`、
  `/rom`、`/overlay`、`/opt` 所在磁盘全部排除。loop、MTD、kernel、squashfs、swap
  以及固件保留标签不会进入匿名挂载。
- 已有分区的整盘事件跳过；已挂载设备、重复 UUID、目标目录已有内容、链接、已有
  挂载或遗留所有权记录时不会覆盖。并发事件通过独占目录锁串行处理。
- 卸载只处理 helper 自己记录的挂载点，核对真实设备号及文件系统根目录。仅普通
  `umount` 成功后移除空目录；忙挂载保留，既不强制也不 lazy detach。

## 用户配置优先

有显式 fstab UUID、label 或 device 条目的盘交由原生 block-mount 处理，包括
`enabled=0` 的禁用条目；自定义目标不被匿名 helper 改写。全局 `auto_mount=0`
也会禁用 helper 自动挂载。原生 block-mount 的显式配置行为保留。
例如设备当前已有按 UUID 挂载到 `/mnt/sdcard` 的配置，保留配置升级后仍沿用
`/mnt/sdcard`，不会另挂一份到 `/mnt/removable/`。

首次启动的 `10-fstab` 只在配置文件不存在时写入原有全局默认值，不再由
`block detect` 自动为当时插入的卡创建 `enabled=0` 条目。保留配置升级时不会重写
用户 fstab；`99-expand-data` 继续独立配置系统数据分区 `/opt` 和 swap。

已有旧版自动生成的禁用条目仍视为用户配置；需要用户明确删除或启用，不能由
新脚本猜测其来源并绕过。匿名挂载不写 UUID 配置，所以设备枚举改变时路径也可能
改变；需要固定路径的用户可以在 LuCI 挂载点中设置 UUID 和目标目录。

## 相关热插拔保护

`05-m28c-automount` 先于原生 `10-mount`。自有挂载普通卸载失败时，所有权记录仍在，
原生 `block hotplug` 的 remove 分支会跳过该设备，防止随后执行 lazy detach。
其他原生挂载流程保持原行为。

Samba4 的 `20-smb` 继续遵循原来的 autoshare 开关，保留共享条目的删除及 reload；
移除其中 `rm -rf /mnt/...`，避免自动共享启用时递归删除仍挂载或忙碌的卡内容。
新功能没有开启自动共享。

此次部署目标是完整固件镜像。构建必须检查最终 rootfs 中的新文件、可执行权限、
S95 链接和旧 hook 缺失；不保证单独用 opkg 升级旧 automount 包的迁移，因为旧包
曾移动出的 `/etc/hotplug.d/block/15-automount` 不属于原安装清单。不得在仍保留旧
hook 的设备上仅安装新包并称为完成升级。

## 验证

`python -m unittest discover -s tests -p test_automount.py` 校验锁定源码、幂等、
改动保护、首次启动 fstab、忙挂载 native guard、Samba autoshare remove 数据保护。
`bash tests/automount-test.sh` 使用隔离 sysfs/mountinfo/UCI fixture 验证插卡、重复事件、
系统盘及 `/opt` 排除、SD/eMMC 区分、显式配置、文件系统白名单、UUID 冲突、已有
目录内容保护、忙卸载与并发锁。它们不连接设备，不执行真实挂载。

这些是构建/隔离测试范围，实际新镜像还需完整下载、离线验收；没有据此声称已
在用户设备刷入新固件并完成插拔或开机实测。
