# M28C 中文控制台

独立 LuCI JS 页面 `/admin/m28c`，使用当前 Argon 主题的外框，不覆盖主题、原系统概览或插件控制器。菜单顺序为 1，适用于 LuCI 的默认首页选择。所有样式仅作用于 `.m28c-dashboard`。

- 每 10 秒读取 `system.board`（仅首次）、`system.info` 和 `luci.getMountPoints`。负载显示为 1 / 5 / 15 分钟平均值，不当作 CPU 使用率。
- 内存按 `total - available` 计算；旧接口缺少 `available` 时使用 `free + buffered + cached`。合法的零值照常显示。
- 系统存储优先显示 `/overlay`，ext4 镜像回退到 `/`；独立列出 `/opt` 数据分区容量。读取失败时显示“暂不可用”，不显示示例数据。
- 快捷入口根据 `ui.menu.load()` 的真实菜单筛选，没有安装或无权访问的入口不会生成链接。
- 首次设置区只提供管理密码和无线设置的链接，不读取口令、密钥或无线配置。面板没有修改配置或执行 Shell 的权限。
- 保留 `/admin/status/overview`，页面提供“完整系统概览”入口。

## 构建接入

将此目录复制到 LEDE 的 `package/luci-app-m28c`，设置 `CONFIG_PACKAGE_luci-app-m28c=y`，在 feeds 更新后执行仓库的 `scripts/customize-ui.py LEDE_ROOT`。iStore 补丁只修改语言选择函数与菜单标题；遇到不认识的上游代码会明确停止构建。

## 预览与验证

在仓库根目录运行 `python -m http.server 8768 --bind 127.0.0.1`，打开 `http://127.0.0.1:8768/tests/ui-preview.html`。预览直接运行本包中的 JS 和 CSS，顶部始终标明“示例数据，未连接设备”，可切换正常、RPC 失败、缺少扩展插件以及零值场景，也可以切换深色、缩窄窗口检查手机布局。

执行 `python -m unittest discover -s tests -p test_customize_ui.py -v` 验证补丁幂等性、上游变化拒绝与原控制器保留情况。安装可选 Python 包 `lupa` 后，还会在 Lua 运行时中验证语言变体、旧/新 LuCI 翻译接口、前端语言资源缺失和显式英语选项；未安装时只跳过这组运行时检查。`node --check package/luci-app-m28c/htdocs/luci-static/resources/view/m28c/dashboard.js` 可进行 JS 语法检查。

预览无法替代固件验证：刷入测试介质后仍需核对实际 ACL、自动首页跳转、Argon 明暗模式、iStore 首次中文和各插件路由。无需修改现有设备即可预览。
