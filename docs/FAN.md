# M28C 风扇控制

固件内置用户提供的 `风扇固件.ipk`，包名 `luci-app-m28c-fan`，版本
`2026.11.16-r3`，架构 `all`。页面入口为 **系统 → 风扇控制**，提供自动温控、
手动 PWM 输出和四点温控曲线；温度来自 SoC/CPU。

## 原包与默认行为

- 原包 10,905 字节，SHA256：
  `4f43ab3d4e8b190beed27915fc4ab8ed26bfb29c46e6f11f82bcd557e0840b00`。
- 原包保存在 `package/luci-app-m28c-fan/original.ipk`；`SOURCE.json` 记录 control、
  conffiles、安装脚本及七个运行文件的大小、权限和摘要。
- 七个运行文件与 IPK 逐字节一致，包括原有中文页面、服务与默认配置。
  原 control 声明许可证 Apache-2.0。控制脚本注明改编自 `luci-app-openfi` 并提及
  `SOURCE.txt`，但提交的 IPK 没有附带该文件；这里保留原注释，不推测上游版本。
- 默认自动温控，低于 60°C 停转；四点曲线为 60°C/30%、65°C/55%、70°C/80%、
  75°C/100%；85°C 强制全速，温度读取失败也请求全速。默认采样周期 5 秒。
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

因此本次不需要修改设备树或内核配置，也不加入会接管 PWM 通道的 pwm-fan 驱动。
源码条件核对不能替代刷入新镜像后的风扇转动、温度曲线及页面实测。

## 构建集成与验收

该包使用原生 `BuildPackage` 定义参与完整构建；依赖 `libc`、`luci-base`，保留
`Provides: luci-app-m28c-fan-any`，并显式固定完整版本。直接安装原包文件，避免
LuCI 构建规则再次压缩已经压缩过的 JS。现场安装的缓存刷新逻辑只在没有
`IPKG_INSTROOT` 时运行，构建主机不会执行风扇控制或重载 rpcd。

`m28c.config` 将包选为 `y`，`configs/required-packages.txt` 要求它同时出现在
defconfig 和最终 manifest 中。构建前验证原 IPK 与展开文件；构建后检查实际
rootfs 中的文件字节、权限、opkg 包身份、配置清单及 S99 开机链接。
同次构建的包由既有 matching-packages 流程导出，并由本次构建索引签名覆盖。
固件 artifact 另带 `fan-package-source.json` 以便离线核对原包来源。

本地与 CI 检查命令：

```sh
python3 scripts/verify-fan-package.py
python3 -m unittest discover -s tests -p 'test_fan_package.py' -v
sh -n package/luci-app-m28c-fan/files/usr/sbin/m28c-fan-control
node --check package/luci-app-m28c-fan/files/www/luci-static/resources/view/m28c-fan/settings.js
```

这些检查以及构建后 rootfs 检查均不连接或修改在线设备。
