# MetaClashAutomatic

一套面向 **Mihomo / OpenClash** 的订阅转换模板和可选覆写模块。把自己的节点订阅与本仓库模板交给转换器，即可生成包含 AI、Netflix、社交媒体、开发者服务、下载 CDN 等分组的 Mihomo YAML 配置。

第一次使用，按本文完成：**准备环境 → 生成配置 → 检查候选 → 导入客户端 → 选择出口 → 验收业务**。已有配置的用户可以直接阅读[检查与日常使用](#检查与日常使用)。

## 1. 准备哪些东西

| 项目 | 要求与获取方式 |
|---|---|
| 节点订阅 | 准备自己的订阅链接，转换器通过它取得节点。 |
| 客户端 | 使用 Mihomo，或使用 Mihomo 内核的 OpenClash；内核需支持 MRS、GEOSITE、逻辑规则和 `empty-fallback`。 |
| 订阅转换器 | 使用 [SubConverter-Extended](https://github.com/Aethersailor/SubConverter-Extended)，本仓库转换流程采用 v1.10.5 的请求格式。按上游快速开始部署，取得以 `/sub` 结尾的服务地址。 |
| 检查环境 | 电脑安装 Python 3.10+；Windows 示例使用 PowerShell。检查工具支持 Windows、Linux 和 macOS。 |
| GEO 数据 | 在客户端更新 GeoSite、GeoIP 数据，供模板中的 `GEOSITE` / `GEOIP` 规则使用。 |

转换器会接收订阅链接。使用自己部署或已确认可信的服务，候选配置、节点缓存与检查记录保存在本地 `.local/`。

通过 Git 下载仓库：

```powershell
git clone https://github.com/th3weird0/MetaClashAutomatic.git
cd MetaClashAutomatic
python -m pip install -r tool-for-testing/requirements.txt
```

也可以点击 GitHub 的 **Code → Download ZIP**，解压后在仓库目录打开终端，再执行安装依赖的命令。Linux/macOS 可先用 `python3 -m venv .local/venv` 创建环境，再执行 `source .local/venv/bin/activate`，后续统一使用 `python`。

## 2. 选对文件

| 路径 | 用途 | 首次使用时怎么选 |
|---|---|---|
| [MetaFullauto_Full.ini](MetaFullauto_Full.ini) | 主订阅转换模板 | 将它的 URL 填入转换器的模板字段，生成客户端使用的 YAML。 |
| [tool-for-testing/](tool-for-testing/README.md) | 用户检查工具 | 用于生成候选、检查配置结构、读取运行状态；整个目录可独立复制使用。 |
| [overwrite/](overwrite/) | OpenClash 覆写模块 | 按第 5 节选择需要的模块。 |
| [custom/](custom/) | DNS 策略、嗅探、脚本等辅助配置 | 已使用对应 OpenClash 功能时，再按第 6 节引入。 |
| [tools/](tools/README.md) | 维护者的保护处理、完整回归与快照工具 | 本文使用其中的候选保护命令；开发与恢复流程见目录文档。 |
| [Aethersailor 规则 重度分流版.ini](Aethersailor%20规则%20重度分流版.ini) | 上游模板对照 | 用来理解和比较模板来源。 |

主模板地址：

```text
https://raw.githubusercontent.com/th3weird0/MetaClashAutomatic/main/MetaFullauto_Full.ini
```

## 3. 生成并检查第一份配置

### 第一步：生成候选 YAML

先启动自己的 SubConverter-Extended。把下面的 `https://你的转换器域名/sub` 替换为实际服务地址：

```powershell
python tool-for-testing/check.py convert --backend "https://你的转换器域名/sub" --output .local/candidate.yaml
```

工具会提示 `Subscription URL (hidden):`，粘贴订阅链接并回车。输入内容在终端中隐藏。多条订阅用 `|` 连接后一起粘贴。

成功结果包含 `"passed": true` 和 `"scope": "conversion-download"`，生成文件为 `.local/candidate.yaml`。再次转换时使用一个新的输出文件名。该步骤请求 `target=clash&expand=false`，保留原生远程资源结构。

### 第二步：检查节点与策略组

```powershell
python tool-for-testing/check.py config .local/candidate.yaml --data-dir .local/test-data --fetch-providers
```

`--fetch-providers` 会把缺少的 HTTP 节点缓存下载到指定数据目录；已有缓存继续使用。检查覆盖策略组引用、循环、Netflix / AI 候选、自动组空池，以及路由和 DNS 对规则源的引用。

成功结果包含 `"passed": true`、`eligible_netflix`、`eligible_ai` 和 `groups` 等计数。自动组都需要实际节点；某地区、家宽或低倍率组为空时，按工具文档中的[失败项处理](tool-for-testing/README.md#失败项处理)调整订阅或模板筛选，再重新转换。

### 第三步：生成带保护的待用文件

在完整仓库中运行：

```powershell
python tools/policy.py prepare .local/candidate.yaml --data-dir .local/test-data --output .local/checked.yaml --first-activation
```

该命令生成 `.local/checked.yaml`，加入固定私网直连规则，给策略组设置 `empty-fallback: REJECT`，并将首次激活的 `profile.store-selected` 设为 `false`。漏网流量的初始出口为手动代理。

### 第四步：交给目标内核解析

使用将要运行此配置的 Mihomo 版本检查。把示例内核路径改成自己的实际路径：

```powershell
$dataDir = (Resolve-Path .local/test-data).Path
$configFile = (Resolve-Path .local/checked.yaml).Path
& 'C:\你的Mihomo目录\mihomo.exe' -t -d $dataDir -f $configFile
$LASTEXITCODE
```

数据目录需具备该内核使用的 GEO 数据，远程规则源需能够取得。内核退出码为 `0` 后，进入导入步骤。工具的 `config-structure` 结果对应结构与候选检查，内核 `-t` 对应实际格式与规则解析，两项分别验收。

## 4. 导入客户端并选择出口

### OpenClash

1. 在 **服务 → OpenClash → 配置管理** 下载并保存当前配置，作为回退文件。
2. 上传 `.local/checked.yaml`，给它起一个容易识别的名字，例如 `MetaFullauto.yaml`。OpenClash 的上传流程会选择新配置；切换配置会重启核心，安排在可以短暂断网的时间操作。
3. 打开 **运行状态 → 仪表盘**，按下面的表格确认分组选择。
4. 完成 Netflix 播放和 AI 请求验收。以后需要保存手动选择时，在配置中设置 `profile.store-selected: true`，应用后再检查一次仪表盘。

### 其他 Mihomo 客户端

将 `.local/checked.yaml` 作为本地配置导入，启用规则模式，更新 GEO 数据，再打开客户端的代理组面板完成同样的选择。

| 分组 | 初次使用的操作 | 后续用途 |
|---|---|---|
| `🚀 手动选择` | 选择可用的地区组或节点 | 通用代理出口。 |
| `🐟 漏网之鱼` | 选择 `🚀 手动选择` | 承接最终匹配流量；需要直连时可主动选择全球直连。 |
| `🤖Ai服务` | 保持 `🤖 AI稳定出口` | AI 请求统一进入独立候选池。 |
| `🤖 AI稳定出口` | 从美、日、新加坡候选中选择一个通过实际 AI 请求的节点 | 日常固定该健康出口，故障时在池内切换。 |
| `🎥 Netflix` | 选择 `🎥 Netflix自动`，再实际播放验收 | Netflix 候选根据节点名称末尾的 Netflix / NF / 奈飞等标签筛选。 |
| `🇨🇳 本地\|国内直连`、下载 CDN、OneDrive | 从默认直连开始，按实际访问情况调整 | 国内服务、共享下载与云盘分别管理。 |
| 国外媒体、社交媒体、开发者服务 | 选择通用代理、地区组或节点 | YouTube 属于国外媒体，TikTok 属于社交媒体。 |

地区及其他自动组每 300 秒做连通性测试，远程规则集的更新间隔为 48 小时。连通性和延迟由探测 URL 检查；Netflix 以实际播放为验收结果，AI 以登录后的真实请求为验收结果。

### 通过 OpenClash 定期转换订阅

第一份配置验收后，可在 **配置订阅 → 添加 / 编辑订阅** 设置自动转换：

| 字段 | 填写内容 |
|---|---|
| 配置名称、订阅地址 | 自己的配置名称与订阅链接。 |
| 在线订阅转换 | 开启。 |
| 转换地址 | 自己的 SubConverter-Extended `/sub` 地址。 |
| 模板名称 | 选择“自定义模板”。 |
| 自定义模板 URL | 填写第 2 节的主模板地址。 |
| 使用规则集（Use Rule Provider） | 开启，使用原生远程规则。 |
| 节点类型后缀（Append Node Type） | 关闭，保持 Netflix 标签处于节点名称末尾。 |
| 自动更新 | 首次检查完成后，再设置适合自己的更新周期。 |

自动转换生成的新配置配合下一节的 `PrivateNet`、`MetaFullautoSafety` 模块使用。每次更换订阅、筛选条件或转换器后，对新候选重新执行配置检查，并确认运行中的选择状态。

## 5. 按需要安装覆写模块

OpenClash 的入口为 **运行状态页顶部 → 覆写模块 →「+」→ File**。填写模块名、匹配配置文件和顺序，创建后复制对应文件的完整内容，保存并启用。匹配配置填写自己的文件名，例如 `MetaFullauto.yaml`；模块在下一次应用配置时合并。

| 模块 | 使用场景与设置 |
|---|---|
| [MetaFullautoSafety](overwrite/MetaFullautoSafety) | 为自动组和选择器设置空候选时的 `REJECT`。使用模板时启用。 |
| [PrivateNet](overwrite/PrivateNet) | 将 private 域名和 28 个保留网段固定直连。使用模板时启用，并安排在其他规则模块之后应用。 |
| [DNS-fullauto](overwrite/DNS-fullauto) | 使用本仓库的 Redir-Host DNS 方案：IPv4、7874 端口、DoH 与 `respect-rules`。需要这套 DNS 参数时启用。 |
| [clash-fullauto](overwrite/clash-fullauto) | 补充 24 条独立业务例外，按自己的访问需求选择。 |
| [WiFi_Calling](overwrite/WiFi_Calling) | 使用 WiFi Calling，且已有支持 UDP 的代理节点时启用；手动选择组需指向该节点。 |
| [Google_Play](overwrite/Google_Play) | 为 Google Play 下载域名使用境外 DNS。 |
| [Tailscale](overwrite/Tailscale) | 已部署 Tailscale MagicDNS 时，为 `ts.net` 使用该解析服务。 |
| [default](overwrite/default) | 编写自己模块时的格式示例。 |
| [Tiktok-selector](overwrite/Tiktok-selector) | 历史兼容空文件；当前 TikTok 路由由模板的社交媒体组承担。 |

模块顺序数字越大越早处理。只启用两个保护模块时，可给 `MetaFullautoSafety` 设置 `1`、`PrivateNet` 设置 `0`；增加其他模块时，让它们的顺序大于 `PrivateNet`，使私网规则最后前置合并。

## 6. 需要自定义 DNS 或脚本时

`custom/` 对应路由器的 `/etc/openclash/custom/`。先保存原文件副本，再引入需要的单个配置，并在 OpenClash 中开启对应功能。

| 文件或类别 | 操作入口与用途 |
|---|---|
| `openclash_custom_domain_dns_policy.list` | **覆写设置 → DNS 设置 → 自定义 Nameserver-Policy**，按 GEO 分类和显式域名分配解析服务。 |
| `openclash_custom_fake_filter.list`、`openclash_custom_fallback_filter.yaml` | 分别用于 Fake-IP 过滤与 DNS fallback 过滤；按所选 DNS 模式使用。 |
| `openclash_custom_sniffer.yaml` | 用于自定义 TLS、HTTP、QUIC 嗅探。 |
| `openclash_custom_rules.list`、`openclash_custom_rules_2.list` | 用于自定义前置、后置分流规则。 |
| `openclash_custom_chnroute*_pass.list`、`openclash_custom_localnetwork_*.list` | 用于大陆绕过例外与局域网网段。 |
| `openclash_custom_overwrite.sh` + `provider_aliases.rb` | 需要按 LuCI 订阅名称处理 Provider 别名时，将两个文件放在同一 custom 目录，并保留入口脚本执行权限。 |

首次使用主模板时，先完成第 3、4 节；需要某项功能时再添加相应辅助文件。

## 检查与日常使用

可直接使用 [tool-for-testing/](tool-for-testing/README.md)，也可以把整个目录复制到另一台装有 Python 的电脑。

| 时机 | 命令 | 能确认的内容 |
|---|---|---|
| 首次转换、更换订阅后 | `python tool-for-testing/check.py config .local/candidate.yaml --data-dir .local/test-data --fetch-providers` | 节点缓存可读、分组引用完整、自动组有节点、Netflix / AI 有候选、DNS 规则源存在。 |
| 检查已有缓存 | `python tool-for-testing/check.py config .local/candidate.yaml --data-dir .local/test-data` | 使用本地文件进行同样的结构检查。 |
| 日常检查运行状态 | `python tool-for-testing/check.py state --controller http://127.0.0.1:9090` | 规则模式、运行分组的候选和健康计数、规则集加载、漏网之鱼与 AI 服务的当前选择。 |
| 检查工具本身 | `python tool-for-testing/test_checks.py` | 使用合成文件和本地模拟控制器验证工具行为。 |

控制器地址填写客户端实际的 API 地址。在 OpenClash 中查看 **插件设置 → 外部控制**；在电脑访问路由器时，将示例中的 `127.0.0.1` 换为实际路由器地址。认证密钥通过环境变量读取，PowerShell 可以这样临时输入：

```powershell
$credential = Get-Credential -UserName 'mihomo' -Message '在密码框输入控制器密钥'
$env:MIHOMO_API_SECRET = $credential.GetNetworkCredential().Password
python tool-for-testing/check.py state --controller http://127.0.0.1:9090
Remove-Item Env:MIHOMO_API_SECRET
Remove-Variable credential
```

`state` 读取运行中的四个 GET 接口，输出通过项与失败项；服务和分组选择保持原状。看到 `"passed": true` 后，再按业务完成：Netflix 实际播放一段视频，AI 登录后发送一次请求，常用国内网站与局域网服务各访问一次。

完整的参数、退出码、失败项处理、缓存更新方法和验证边界见[工具使用说明](tool-for-testing/README.md)。维护者的真实转换回归、状态迁移、隐私扫描与恢复快照见 [tools/README.md](tools/README.md)。

## 来源与参考

- 模板基线：[Aethersailor / Custom_OpenClash_Rules](https://github.com/Aethersailor/Custom_OpenClash_Rules)。
- 远程规则引用：MetaCubeX/meta-rules-dat、Sukka、blackmatrix7/ios_rule_script、ACL4SSR 及 Aethersailor 的现成规则。
- 操作字段依据：[OpenClash 订阅编辑器源码](https://github.com/vernesong/OpenClash/blob/dev/luci-app-openclash/luasrc/model/cbi/openclash/config-subscribe-edit.lua)、[Mihomo 代理组文档](https://wiki.metacubex.one/config/proxy-groups/)、[Mihomo 控制器 API](https://wiki.metacubex.one/api/)。
