# OpenClash 插件与预装核心

2026-10-16 核实的最新稳定版本为 OpenClash **v0.48.002**、Mihomo **v1.19.32**。插件从官方标签对应的完整提交 `e553e82e01c697aefbe1555261a503f425e00ccd` 编译，核心使用 MetaCubeX 发布的官方 Linux ARM64 程序。

两者记录在 `sources.lock.json`。LEDE、内核和其他 feeds 继续使用原有锁定版本。“最新版”指这次核实的明确版本；后续更新必须重新确认 release 和 SHA256，再更新锁文件，构建时不会追踪浮动 latest。

## 核心下载与安装

官方核心资产：

- 发布页：<https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32>
- 文件：`mihomo-linux-arm64-v1.19.32.gz`
- 长度：20,809,592 字节
- SHA256：`9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0`
- 摘要来源：官方 GitHub release asset 的 `digest` 字段。

已实际下载上述资产并通过脚本验证。解压后的 ARM64 核心为 **57,409,662 字节**，SHA256 为 `c80df2ca5159567804da883401e0118908fc1155e05df2baf3dbd4d3ecfc3754`，格式为无动态解释器的 ELF64 AArch64 可执行文件。

复制项目 `package/` 到 LEDE 后、生成 defconfig 前执行：

```sh
python3 scripts/prepare-openclash-core.py lede
```

脚本复用 `lede/dl` 下载缓存，核验压缩包长度和 SHA256，再完整检查 gzip 校验和与结束位置。解压后检查 ELF64、小端序、AArch64、有效可执行段和无动态解释器；不在构建主机运行 ARM64 核心。损坏的下载或核心不会替换已有输出。

`m28c-openclash-core` 是独立 opkg 软件包，版本为 `1.19.32-1`。它把核心以 `0755` 权限安装到最新插件使用的 `/etc/openclash/core/clash_meta`，并将公开来源、二进制摘要和版本放到 `/usr/share/m28c-openclash-core/source.json`。配置中必须选择 `CONFIG_PACKAGE_m28c-openclash-core=y`，且最终 manifest 必须包含该包和 `luci-app-openclash`。

## 用户配置与验收

公开镜像仅预装插件和核心，不包含代理订阅、节点、令牌或在线设备运行数据。OpenClash 默认关闭，首次使用由用户导入配置后启用。

最终验收须核对镜像内核心的 ELF 架构、执行位及 SHA256，软件清单中的插件/core 包版本，以及镜像附带的锁文件。构建和文件校验不等于实际代理联网测试；需要在用户完成配置后另行验证。

OpenClash 自带的核心更新功能可以由用户覆盖 `clash_meta`。发生手动更新后，opkg 包版本与随包保存的 `source.json` 仍表示镜像预装版本，实际运行版本应以核心 `-v` 输出及当前文件摘要为准。
