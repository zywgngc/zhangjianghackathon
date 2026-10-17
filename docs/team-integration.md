# 团队组件与官方 case 接入状态

v2 保留 `prompting` 资料、`tests` 三份完整规格和参考图、`Webmcp` 门户契约，并将大型游戏作为完整工程任务执行。长规格入口、React / TypeScript / Vite 多文件构建与真实 Phaser / Babylon.js 模板已接通，正式规格不会改写成 Canvas 近似版本。

## 三个必须完成的正式 case

| case | 完整原文与引擎锁 | 本轮实际任务 ID | 验收状态 |
| --- | --- | --- | --- |
| `GAME-TEST-001` 数字门枪手 | `case1:Number_Gate_Shooter/001数字门枪手.txt`；Babylon.js + React + TS + Vite | `5534e13dfdc74d1aa810f2beb719e5ed` | 真实实现推进中，尚未完整通过 |
| `GAME-TEST-002` 武林幸存者 | `case2:Wuxia_Survivors/002武林幸存者.txt`；Phaser + React + TS + Vite | `c0e2aefc313246fc879c5a89f505bdfb` | 真实实现推进中，尚未完整通过 |
| `GAME-TEST-003` 百炼英雄／战力吞噬者 | `case3:Power_Hunter/003百炼英雄.txt`；Babylon.js + React + TS + Vite | `11fc08ffeb364846aadb87751eb842cf` | 真实实现推进中，尚未完整通过 |

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

数值规则、真实状态、参考图外观和实体设备性能使用不同证据。没有测试执行器、尚无相关截图或需要真人/真机复验的项不会被自动改成通过。独立源码审查是后续编码建议，不是运行验收；模型的 `finish/pass` 也不能覆盖实际编译或可信断言失败。

当前 v2 已从 v1 的一次 HTML 生成扩展为可恢复的工程工具循环，仍是单机文件锁与 SQLite 状态管理。研究依据见 [长程任务调研](long-horizon-research.md)，运行机制与执行权限见 [Harness 架构](harness.md)。

项目总预算为 600 调用、6,000,000 个已报告 token 和 6 小时。单功能租约只有在回合达到 `action_limit` 且主机确认源码 SHA 实质变化时才允许继续，最多 8 个租约；无进展故障最多 3 次尝试。续接与恢复不会重置全局预算。

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

[WebMCP 交接说明](../webmcp/handoff.md) 区分研究 MAS、浏览器 WebMCP 命令适配器和外部素材门户。当前项目的规划/编码/验证多代理不等同于其多候选研究、决策修订及蓝图编译流水线。[v0.2 讨论提案](../webmcp/discussion-policy.md) 中两套独立三轮追问预算、问题回答、修订冲突与讨论重启恢复仍需单独实现；现有 bridge 执行首轮检索，schema 样例通过不等于这些状态机已经落地。

真实 MCP tool 名称与传输、图片 URI 解析、来源代码获取、外部作业恢复、浏览器命令注册及完整讨论流程，仍需与对应组件负责人对齐。这些外部接入事项不再阻止 harness 接收三份完整规格或使用真实引擎构建，游戏本身仍必须完成原文规定的全部正式验收。

## 实测事实的范围

v2 运行时专项已在 210 验证两个引擎真实构建/渲染、opaque iframe、无外部资源请求、热缓存离线安装及编译错误阻断；工作台专项覆盖长 TXT 无损提交/下载、官方 case 创建、候选/正式区分、存档隔离及移动布局。这些是 harness 基础设施回归。

v1 打地鼠 `b475570322d5409dadfc5549ceae0053` 已有真实生成、修复、浏览器/截图 QA 和正常胜负操作证据，见 [实测记录](harness.md#v1-打地鼠实测记录)。它不计入三份大型正式 case 的完成数量。大型 case 的完成结论只取决于各自原文验收与真实证据，不以模板演示或基础设施测试替代。
