# 本地检查、候选保护与恢复

本仓库采用：编辑 → 本地检查 → 逐行审查 → 本地 commit → 用户决定何时 push。工具不会自动提交、推送、拉取、合并、重载或部署。

## 本机一次性设置

Windows 使用 Python 3、PowerShell 7、WSL。Python 依赖见 `requirements.txt`，WSL 的 Python 也需 PyYAML。官方 Windows SubConverter-Extended 1.10.5 与已核验的 Mihomo 二进制保存在本地；不将可执行文件、数据或安装包加入 Git。

`.local/settings.json` 为本机忽略文件，包含以下键：`converter`、`converter_sha256`、`converter_version`、`core`、`core_wsl`、`core_sha256`、`core_version`、`wsl_distro`。分别记录转换器 Windows 路径、核验摘要/版本，内核 Windows 路径、WSL 路径、核验摘要/版本和发行版。首次准备的数据为 `.local/validation/runtime-data/GeoSite.dat`、`Country.mmdb`、`ASN.mmdb`。路径和个人信息不要写入公开文档。

本机已将 Git `core.hooksPath` 指向 `.githooks`，`meta.privacyPython` 指向本机 Python；新克隆需自行设置。私有主机名、节点名、内网地址等补充禁发词保存在 `.local/privacy-deny.json`，格式为字符串数组。

## 每次修改后

```text
python tools/run-checks.py
```

该命令以前台方式运行真实转换器，使用合成订阅，下载当前引用的规则，交给核验内核解析，再检查关键域名分流、节点筛选、锁定/故障切换、保存选择状态、零 Netflix 候选拒绝替换及空组拒绝连接，最后扫描工作目录与完整可达 Git 历史。输出在 `.local/validation/`；失败不会产生“本轮全部通过”凭证。

还会将 DNS-fullauto、退役 TikTok 兼容文件、clash-fullauto、WiFi Calling 与两个保护模块合并到生成配置，载入独立的 Nameserver-Policy 文件，检查规则出口、路由及 DNS 中的规则源引用、同名组与真实候选展开。此检查模拟这些文件所用的 YAML 操作符；不执行 OpenClash 的 General 设置和 Shell/Ruby 段。故障修复时已另行对比路由器实际 Ruby 合并器的输出。

`run-checks.py` 会启动并停止隔离测试内核，安全测试还会重启这个测试实例。当前会话禁止重启核心，禁止自动执行该完整命令。`python tools/overwrite-check.py --static-only` 复用摘要匹配的缓存，检查合并、引用、循环及候选数量，不运行内核；`python tools/overwrite-check.py --parse-only` 另外调用同版内核的 `-t`，解析后退出，不启动监听或运行测试实例。两者都不能替代完整运行验证。本次 DNS 与模块修复已通过 `--parse-only`，完整回归仍未重跑。

业务解锁另验：204 只验证连通性与延迟。Netflix 首次使用、换机场或异常时实际播放验收；AI 还需真实业务验证和用户最终候选名单。第一版 AI 只限定美/日/新加坡，包含带日本 FLAG 的自建候选，尚不能称为已经业务验证的稳定池。

## 部署前的候选检查

转换后先暂存为独立候选文件，禁止直接覆盖运行配置。提供商缓存必须存在；缺缓存、Netflix 合格候选为零、意外空组或引用错误均拒绝输出。

```text
python tools/policy.py prepare candidate.yaml --data-dir CORE_DATA_DIR --output checked-candidate.yaml --first-activation
```

此步骤应用 `PrivateNet` 的 28 个固定直连网段，并给各策略组设置 `empty-fallback: REJECT`。Netflix 和 AI 自动组还显式保留 REJECT 哨兵，以免转换器将空候选改成 DIRECT。首次激活设置 `profile.store-selected: false`，防止保存的直连选择覆盖新默认值。

这是**文件候选检查**，不会自行调用内核或部署。其后必须执行核验内核的 `-t -d CORE_DATA_DIR -f checked-candidate.yaml`；只在检查成功并获得部署指令后，才可原子替换目标。OpenClash 界面直接切换配置会绕过这个文件检查，未来部署不能省略。

首次激活后，在真实放行业务前检查并迁移保存选择。认证从 `MIHOMO_API_SECRET` 环境变量读取，不写入命令行参数或文件。控制器地址以本机实际配置为准：

```text
python tools/policy.py state --controller CONTROLLER_URL
python tools/policy.py state --controller CONTROLLER_URL --apply --pin-ai
```

无 `--apply` 只检查。首次迁移将漏网之鱼改为手动代理，AI 则在自己的健康候选中固定当前可用节点。真实业务验证后才能确认该节点适合 AI。之后恢复 `profile.store-selected: true` 前须再次确认 API 状态；这保留正常手动选择的持久化能力。迁移是首次部署步骤，不能每次定时执行，否则会覆盖用户后来主动选择的直连。

`MetaFullautoSafety` 在 OpenClash 覆写中提供相同空组保护，`PrivateNet` 应最后应用以保持规则优先。两者均尚未自动安装或启用于路由器。

## 本地快照

全部检查通过后：

```text
python tools/recovery.py save --label verified --keep 3
python tools/recovery.py verify SNAPSHOT_DIRECTORY
python tools/recovery.py restore SNAPSHOT_DIRECTORY --destination NEW_RECOVERY_DIRECTORY
```

快照保存模板、覆写模块、检查工具、核验过的转换器/内核版本、GEO 数据、规则缓存与测试证据，逐文件记录 SHA256，默认只保留最近三份。仅接收合成节点测试缓存。恢复只允许新目录，绝不直接覆盖活动配置；恢复后用目录内的内核、数据和测试 YAML 再做离线 `-t` 检查，并审查保存状态，随后才考虑上线。

快照同时保存纯模板的 `runtime-data/`、`converted-native-test.yaml`，以及旧模块合并后的 `module-data/`、`module-native.yaml`。恢复验证时分别使用对应的数据目录，不能混用规则缓存。

快照全部留在忽略目录，不作为自建 RULESET 发布。这支持回退和对比，不能保证两次本地验证之间上游的每次自动更新都已提前测试。

## 推送前审查

```text
python tools/privacy.py --history
```

本机 pre-push 钩子自动执行此扫描，并在发现潜在凭据、未知私网主机地址或私有标识时拦截。`privacy-examples.json` 仅按完整内容 SHA256批准已逐字审查的上游示例和标准保留网段；修改后摘要失效，需要重新人工审查。它不会豁免 token 或私钥检测。

推送前仍须人工逐字审查所有新文件、修改、注释、文件名、提交说明和新出现的主机标识。自动扫描不能识别所有私人域名或人名。对维护缓存、订阅、GEO、工具与恢复目录的 Git 忽略规则也需一起复核。
