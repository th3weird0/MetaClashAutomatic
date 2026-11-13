私用Clash覆写模块和配置

## 当前订阅模板

- [MetaFullauto_Full.ini](MetaFullauto_Full.ini)：个人 Mihomo 订阅转换模板，未识别流量默认进入手动代理，直连仍可主动选择。
- [Aethersailor 规则 重度分流版.ini](Aethersailor%20规则%20重度分流版.ini)：上游原文，保留用于对照。
- [本地检查与恢复](tools/README.md)：真实转换、关键分流、空组拒绝、保存选择迁移、隐私审查及最近三份本地快照。

自动维护的远程规则集统一每 48 小时更新。AI 稳定出口首版包含美/日/新加坡候选，尚待实际业务验证与精简；Netflix 名称尾缀只是候选筛选，204 不代表播放解锁。

仓库采用编辑、验证、审查后本地提交，远程推送单独决定。修改文件不代表已在路由器应用，也不会自动重启核心。

## 初始归档来源

采集日期：2026-11-13；来源为本机路由器当前的 OpenClash 0.48.002 文件。

每个文件内都附有简短的“文件作用”注释。目录名与 `/etc/openclash/` 下的来源目录对应。

## 覆写模块

| 文件 | 文件作用 | 采集时状态 | 顺序 |
|---|---|---|---|
| [DNS-fullauto](overwrite/DNS-fullauto) | 集中管理 Mihomo DNS 基础参数，保留 LuCI 配置的 Nameserver-Policy。 | 已登记启用 | -1 |
| [Google_Play](overwrite/Google_Play) | 为 Google Play 下载相关域名指定境外 DNS，避免解析到不适用的国内 CDN。 | 仅保留文件，未登记启用 | — |
| [Tailscale](overwrite/Tailscale) | 将 ts.net 域名交给 Tailscale MagicDNS 解析。 | 仅保留文件，未登记启用 | — |
| [Tiktok-selector](overwrite/Tiktok-selector) | 保留 TikTok 规则并接入已有社交媒体组，保留显示顺序处理。 | 已登记启用 | 6 |
| [WiFi_Calling](overwrite/WiFi_Calling) | 为 WiFi Calling 的 ePDG、IKE 与 NAT-T 流量添加 UDP 代理规则。 | 仅保留文件，未登记启用 | — |
| [clash-fullauto](overwrite/clash-fullauto) | 补充规则源与分流规则，统一 AI、视频、下载及国内直连策略。 | 已登记启用 | 4 |
| [default](overwrite/default) | 保留 OpenClash 覆写参数与用法示例，供创建模块时参考。 | 仅保留文件，未登记启用 | — |
| [PrivateNet](overwrite/PrivateNet) | 将 private 域名与上游 28 个保留网段固定 DIRECT，优先于可切换分组。 | 新增，未部署 | 最后应用 |
| [MetaFullautoSafety](overwrite/MetaFullautoSafety) | 自动组与选择器候选为空时明确 REJECT，避免回退直连。 | 新增，未部署 | 规则合并后 |

已登记的上述模块均匹配 `超级自动挡.yaml`。顺序数字越大越先处理。`default` 含有效的 `[General]` 示例参数，不能把它当作当前运行设置直接启用。

旧模块已在本地按新模板名称对齐，保留全部旧分流规则与规则源；表中登记状态仅指采集时的路由器，尚未部署修改后的文件。TikTok 改为更新已有社交媒体组，避免同名组；原先依赖底层配置的 GoogleFCM、GoogleCN 补齐现成来源引用。WiFi Calling 的出口固定为手动选择，需选择支持 UDP 的节点。按后续决定移除旧 `Youtube-autofallback` 模块，其美国 fallback 不加入新模板；国外媒体复用地区 url-test。路由器上的旧模块登记尚未删除。

启用旧模块仍会覆盖新 INI 的部分分流：旧 Steam 非下载规则指向手动选择，旧 Google provider 会覆盖同名来源。按用户选择保留这些规则行为。`PrivateNet` 最后应用以固定私网直连；空组保护在所有组添加完毕后应用。移除 YouTube 模块前的合并配置通过同版内核；移除后只做静态合并、引用和候选检查，未重新运行内核，也未在路由器执行 General、Shell 或 Ruby 段。

## custom_scripts 与辅助配置

`custom/openclash_custom_overwrite.sh` 是自定义覆写入口，依赖同目录下的 `provider_aliases.rb`。恢复时需保留路由器上的原始目标路径；脚本可执行权限沿用本机设置。

| 文件 | 文件作用 |
|---|---|
| [openclash_custom_chnroute6_pass.list](custom/openclash_custom_chnroute6_pass.list) | 列出 IPv6 绕过大陆规则中的例外域名。 |
| [openclash_custom_chnroute_pass.list](custom/openclash_custom_chnroute_pass.list) | 列出 IPv4 绕过大陆规则中的例外域名。 |
| [openclash_custom_domain_dns.list](custom/openclash_custom_domain_dns.list) | 自定义域名 DNS 列表入口，目前仅保留示例注释。 |
| [openclash_custom_domain_dns_policy.list](custom/openclash_custom_domain_dns_policy.list) | 按规则集与域名分配国内或境外 DNS，供 Nameserver-Policy 加载。 |
| [openclash_custom_fake_filter.list](custom/openclash_custom_fake_filter.list) | 列出不使用 Fake-IP 的域名与服务例外。 |
| [openclash_custom_fallback_filter.yaml](custom/openclash_custom_fallback_filter.yaml) | 定义 DNS 回退判定中的 IP 网段和域名过滤条件。 |
| [openclash_custom_firewall_rules.sh](custom/openclash_custom_firewall_rules.sh) | 保留自定义防火墙脚本入口，目前仅记录启动提示日志。 |
| [openclash_custom_hosts.list](custom/openclash_custom_hosts.list) | 静态 Hosts 映射入口，目前为注释示例。 |
| [openclash_custom_localnetwork_ipv4.list](custom/openclash_custom_localnetwork_ipv4.list) | 定义局域网及保留 IPv4 网段和直连域名例外。 |
| [openclash_custom_localnetwork_ipv6.list](custom/openclash_custom_localnetwork_ipv6.list) | 定义局域网及保留 IPv6 网段。 |
| [openclash_custom_overwrite.sh](custom/openclash_custom_overwrite.sh) | custom_scripts 自定义覆写入口，调用 Provider 别名处理脚本。 |
| [openclash_custom_proxy_server_dns_policy.list](custom/openclash_custom_proxy_server_dns_policy.list) | 为代理节点域名按国内与境外分类指定 DNS。 |
| [openclash_custom_rules.list](custom/openclash_custom_rules.list) | 自定义优先匹配分流规则入口，目前主要为注释示例。 |
| [openclash_custom_rules_2.list](custom/openclash_custom_rules_2.list) | 自定义后置匹配规则与旧 Script 入口，目前为示例。 |
| [openclash_custom_sniffer.yaml](custom/openclash_custom_sniffer.yaml) | 配置 TLS、HTTP、QUIC 嗅探及强制和跳过域名。 |
| [provider_aliases.rb](custom/provider_aliases.rb) | 按 LuCI 订阅名称设置 Provider 别名，并同步缓存和界面订阅元数据。 |

这些辅助文件的存在不代表对应功能全部启用；是否读取由 OpenClash 的开关和覆写流程决定。

## 归档说明

- 仓库文件保留源配置和脚本逻辑，补充用途注释；脚本注释示例中的密码值改为明确占位符。
- 提交前审查全部待上传文件、注释、示例、README、路径和提交说明，并复核认证字段、URL 和高熵字符串。
- 这里归档模块与辅助文本，未包含路由器完整配置、订阅缓存或凭据备份。
- 本次未在路由器上部署或重启任何服务。
