# 恢复刷机前的 DockerMan 界面

固件恢复设备原来使用的 Lua/CBI DockerMan。入口仍是“Docker”，包含配置、概览、容器、镜像、网络、存储卷和事件页；保留原有容器创建、控制台、文件、日志等界面与简体中文翻译。

只替换 `luci-app-dockerman` 和 `luci-lib-docker` 两个包，其他 LuCI 源仍使用项目的锁定版本。Docker Engine、Compose 和已有容器数据不因这项界面恢复而降级或迁移。

## 固定源与许可证

两个包取自 [immortalwrt/luci 的固定提交](https://github.com/immortalwrt/luci/tree/af8a35c38a548a5319349e3ff8ff70820bb0a4b3)：

| 项目目录 | 上游目录 | 固定包版本 |
| --- | --- | --- |
| `package/luci-app-dockerman` | `applications/luci-app-dockerman` | `0.5.13.20241008-r1` |
| `package/luci-lib-docker` | `collections/luci-lib-docker` | `26.005.68630~af8a35c-r1` |

全部 72 个包源文件已随仓库保存，不在构建时追踪上游分支。各包的 `UPSTREAM.json` 记录来源路径与 Git blob SHA-1；测试会重新核对每个文件。Lua、模板、图标、ACL 和所有翻译文件与该提交逐字节一致。

本地仅修改两个 Makefile：把相对的 `../../luci.mk` 改成当前构建树的 `$(TOPDIR)/feeds/luci/luci.mk`，显式固定包版本及 release，避免本仓库的提交时间改变库版本。应用原有的 `+luci-compat` 依赖保留。

包 Makefile 声明 AGPL-3.0，部分文件有 Apache-2.0 的独立声明，均保留原作者信息。各包保留上游仓库根目录的 Apache `LICENSE` 和 `NOTICE`，并附 AGPL-3.0 完整文本 `COPYING`。后者来自 [SPDX license-list-data v3.25.0](https://github.com/spdx/license-list-data/blob/v3.25.0/text/AGPL-3.0-only.txt)，SHA-256 也写在源记录中。

## Docker 29 API 核对

此版本的 Lua 库在 `options.version` 中留下了 `v1.40` 默认值，但 HTTP 请求生成器从未使用它。实际发出的路径是 `/containers/json`、`/info`、`/images/json` 等，没有 `/v1.40` 或 `/v1.41` 前缀。因此没有机械修改这个未使用的字段，也没有改动界面来规避 API 问题。

[Docker 29.1.1 的版本中间件](https://github.com/moby/moby/blob/docker-v29.1.1/daemon/server/middleware/version.go) 明确把未指定版本的请求设为服务器默认 API，再检查服务器支持的版本范围。该行为不会触发“客户端 API 低于 1.44”的拒绝。

`tests/test_dockerman.py` 在 Lua 5.1 中执行完整的原始库与控制器，使用内存 socket 模拟 Docker HTTP 响应，检查实际发出的请求和 JSON/流式返回值。覆盖无版本请求、概览所需资源、创建与启动容器、文件下载回调、本地自定义 socket、远程连接配置和旧版导航入口。另检查全部 Lua 文件可以解析，简体中文目录完整，全部固定源未被意外修改。

这些检查证明请求路径和 LuCI 客户端行为，不能代替设备上 Docker 29 的全部功能验收；编译、安装后的实际页面和容器操作仍需在对应设备上验证。

## 构建集成

在 feeds 安装后、项目 `package/*` 复制到 `package/custom/*` 之前，排除原 feed 的同名包链接与对应目录，避免新旧实现产生重复定义。需要排除的是 `package/feeds/luci/luci-app-dockerman`、`package/feeds/luci/luci-lib-docker`，以及锁定 feed 内对应的 `applications/luci-app-dockerman` 和 `collections/luci-lib-docker` 源目录。若该 feed 将库放在 `libs/`，也需排除同名目录。

随后使用现有项目包复制逻辑安装两个目录。保留 `CONFIG_PACKAGE_luci-app-dockerman=y`、`CONFIG_PACKAGE_luci-lib-docker=y` 与 `CONFIG_PACKAGE_luci-i18n-dockerman-zh-cn=y`。翻译仍由当前锁定的 `luci.mk` 从 `po/zh_Hans/dockerman.po` 生成，与旧版 Lua 界面配套。

验证命令（需要项目 CI 已安装的 `lupa==2.8`）：

```sh
python -m unittest discover -s tests -p 'test_dockerman.py' -v
```
