# 用户检查工具

这个目录提供三种操作：**转换订阅得到候选 YAML、检查候选配置、读取正在运行的 Mihomo 状态**。检查逻辑由原维护工具 `tools/policy.py` 提取共享，日常检查可以直接对接自己的配置与控制器。

## 安装与独立使用

安装 Python 3.10+，在仓库根目录运行：

```powershell
python -m pip install -r tool-for-testing/requirements.txt
python tool-for-testing/check.py --help
```

独立使用时，复制整个 `tool-for-testing/` 目录，在该目录运行：

```powershell
python -m pip install -r requirements.txt
python check.py --help
python test_checks.py
```

依赖为 PyYAML。所有命令以前台方式运行，输出 JSON；通过时退出码为 `0`，失败时为 `1`，参数用法错误由 argparse 返回 `2`。以下示例均从仓库根目录运行。

## 1. 转换订阅

准备已经运行的 SubConverter-Extended 后端，将示例地址替换为自己的 `/sub` 服务：

```powershell
python tool-for-testing/check.py convert --backend "https://你的转换器域名/sub" --output .local/candidate.yaml
```

提示后粘贴订阅链接，回车确认。多条链接用 `|` 连接。转换请求使用 `target=clash`、`expand=false` 和主模板 URL，生成原生远程资源配置。后端接收订阅内容，输出 YAML 存在本地；成功输出的范围为 `conversion-download`。

可选参数 `--template URL` 指定另一份可访问的模板。自动化场景可通过 `METACLASH_SUBSCRIPTION` 环境变量提供订阅。再次执行时换一个新的输出文件名。

## 2. 检查 YAML

```powershell
python tool-for-testing/check.py config .local/candidate.yaml --data-dir .local/test-data --fetch-providers
```

`--data-dir` 表示内核数据目录，配置中 `proxy-providers.*.path` 的相对路径以此目录为起点。`--fetch-providers` 下载缺少的 HTTP 节点缓存，保存路径限定在该目录内；已经存在的文件继续使用。每个下载限 16 MiB，下载内容须为包含 `proxies` 列表的 YAML。带自定义 `header` 的 HTTP Provider 由客户端准备缓存。`inline` Provider 直接读取配置中的节点；`file` Provider 使用已准备好的文件。

只使用现有缓存时运行：

```powershell
python tool-for-testing/check.py config .local/candidate.yaml --data-dir .local/test-data
```

成功输出含 `"passed": true`、`"scope": "config-structure"`，以及 `eligible_netflix`、`eligible_ai`、节点数、组数、自动组数、缓存下载数、设置 `empty-fallback: REJECT` 的组数和漏网之鱼的初始选择状态。

检查范围：

- YAML 顶层类型、重复字段、重复节点 / 组名及名称冲突。
- 直接节点、inline / HTTP / file Provider 缓存，`use`、`include-all*`、名称筛选与排除筛选。
- 分组引用、循环引用和自动组实际节点数量；`REJECT` 等内置出口单独计数。
- 本模板必要分组、Netflix 自动组的名称筛选与实际候选、AI 服务组对独立池的引用。
- 路由规则、子规则以及 DNS 策略中的命名 `RULE-SET` 引用。

Python 正则用于本模板的名称筛选。Provider 的 `filter` / `exclude-filter` / `override` 变换和分组 `exclude-type` 会返回需要内核核验的错误。目标内核的 `-t` 负责其原生正则、完整规则语法、GEO/MRS 解析和其他配置字段，见[主 README 第 3 节](../readme.md#3-生成并检查第一份配置)。

更新节点缓存时，使用新的数据目录，例如 `--data-dir .local/test-data-next --fetch-providers`；这样便于保留上一份检查输入。HTTP 节点 URL 及其生成缓存含订阅信息，保存在自己的电脑即可。

## 3. 日常运行检查

控制器使用客户端实际的 `http(s)://主机:端口` 地址。例如本机为 `http://127.0.0.1:9090`，检查路由器时填写路由器的实际地址。

PowerShell 临时输入密钥：

```powershell
$credential = Get-Credential -UserName 'mihomo' -Message '在密码框输入控制器密钥'
$env:MIHOMO_API_SECRET = $credential.GetNetworkCredential().Password
python tool-for-testing/check.py state --controller http://127.0.0.1:9090
Remove-Item Env:MIHOMO_API_SECRET
Remove-Variable credential
```

Linux/macOS 在 Bash 中运行：

```bash
read -r -s -p 'Controller secret: ' MIHOMO_API_SECRET
export MIHOMO_API_SECRET
python tool-for-testing/check.py state --controller http://127.0.0.1:9090
unset MIHOMO_API_SECRET
```

密钥为空的控制器可直接执行检查命令。

工具只读取 `/version`、`/configs`、`/proxies`、`/providers/rules` 四个 GET 接口，连接保持系统代理旁路，认证使用 `MIHOMO_API_SECRET`。重定向会被拒绝，便于使认证信息留在指定控制器。

| `checks` 字段 | 通过条件 |
|---|---|
| `rule_mode` | 运行模式为 `rule`。 |
| `netflix_candidates` / `ai_candidates` | 对应池展开后有实际节点。 |
| `netflix_healthy_nodes` / `ai_healthy_nodes` | 对应池至少一个节点的内核健康状态为 `alive: true`。 |
| `automatic_groups_have_nodes` | 自动组展开后都有实际节点。 |
| `rule_providers_loaded` | 规则集列表存在，且每个 `ruleCount` 都大于零。 |
| `catchall_manual` | 漏网之鱼当前选择手动代理。 |
| `ai_service_uses_pool` | AI 服务当前选择 AI 稳定出口。 |

`passed` 汇总上述基线。`ai_fixed` 单独展示内核返回的固定节点状态；按实际 AI 请求结果选择并固定节点。自己主动采用其他漏网出口时，可结合 `catchall_manual` 单项结果理解当前设置。

工具输出计数和固定诊断码。配置原文、订阅链接、节点名及控制器密钥保持在本地输入中。

## 失败项处理

| 提示或字段 | 具体操作 |
|---|---|
| `OUTPUT_ALREADY_EXISTS` | 给本次候选换一个新文件名。 |
| `PROVIDER_CACHE_MISSING` / `PROVIDER_CACHE_PATH_REQUIRED` | HTTP Provider 添加 `--fetch-providers`；file Provider 按配置的 path 放入节点 YAML。 |
| `CACHE_PATH_MUST_BE_RELATIVE` / `CACHE_PATH_ESCAPES_DATA_DIR` | 将缓存 path 改为数据目录内的相对路径，例如 `proxy_provider/nodes.yaml`。 |
| `AUTOMATIC_GROUP_HAS_ZERO_NODES`、`NETFLIX_HAS_ZERO_NODES`、`AI_HAS_ZERO_NODES` | 自动组错误的 `group_number` 指向 `proxy-groups` 数组中从 1 起算的出错组。核对节点命名和筛选，补齐节点或调整该组及引用，再转换检查。Netflix 标签需位于名称末尾。 |
| `OUTBOUND_REFERENCE_MISSING` / `METACLASH_GROUP_MISSING` | 使用本仓库模板重新生成，核对自己修改过的组名与引用。 |
| `CYCLIC_GROUP_REFERENCE` | 删除导致两个组相互引用的那条引用，保留单向的出口层级。 |
| `ROUTING_RULE_PROVIDER_MISSING` / `DNS_RULE_PROVIDER_MISSING` | 补齐命名规则源，或将引用改成配置中已有的源。 |
| `*_REQUIRES_CORE_CHECK` | 用实际目标内核及对应数据执行 `-t`，并在运行后使用 `state` 检查实际展开。 |
| `PROVIDER_HEADERS_REQUIRE_CLIENT_DOWNLOAD` | 用客户端按 Provider 的 header 下载节点缓存，再省略 `--fetch-providers` 执行检查。 |
| `CONTROLLER_AUTH_FAILED` | 从客户端设置重新取得控制器密钥，更新环境变量后再试。 |
| `INVALID_CONTROLLER_URL` | 填写控制器根地址，例如 `http://127.0.0.1:9090`，认证放入环境变量。 |
| `INPUT_OR_CONNECTION_ERROR` | 先确认文件路径、YAML 格式、后端 / 控制器可达性与证书；在客户端日志中查看详细错误。 |
| 健康节点项为 `false` | 在仪表盘触发对应池的健康检查，等待完成后再运行工具；选择健康候选做业务验收。 |
| `rule_providers_loaded: false` | 在客户端日志检查规则下载，完成规则集加载后再查。 |
| `catchall_manual: false` / `ai_service_uses_pool: false` | 在仪表盘分别选择手动代理、AI 稳定出口；工具会保留用户当前选择。 |

`config` 使用已有缓存且只读输入，添加 `--fetch-providers` 后会写入缺少的节点缓存；`convert` 写一个新候选文件；`state` 读取已有服务状态。所有操作的验证结果都带 `scope`，便于区分完成了哪一步。

## 4. 业务验收与工具自检

1. Netflix：选择候选后实际播放视频，确认播放成功与所需内容可见。
2. AI：登录目标服务发送一次真实请求，确认结果正常，再固定健康出口。
3. 国内与局域网：分别访问常用国内站点和自己的局域网服务。

连通性探测负责延迟与健康状态，上述操作负责实际业务验收。

运行工具自检：

```powershell
python tool-for-testing/test_checks.py
```

自检使用临时 YAML、合成节点、本地模拟转换器 / 控制器，覆盖成功、失败、独立复制、原文件保持、HTTP 请求类型、认证和缓存路径边界。测试结束后临时目录与模拟 HTTP 服务清理完毕。真实转换器、Mihomo 核心及实际业务按前述步骤分别验证。

实现依据：[Mihomo 代理组字段](https://wiki.metacubex.one/config/proxy-groups/)、[控制器 API](https://wiki.metacubex.one/api/)、仓库中已使用的 `tools/check-local.py` 请求格式。
