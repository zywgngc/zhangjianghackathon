# Harness + WebMCP 最终集成版

分支：`codex/final-webmcp-harness`。这是最新 harness 与 WebMCP 组件的合并交付版本，包含两边的 Git 历史和下面列出的兼容修复。“最终集成”指本次代码交付范围，不表示所有游戏验收通过或所有队友服务已经接通。

## 版本来源

| 来源 | 固定提交 | 纳入范围 |
| --- | --- | --- |
| `codex/game-harness` | `4d779f18d168ba32c5bb60a6829b68876d2048e2` | 最新 v1/v2 harness、项目构建与验收、已有 WebMCP 网关、上游游戏证据文档 |
| `codex/webmcp-harness-integration` | `4304ae6bbdf02ebff4a6ab6c93f8d9f1beb695f7` | WebMCP 研究组件、中文界面、来源证据、默认缓存模式、持久请求限额与冷却 |
| 默认分支 `prompting` | `e2602adce3e05d067f721320cdf10c7fcf7c87a3` | 已在上述 harness 的祖先历史中 |
| 测试规格分支 `tests` | `81fc5366aa417754ccdba07d5d3dc08daa8f6a02` | 已在 harness 历史中；54 / 88 / 83 条原始验收要求保留 |

`Webmcp@82862cf` 也已在合并历史中。`zx@2fb926c` 是另一份基于旧版 v1 harness 的材料服务快照，本次没有整体合入；不能用它覆盖新版项目运行时。材料服务负责人仍可通过独立端口继续集成。

## 组件与可运行范围

| 入口 / 组件 | 当前行为 |
| --- | --- |
| `/` | harness 主工作台，保留单文件游戏与多文件项目流程 |
| `/research` | WebMCP 研究工作台：稀疏创意、默认提案、选择、机制拆分、候选、修订和蓝图 |
| `/api/research/*` | harness 鉴权网关，转发给独立 Node 研究进程 |
| GitHub 原型研究 | 默认缓存模式；显式启用有限联网后才发出请求。缓存不足会显示缺口 |
| MaterialPortal | 队友拥有的接口，目前 Node 研究服务的真实适配器尚未配置 |
| v1 蓝图交接 | 已接入准入检查；要求 ready、验证证据与兼容 HTML 引擎，partial 不会启动 Builder |
| v2 蓝图交接 | `/api/projects` 尚未接收 WebMCP 研究蓝图；v2 继续使用完整规格入口 |

研究过程不会自动认定图片已理解、源码行为已验证或游戏已经验收。main、material 各最多三轮追问，原型研究计数独立。

## 合并时补齐的兼容项

- 联合启动器保留上游环境隔离与 `--research-node`，只传递命名的研究配置；GitHub token 仅在显式 `bounded` 且非 fixtures 时传递，不继承通用 GitHub、模型、RAG 或材料服务密钥。
- 新增 `WEBMCP_GITHUB_STATE`，让保护状态可放在固定的本地磁盘路径。切换运行历史数据库不会重置请求预算；同机预览应共用该文件。
- HTTP 响应体读取阶段的断线或超时同样持久化冷却，避免下一任务立即再请求。
- 规格与参考图的仓库相对路径统一使用 `/`，使 Windows 与 Linux 的来源引用一致，未改动规格内容或哈希。

## 安装与启动

在受支持的环境准备 Python 3.10+、Python 项目依赖以及 Node 24.10–24.x 研究运行时：

```sh
git switch codex/final-webmcp-harness
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
pnpm --dir webmcp install --frozen-lockfile
```

Windows 使用 `.venv/Scripts/python.exe`；现有虚拟环境不必重建。游戏生成需要配置模型服务，浏览器 QA 需要安装 Playwright Chromium。默认缓存研究不需要 GitHub 登录或 token。

```sh
WEBMCP_GITHUB_NETWORK=off \
WEBMCP_DB=/var/lib/webmcp/research.sqlite \
WEBMCP_GITHUB_STATE=/var/lib/webmcp/github-network.sqlite \
.venv/bin/python scripts/run_integrated.py --port 18080 --research-port 4175 \
  --research-node /path/to/node24
```

以上目录由部署者预先创建并赋予运行用户权限，两个 SQLite 文件必须在单机本地磁盘上；若源码在 NFS 上，不能省略这些路径。所有预览应使用同一 `WEBMCP_GITHUB_STATE`，不能通过更换路径绕过限流。Builder 可保留原有 Node22，研究侧单独指定 Node24。

Windows 本地研究与 v1 接入验证：

```powershell
$env:WEBMCP_GITHUB_NETWORK = 'off'
.venv/Scripts/python.exe scripts/run_integrated.py --port 18080 --research-port 4175
```

Windows 默认两个数据库都在本地 checkout 的 `webmcp/.data/`。完整 v2 编码/构建运行时依赖 POSIX 文件锁与进程控制，应使用受支持的 Linux/macOS；本次不声称完成 Windows v2 移植。

打开 `http://127.0.0.1:18080/research`。`GET /api/research/providers` 应显示 `github.enabled: false`。`WEBMCP_RESEARCH_MODE=fixtures` 只用于明确的模拟演示；它强制关闭 GitHub 联网。

## GitHub 请求保护

有限联网必须由服务端显式设置 `WEBMCP_GITHUB_NETWORK=bounded`。每创意最多 15 次 HTTP 尝试，跨追问累计；共享状态库限制为 20 次/分钟、4 次搜索/分钟、30 次/小时和 120 次/天，串行请求至少间隔两秒。失败、取消和崩溃预留可能计入预算。限流或拒绝访问持久化冷却，不自动重试。

这些措施约束本组件，不保证 GitHub 对账户/IP 的零风险，也无法控制同一公网出口下其他软件的流量。详见 [检索范围及保护策略](../webmcp/github-research.md)。

## 本次验证

2026-10-17，在 Windows / Node 24.19 / Python 3.12 上验证：

- Node 组件测试 62 项通过，TypeScript 检查与 Prettier 格式检查通过；包含真实生产入口启动、独立保护数据库配置和缓存缺失时 HTTP 次数为零。
- Python 接入、队友契约、规格、harness、agents 与工作台测试：69 通过，2 个需额外运行条件的测试跳过，1 个依赖 POSIX 项目运行时的 `project_candidate` 测试未执行；另有 3 个子测试通过。
- GitHub 响应使用注入模拟；没有通过真实 GitHub 检索来测试限额。未调用生成模型或重新运行完整游戏验收。

上游已有的游戏验收证据随 harness 历史保留，但属于对应源码版本的历史结果；不能代替本合并提交的完整 Linux v2 发布验收。本次没有更新正在运行的远程服务。

## 继续集成

材料服务适配、图片/模型理解、源码行为验证和研究到 v2 Builder 的交接仍需分别实现。进入默认分支前可用本分支统一做团队回归；组件边界和接口详见 [联合运行说明](webmcp-integration.md)。
