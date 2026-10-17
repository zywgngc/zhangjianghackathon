# 团队组件与官方 case 接入状态

v2 保留 `prompting` 资料、`tests` 三份完整规格和参考图、`Webmcp` 素材门户契约与独立研究服务，并将大型游戏作为完整工程任务执行。长规格入口、React / TypeScript / Vite 多文件构建与真实 Phaser / Babylon.js 模板已接通，正式规格不会改写成 Canvas 近似版本。

## 三个必须完成的正式 case

| case | 完整原文与引擎锁 | 本轮实际任务 ID | 验收状态 |
| --- | --- | --- | --- |
| `GAME-TEST-001` 数字门枪手 | `case1-Number_Gate_Shooter/001数字门枪手.txt`；Babylon.js + React + TS + Vite | `5534e13dfdc74d1aa810f2beb719e5ed` | 真实实现推进中，尚未完整通过 |
| `GAME-TEST-002` 武林幸存者 | `case2-Wuxia_Survivors/002武林幸存者.txt`；Phaser + React + TS + Vite | `c0e2aefc313246fc879c5a89f505bdfb` | 真实实现推进中，尚未完整通过 |
| `GAME-TEST-003` 百炼英雄／战力吞噬者 | `case3-Power_Hunter/003百炼英雄.txt`；Babylon.js + React + TS + Vite | `11fc08ffeb364846aadb87751eb842cf` | 真实实现推进中，尚未完整通过 |

此表运行 ID/状态沿用 harness 负责人的上游交接记录，本次组件集成没有重跑或重新核实远程游戏任务。本分支为 Windows 检出把目录中的冒号改成连字符，原文和图片内容未改。

任务在 210 的 `data/runs/<id>/` 保留原文、计划、真实编码动作、编译日志、失败/恢复轨迹和独立审查。可用 `GET /api/projects/{id}` 查询当前状态。上述 ID 是持续执行的实际回合，不是三个已完成游戏，也不应通过新建 ID 隐去失败或重置额度。

工作台可以从官方 case 直接启动，或通过 `POST /api/benchmarks/{case_id}/runs` 创建新回合。已存在的任务继续使用同一 ID 跟踪与恢复；每次 POST 都会创建新任务。完整长规格也可经 `POST /api/projects` 或 TXT 导入提交，最大 1,000,000 字符。

```bash
# 不调用模型：资料完整性及运行能力预检
.venv/bin/python scripts/check_benchmarks.py --require-runnable
```

预检报告为 `data/benchmarks/preflight.json`，也可读取 `GET /api/benchmarks`。检查包含规格 ID、全文哈希、要求的技术栈、验收章节、PNG 尺寸、重复图片和命名冲突。当前三份资料具备执行入口与相应引擎，预检的 `ready/runnable` 只表示可以进入流程；其中 `execution: not_run` 是预检本身不执行游戏的说明，不能覆盖上表实际任务或冒充验收结论。case3 标题与正文名称差异、重复参考图均保留来源记录。

## 组件边界与交付契约

| 组件 | 当前接入与责任 |
| --- | --- |
| 原始规格与参考图 | `specifications.py` 无损归档、分片、章节/数值/验收索引及源 SHA；图像保留来源和哈希 |
| 项目规划 | `ProjectOrchestrator` 生成有 owner、文件归属、接口与验收映射的功能 DAG，检查完整性和集成依赖 |
| 编码代理 | `CodingAgent` 分页读取、写入、精确补丁、构建与源码 SHA 恢复；主机强制工具权限和预算，有实质源码进展才可续接租约 |
| 按功能检索 | 每个功能按标题、游戏名和引擎调用已有 `Retriever`，缓存 query、文档 ID/来源与警告，通过 `retrieved_knowledge` 按需读取 |
| 真引擎工程 | `ProjectWorkspace` 固定 React / TS / Vite 与 Phaser / Babylon 依赖、构建日志和产物 manifest |
| 独立机械验证 | `ProjectValidator` 在真实编译资源上执行浏览器输入、边界条件与正常通关路线，保存可信轨迹 |
| 独立视觉审查 | `project_review.py` 只针对已提供的主机截图和原始视觉准则形成具体观察；缺画面保持不足 |
| 正式验收 | `acceptance.py` 重读并核验带 SHA 的文件、执行逐项断言；功能实现、编译成功与正式通过分别记录 |
| 中文工作台与 ZIP | 展示完整规格、功能状态和逐项证据；允许带醒目标记的候选预览/下载，全部门禁满足后才正式交付 |
| WebMCP 研究服务 | 独立 Node 24 / SQLite 服务，提供参考 case 的研究、候选、追问及 partial 蓝图；尚未接入 Python 项目编码与验收流程 |

数值规则、真实状态、参考图外观和实体设备性能使用不同证据。没有测试执行器、尚无相关截图或需要真人/真机复验的项不会被自动改成通过。独立源码审查是后续编码建议，不是运行验收；模型的 `finish/pass` 也不能覆盖实际编译或可信断言失败。

当前 v2 已从 v1 的一次 HTML 生成扩展为可恢复的工程工具循环，仍是单机文件锁与 SQLite 状态管理。研究依据见 [长程任务调研](long-horizon-research.md)，运行机制与执行权限见 [Harness 架构](harness.md)。

项目总预算为 600 调用、6,000,000 个已报告 token 和 6 小时。单功能租约只有在回合达到 `action_limit` 或局部阅读停滞、且主机确认本租约源码 SHA 实质变化时才允许继续，最多 8 个租约；无进展故障最多 3 次尝试。续接与恢复不会重置全局预算。

本轮三个正式 case 使用各自的 900 调用 / 12,000,000 token 执行档案，覆盖完整内容、集成修复和独立验证；武林幸存者另包含12主动技能、8被动技能、6进化及长时运行。各任务的配置变更、调整时已用量与理由记录在 `execution-policy.json` 和 `policy_updated` 事件中；此前累计用量与原始6小时时限均保留。默认新项目配置仍为600调用/6,000,000 token。

## 现有 RAG 与素材门户

本地 Markdown BM25、`RAG_API_URL` HTTP 检索和可选素材门户 bridge 已提供适配边界。已批准的完整 case 原文始终是权威需求来源，检索素材不改变数值、技术栈或正式验收项。

v2 已调用该检索边界，而非只预留接口：每个功能节点生成 `project-retrieval-<feature>.json` 并保留 query、documents 和 warnings；编码工具按需读取 `retrieved_knowledge`。外部 RAG 和素材门户的真实服务地址仍未提供，默认本地检索与 HTTP 适配实现不能表述为真实外部服务已经上线。

`material_portal.MaterialPortalClient` 使用团队 `webmcp/material-portal.schema.json` 校验请求与结果，并检查版本、requestId、数量、类型、候选 ID 唯一性和讨论关联字段。配置 `MATERIAL_PORTAL_URL` 后通过 HTTP bridge 发送 v0.2 首轮 archetype 请求，`discussion.runId` 使用真实任务 ID；可选 `MATERIAL_PORTAL_TOKEN` 用于鉴权，`MATERIAL_PORTAL_SCHEMA` 可覆盖契约路径。目前没有将该适配器表述为已连接真实外部 MCP 服务。

- mock 结果仅作契约和接口测试，不能成为真实素材来源证据。
- 真实候选仍为 `unverified_candidate`，保留来源、revision 和许可信息，不自动视为已验证或获授权素材。
- 超时或契约错误返回警告，允许继续使用本地知识；不会伪造门户成功。
- 稳定 requestId 与 Idempotency-Key 用于关联请求，不盲目重试可能计费的搜索。
- 异步取消传播至 HTTP 请求；没有真实作业协议时不声称具备外部作业恢复。
- `needs_clarification` 保留问题与候选，不能伪造答复或把未解决澄清结果直接注入生成。

[WebMCP 交接说明](../webmcp/handoff.md) 区分研究 MAS、浏览器 WebMCP 命令适配器和外部素材门户。合并的组件 `82862cf` 已提供可执行的独立研究服务，详见 [运行说明](../webmcp/runtime.md)。它为三个固定 case 分配机制，采用分离的 Researcher / Planner / Reviewer 组件、确定性参考规划和默认 synthetic 门户；尚未调用 LLM 规划、检查来源代码或验证候选之间的兼容组合。全部蓝图明确标记 `readiness: partial`，全部正式游戏测试仍为 `not_executed`。

该服务已实现两套各最多三次的独立追问预算、必答澄清等待、可选问题默认值、修订与父请求关联、请求幂等、取消与迟到结果隔离，以及 SQLite 重启恢复和跨进程预算预留。门户请求只有在适配器明确保证 request-ID 幂等时才能安全重放。浏览器适配器提供七个研究工具，检测到 `modelContext` 才注册；普通 studio / HTTP API 可单独使用。自动化注册测试使用模拟 `modelContext`，不能作为本轮原生浏览器 WebMCP 注册成功的证据。

Python `MaterialPortalClient` 的 v0.2 素材搜索请求与研究服务的 `POST /api/runs` 是不同协议：后者接受 `StartRequest` 并返回研究任务 ID，随后提供 partial 蓝图。**不能将研究服务地址直接填入 `MATERIAL_PORTAL_URL`**。`MaterialPortalClient` 仍只执行首轮素材搜索；研究服务的追问、修订和蓝图经独立 `/api/research` 适配器访问。Node 状态机测试成功不意味着研究蓝图已接通 v2 游戏生成。

真实外部 MCP tool 名称、传输和鉴权、图片 URI 的获取与解读、来源代码和许可审查、非幂等外部作业恢复，以及蓝图到 Builder / QA 的适配仍需与组件负责人对齐。外部候选即使 `isMock: false` 也只标记 `discovered`；revision 或 confidence 不能替代来源与运行验证。这些接入事项不再阻止 harness 接收三份完整规格或使用真实引擎构建，游戏本身仍必须完成原文规定的全部正式验收。

## WebMCP 在 210 的隔离验证

本轮在 `.harness-dev/webmcp-validation/` 同步组件源码，使用独立 Node `v24.10.0` 与 pnpm `10.18.3`，完成 `pnpm install --frozen-lockfile`、`pnpm check`、`pnpm test` 和 `pnpm demo`。Node Linux x64 压缩包按 [官方 SHA256 清单](https://nodejs.org/download/release/v24.10.0/SHASUMS256.txt) 校验为 `2642f4428869aca32443660fd71b3918e2be1277a899bdcaeb64c93b54b5af17`。主游戏环境仍为 Node `v22.21.0`，没有重启主 harness、修改门户地址或发起重复的正式游戏任务。

38 个 Node 测试全部通过、无跳过，覆盖实际本地 HTTP 请求、SQLite 状态与跨进程预算竞争、取消与超时、恢复、契约和模拟浏览器适配器。该结果是研究组件的基础设施回归，不包含原生 WebMCP、真实门户、LLM 或游戏运行验收。

| 隔离 demo | 机制 | synthetic 候选 | 保留的正式准则 | 游戏验收实际执行 | 蓝图 |
| --- | ---: | ---: | ---: | ---: | --- |
| `GAME-TEST-001` | 6 | 21 | 54 | 0 | partial |
| `GAME-TEST-002` | 7 | 24 | 88 | 0 | partial |
| `GAME-TEST-003` | 6 | 21 | 83 | 0 | partial |

主机重新读取全部 demo 文件，确认每个候选均为 `isMock: true` / `evidence: discovered`，225 条验收均为 `not_executed`。每个 profile 的 ID、逐条原文（允许换行折叠）、固定来源提交和引擎锁也与 Python 的完整规格索引比对通过。`tests/test_team_components.py` 另验证研究任务回执或 partial 蓝图不会误作为素材搜索证据注入。

210 证据入口为项目目录 `.harness-dev/webmcp-validation-report.json`，保存源文件 manifest、source SHA、Node / pnpm 版本、四步日志哈希与三份蓝图哈希；原日志位于 `.harness-dev/webmcp-validation/*-validation.log`，蓝图及独立 SQLite 位于该目录 `.demo/`。可在不触及主服务的情况下重跑：

```bash
cd /home/zhaoyiwen/zhaoyiwen_nfs_data/zhangjianghackathon
export PATH="$PWD/.harness-dev/node24-runtime/node-v24.10.0-linux-x64/bin:$PWD/.harness-dev/node24-runtime/pnpm/node_modules/.bin:$PATH"
cd .harness-dev/webmcp-validation
pnpm install --frozen-lockfile --store-dir ../node24-runtime/pnpm-store
pnpm check
pnpm test
pnpm demo
```

新合并的 [研究组件适配器](webmcp-integration.md) 提供 `/api/research` 转发、`/research` 工作台及 v1 固定蓝图接收钩子。两套追问计数与修订仍由 Node 研究服务维护，Python 不重复计数。v2 `/api/projects` 的 ResearchReference 与按功能使用候选尚未接入，三个正式 case 继续执行完整原文工作流。

## 实测事实的范围

v2 运行时专项已在 210 验证两个引擎真实构建/渲染、opaque iframe、无外部资源请求、热缓存离线安装及编译错误阻断；工作台专项覆盖长 TXT 无损提交/下载、官方 case 创建、候选/正式区分、存档隔离及移动布局。这些是 harness 基础设施回归。

v1 打地鼠 `b475570322d5409dadfc5549ceae0053` 已有真实生成、修复、浏览器/截图 QA 和正常胜负操作证据，见 [实测记录](harness.md#v1-打地鼠实测记录)。它不计入三份大型正式 case 的完成数量。大型 case 的完成结论只取决于各自原文验收与真实证据，不以模板演示或基础设施测试替代。


2026-10-17 后续已合并 `af1b57d`、`0b419a7`：研究工作台中文化，普通创意支持保留用户已知条件、编辑默认提案、round-zero 选择和来源标记。三份 benchmark 仍保留完整原文。210 在隔离 Node 24 下完成更新后的 TypeScript check 与 47 项真实 Node 回归；Python 研究网关及编码上下文回归 76 项通过。该结果不包含游戏验收或真实外部门户。
