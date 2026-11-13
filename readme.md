私用Clash覆写模块和配置

采集日期：2026-11-13；来源为本机路由器当前的 OpenClash 0.48.002 文件。

每个文件内都附有简短的“文件作用”注释。目录名与 `/etc/openclash/` 下的来源目录对应。

## 覆写模块

| 文件 | 文件作用 | 采集时状态 | 顺序 |
|---|---|---|---|
| [DNS-fullauto](overwrite/DNS-fullauto) | 集中管理 Mihomo DNS 基础参数，保留 LuCI 配置的 Nameserver-Policy。 | 已登记启用 | -1 |
| [Google_Play](overwrite/Google_Play) | 为 Google Play 下载相关域名指定境外 DNS，避免解析到不适用的国内 CDN。 | 仅保留文件，未登记启用 | — |
| [Tailscale](overwrite/Tailscale) | 将 ts.net 域名交给 Tailscale MagicDNS 解析。 | 仅保留文件，未登记启用 | — |
| [Tiktok-selector](overwrite/Tiktok-selector) | 添加 TikTok 手动策略组与分流规则，并固定策略组显示顺序。 | 已登记启用 | 6 |
| [WiFi_Calling](overwrite/WiFi_Calling) | 为 WiFi Calling 的 ePDG、IKE 与 NAT-T 流量添加 UDP 代理规则。 | 仅保留文件，未登记启用 | — |
| [Youtube-autofallback](overwrite/Youtube-autofallback) | 为 YouTube 添加美国节点故障切换组，并挂接到视频策略组。 | 已登记启用 | 5 |
| [clash-fullauto](overwrite/clash-fullauto) | 补充规则源与分流规则，统一 AI、视频、下载及国内直连策略。 | 已登记启用 | 4 |
| [default](overwrite/default) | 保留 OpenClash 覆写参数与用法示例，供创建模块时参考。 | 仅保留文件，未登记启用 | — |

已登记的上述模块均匹配 `超级自动挡.yaml`。顺序数字越大越先处理。`default` 含有效的 `[General]` 示例参数，不能把它当作当前运行设置直接启用。

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
