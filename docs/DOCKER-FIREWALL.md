# Docker 与 firewall4

锁定 Docker 29.6.1 默认通过 legacy iptables 建立规则，导致 LuCI 提示旧版规则与 nftables 混用。本构建把 Docker 默认后端改为原生 nftables，保留 `iptables=1` 和 `ip6tables` 原有含义；没有通过关闭 Docker 防火墙或隐藏 LuCI 提示绕过问题。

`scripts/customize-docker.py` 在扫描包元数据之前修改锁定 `packages/utils/dockerd`。修改前核对源文件 SHA256 和唯一锚点，重复执行结果一致，上游漂移会停止构建。包版本为 `29.6.1-1.m28c1`，匹配 IPK 与镜像都安装同一套启动脚本、首启策略、DNS helper 和 nft include，避免只覆盖镜像而遗漏匹配软件包。

## 默认网络策略

首次启动创建默认 `docker0` 对应的 network/zone（不存在时），随后建立三项 firewall4 策略：

- `m28c_docker_wan_guard`：WAN → Docker 拒绝，作为普通转发规则排列在 fw4 自动 DNAT 放行之前。
- `m28c_docker_to_wan`：Docker → WAN 转发。
- `m28c_docker_lan_published`：在 `forward_lan` 前部仅放行 `oifname docker0` 且 `ct status dnat` 的流量，即 LAN 访问显式发布的容器端口。

全新配置在系统首次启动、firewall 与 dockerd 服务之前生成 UCI 策略，不需要额外重载。保留升级且缺少后端设置时，迁移延后到 dockerd 的 S99 启动、fstab 挂载后，检查真实数据根，再同步运行 `fw4 check` 和 `fw4 reload`，确认策略加载成功后才启动可能自动恢复的容器；失败时保留待处理脚本并拒绝启动 Docker。锁定 OpenClash 同为 S99，排序在 dockerd 之后。正常后续启动不会重复这一迁移重载，也不执行 `nft flush ruleset` 或清空 legacy 表。已存在的同名策略，包括用户禁用或修改后的值，保持原样；已有 Docker zone 的输入策略也不覆盖。若 `network.docker` 指向非默认设备，保留原配置并要求手动设置对应策略。

## 配置兼容

普通 DockerMan/UCI 参数、代理、data-root 和自定义 DNS 都继续使用原配置生成路径。有效的 `alt_config_file` 按原逻辑直接使用，自动默认策略不会覆盖该模式。保留升级的 #110 配置若缺少 `firewall_backend`，首启会在默认阻断配置、LAN/WAN zone 和网络满足条件时补为 `nftables`，同时建立策略；显式选择的其他后端保持不变。检测到已有非空 Swarm 数据、非默认设备、未转换的 legacy 限制或缺失 LAN/WAN zone 时不擅自迁移，记录日志，等待用户选择支持的后端和策略。

仅在原生 nftables 且用户未配置 `globals.dns` 时生成 DNS：优先取用户 `bip` 的 IPv4 网关；否则取已存在 `docker0` 的 IPv4 地址；首次启动桥尚不存在则使用当前 LAN 地址，避免猜测 Docker 自动分配的网段。找不到合法地址时不猜测公共 DNS，记录日志并要求明确配置。此逻辑不写死设备的 LAN 地址、UUID 或 `172.17.0.1`。若调整 dnsmasq 监听范围，仍需确保选中的地址可供容器查询。

旧 `blocked_interfaces` 和 `extra_iptables_args` 是 legacy helper 参数。默认 `docker0` / `wan` 已由 fw4 策略承接；其他阻断接口、设备或附加 iptables 参数不会悄悄忽略，nft 模式会拒绝启动并记录原因。应先将其意图转换为 fw4 策略，再移除对应旧参数。init 的 start/reload 路径会传播配置失败，不会继续启动残留配置。

## 验证与范围

回归测试执行实际补丁后的 shell 函数，覆盖普通参数/代理、自定义 DNS/bip、当前桥及 LAN 回退、备用 JSON、旧后端、拒绝不支持的旧规则、首启顺序、幂等及保留用户策略；另核对匹配 IPK 安装路径、版本和源文件漂移拒绝。

#110 设备端同类修复已实际验证容器 HTTPS、直接出网、LAN 发布端口和 legacy 有效规则清零；本次构建改动仍须以新构建实际镜像/IPK验收为准，不能将原设备测试当成新镜像验证。Docker 29 的原生 nftables 后端官方仍标为实验性，不支持 Swarm。自动策略仅覆盖默认 Docker bridge，未声称支持所有自定义 bridge、IPv6 容器或任意用户负载。

使用同次构建的 dockerd IPK 会保留本补丁；从公共补充源升级为上游 dockerd 包仍可能替换定制启动脚本。不要用全量升级混用其他构建的软件包。
