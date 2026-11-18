# M28C 风扇控制

固件内置基于用户提供的 `风扇固件.ipk` 的 `luci-app-m28c-fan`，版本
`2026.11.16-r3.m28c1`，架构 `all`。原包版本为 `2026.11.16-r3`；派生版只修正
页面文案，控制逻辑和默认配置保持原始字节。页面入口为 **系统 → 风扇控制**，
提供自动温控、手动 PWM 指令和四点温控曲线；温度来自 SoC/CPU。

## 原包与默认行为

- 原包 10,905 字节，SHA256：
  `4f43ab3d4e8b190beed27915fc4ab8ed26bfb29c46e6f11f82bcd557e0840b00`。
- 原包保存在 `package/luci-app-m28c-fan/original.ipk`；`SOURCE.json` 记录 control、
  conffiles、安装脚本及七个运行文件的大小、权限和摘要。
- 原始 `original.ipk` 与 `SOURCE.json` 保留不变。七个运行文件中，控制脚本、
  服务、默认配置、启动设置、菜单和 ACL 六个文件与原包逐字节一致；仅中文页面
  按 `UI-CHANGES.json` 中的确定性翻译字符串替换生成。记录包含原包/输入/输出
  摘要、派生版本与每处替换次数，不能用于覆盖其他路径或修改 JavaScript 逻辑。
  原 control 声明许可证 Apache-2.0。控制脚本注明改编自 `luci-app-openfi` 并提及
  `SOURCE.txt`，但提交的 IPK 没有附带该文件；这里保留原注释，不推测上游版本。
- 默认自动温控，低于 60°C 请求 0% PWM；四点曲线为 60°C/30%、65°C/55%、70°C/80%、
  75°C/100%；85°C 或温度读取失败时请求 100% PWM。默认采样周期 5 秒。
- 原服务只在 `widora,mangopi-m28c` 上启动，使用 procd 管理，`START=99`。
  `/etc/config/m28c_fan` 仍声明为 conffile，保留配置升级遵循 opkg 的配置保留规则。

## 硬件条件

控制器按硬件地址 `ffa90030.pwm` 查找 PWM3，单通道、正常极性、20 kHz。
当前锁定 LEDE 提交 `8e247d214f071ef0cb3a31c879d4daf224849d3e` 已具备这些条件：

- `rk3528-mangopi-m28c.dts` 启用 `&pwm3` 和 `pwm3m0_pins`。
- `rk3528.dtsi` 将 PWM3 定义在 `0xffa90030`。
- `target/linux/rockchip/armv8/config-6.12` 启用 `CONFIG_PWM`、
  `CONFIG_PWM_ROCKCHIP`、`CONFIG_PWM_ROCKCHIP_V4` 和 `CONFIG_ROCKCHIP_THERMAL`。
- 共享设备树启用 SoC 温度传感器，通用内核配置启用 sysfs。

本次文案修正不修改设备树、内核配置、PWM 极性、频率或占空比算法，也不加入
会接管 PWM 通道的 pwm-fan 驱动。源码条件核对不能替代风扇实际转动测试。

2026-11-18，用户在 M28C Car 上观察到页面显示低温停转，但风扇仍在转动；同期
只读检查确认服务运行、CPU 38°C、目标与输出均为 0%，PWM3 已启用且
`period=50000`、`duty_cycle=0`、`polarity=normal`。这说明现有页面把控制指令
误写成了已经测得的机械状态，不能单凭此现象推断需要反转极性。

派生页面明确将百分比标为 PWM 指令，说明没有转速反馈；低温状态仅表示请求
0%，保护状态仅表示请求 100%，均不声称已停转或机械全速。删除原页面“30%
已实测持续运行”的声明；最低输出和实际起转能力需要在当前风扇上确认。

## 构建集成与验收

该包使用原生 `BuildPackage` 定义参与完整构建；依赖 `libc`、`luci-base`，保留
`Provides: luci-app-m28c-fan-any`，并显式固定派生版本。安装六个原始文件及经校验
的派生页面，避免 LuCI 构建规则再次压缩 JS。现场安装的缓存刷新逻辑只在没有
`IPKG_INSTROOT` 时运行，构建主机不会执行风扇控制或重载 rpcd。

`m28c.config` 将包选为 `y`，`configs/required-packages.txt` 要求它同时出现在
defconfig 和最终 manifest 中。构建前验证原 IPK、原始记录与确定性页面变换；
构建后检查实际 rootfs 中六个原始文件及派生页面的完整字节、权限、派生包版本、
配置清单及 S99 开机链接。原页面、未声明的编辑、原包版本混入新镜像均会被拒绝。
同次构建的包由既有 matching-packages 流程导出，验收时核对它在该次构建的
原始已签名索引中的覆盖情况；本次源码文案修改本身不代表新 IPK 已构建或验签。
固件 artifact 保留 `fan-package-source.json` 核对原包来源，并携带
`fan-ui-changes.json`（仓库中的 `UI-CHANGES.json`）核对派生页面；不能将派生
页面摘要写回原始来源记录。

本地与 CI 检查命令：

```sh
python3 scripts/verify-fan-package.py
python3 -m unittest discover -s tests -p 'test_fan_package.py' -v
sh -n package/luci-app-m28c-fan/files/usr/sbin/m28c-fan-control
node --check package/luci-app-m28c-fan/files/www/luci-static/resources/view/m28c-fan/settings.js
```

这些检查以及构建后 rootfs 检查均不连接或修改在线设备。
