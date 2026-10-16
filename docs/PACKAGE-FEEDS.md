# 软件源、签名与包兼容性

本固件保留 `option check_signature`。默认启用腾讯镜像的 OpenWrt 24.10.5 `aarch64_generic` 用户态补充源：`base`、`luci`、`packages`、`routing`、`telephony`。镜像内已经安装的 iStore、OpenClash、QModem 等软件仍然保留。iStore 自己管理的、能够通过签名校验的 compat 源继续由其软件包负责，不由此文件覆盖。

这些补充源可以用于有针对性地获取应用，不能据此认定 LEDE 与 OpenWrt 24.10 的软件包可全面混装，也不应对整机执行批量 `opkg upgrade`。安装前应检查依赖、包来源及版本；不能用 `--force-depends` 绕过内核或库依赖。

## 本次错误的原因与证据

锁定 LEDE 提交 `8e247d214f071ef0cb3a31c879d4daf224849d3e` 的 `include/version.mk` 默认将仓库根地址设为 `https://downloads.openwrt.org/releases/24.10.5`；`include/feeds.mk` 又将所有编译源码 feed 名字拼入该地址。首次启动的 `99-default-settings` 仅将域名替换为 `mirrors.tencent.com/lede`。因此源码中的 `istore`、`openclash`、`qmodem` 被误当成该公开仓库中已经发布的目录。

2026-10-16 只读核对结果：

- 腾讯的 `core`、`base`、`luci`、`packages`、`routing`、`telephony` 的 `Packages.gz` 能下载；解压后的索引与对应 `Packages.sig` 均通过下列官方 24.10 公钥的 Ed25519 校验。
- 同一地址下的 `istore`、`openclash`、`qmodem` 返回 HTTP 404。去掉这些无效源条目不等于卸载相应应用。
- 原固件只包含 iStore 公钥、OpenWrt snapshot 公钥及本次本地构建公钥，没有官方 24.10 release 公钥，所以前六路索引签名失败。官方仓库的签名没有使用本项目的临时构建密钥。
- 锁定源码的 `package/system/openwrt-keyring/Makefile` 只安装 snapshot 公钥 `b5043e70f9a75cde`。`package/base-files/Makefile` 在没有构建密钥时生成 `Local build key` 并将公钥装入镜像；`package/Makefile` 用此密钥签署自己编译的索引。这只能验证本次构建的软件包索引，不能替代官方 release 公钥。

## 官方公钥来源

公钥文件：`files/etc/opkg/keys/d310c6f2833e97f7`。

- 权威仓库：OpenWrt 官方 `openwrt/keyring`。
- 完整提交：`fbae29d730f81c892f52e0ff00fe867444aeeae6`。
- 固定来源：<https://raw.githubusercontent.com/openwrt/keyring/fbae29d730f81c892f52e0ff00fe867444aeeae6/usign/d310c6f2833e97f7>。
- 原始文件 SHA256：`e3624aa9be785362a172595b4919b233268871c4365a9b8da2b42ac41745ad95`。
- usign key ID：`d310c6f2833e97f7`，描述为 `Public usign key for 24.10 release builds`。

此文件是公开验签密钥，不含私钥。回归测试核对完整原始文件哈希及解码后的 `Ed` 算法标记、42 字节公钥结构和 key ID。在线索引验证是本次调查证据，测试不依赖外部网络。

## 内核和用户态边界

本项目 #103 固件的 kernel 包为 `6.12.104-1-3fc3553b323ebeaf8484fc8c1710e7e8`。官方 OpenWrt 24.10.5 的 `rockchip/armv8/profiles.json` 标明内核为 `6.6.119`、release 为 `1`、vermagic 为 `77d4782035a23e6f19f9c4751451b4e3`。它们不兼容；同架构和同设备系列不能代替内核 ABI 匹配。

默认源不启用官方 `core` 或任何 `kmods` 目标仓库。当前官方 `core` 索引主要含 `base-files`、`fstools` 等目标相关用户态软件包，内核模块另有独立仓库；排除 `core` 同时避免这些基础包直接覆盖定制 LEDE 系统。所有 `kmod-*` 只能使用**与运行固件同一次构建**产生、kernel 完整依赖与 vermagic 一致的软件包。不要添加官方 6.6 或其他 6.12 构建的 kmod 源，也不要强制忽略依赖。

用户态也存在实际差异：本镜像安装 `libubus20241020`，公开 24.10 仓库使用 `libubus20250102`；本镜像为 OpenSSL 3.6.3，而本次检查的官方源为 3.0.22。因此五路补充源并不提供整机升级的兼容性承诺。遇到要求替换核心库、基础服务或内核模块的软件，应使用项目锁定源码编译，并随新的完整固件或匹配的软件包归档交付。

## 同次构建软件包归档

构建工作流应同时保留本次编译的 IPK、`Packages`、`Packages.gz`、`Packages.sig`、公开构建密钥、完整 kernel ABI、`sources.lock.json` 和定制提交标识，作为与固件对应的离线 artifact；不上传构建私钥。恢复或补装本固件软件时，优先使用这份同次构建归档。

需要长期在线更新源时，应将这份签名仓库发布到带构建标识的固定目录，保留其目录结构与 IPK，再配置相应仓库地址。仅上传镜像、只有 IPK 而没有索引和签名，或复用另一次编译的同名版本，都不能代替匹配的签名软件源。新生成的构建公钥必须与交付固件中信任的公钥对应。

## 首次启动兼容处理

锁定上游默认脚本会对 `distfeeds.conf` 执行 `sed -i "s/# //g"`。本项目的源文件使用紧邻 `#` 的注释形式，不写 `# `，以免注释或禁用的源被重新启用。测试覆盖这一实际首次启动变换。源文件覆盖和公钥添加都不关闭签名校验，也不改动在线设备。
