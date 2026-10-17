# 新仓库内容与 harness 的接入状态

本轮整合 `prompting` 主分支、`tests` 分支的三份完整游戏规格，以及 `Webmcp` 分支的素材门户提案。原始规格、配图和契约文件保留，未以 Canvas 版本替代明确的第三方引擎要求。

最新 [WebMCP 交接说明](../webmcp/handoff.md) 区分了研究 MAS、浏览器 WebMCP 命令适配器和外部 MCP 素材门户。研究 runtime 已作为独立组件接入 harness，详见 [组件集成说明](webmcp-integration.md)。Python harness 继续负责生成与 QA；Node 组件负责研究、决策/修订及两套讨论账本。集成分支供 harness 负责人审阅，不替代其流水线。

## 完整游戏验收用例

```bash
.venv/bin/python scripts/check_benchmarks.py
# CI 中要求所有用例均可运行：当前应退出 2，表示存在能力缺口
.venv/bin/python scripts/check_benchmarks.py --require-runnable
```

报告写入 `data/benchmarks/preflight.json`，API 为 `GET /api/benchmarks`。检查包括规格 ID、源文件哈希、要求的技术栈、验收章节、PNG 尺寸、图片重复和游戏命名冲突。

| 用例 | 明确要求的引擎 | 当前状态 |
| --- | --- | --- |
| GAME-TEST-001 数字门枪手 | Babylon.js + React + TypeScript + Vite | blocked |
| GAME-TEST-002 武林幸存者 | Phaser + React + TypeScript + Vite | blocked |
| GAME-TEST-003 百炼英雄／战力吞噬者 | Babylon.js + React + TypeScript + Vite | blocked |

这些规格长度超过当前一句话入口的 8000 字符限制，也需要多文件 TypeScript 构建与离线第三方引擎模板。当前预检会列出缺口，不调用模型、不截断长规格、不修改引擎锁定，也不把资料检查当作游戏验收。下一阶段应先增加长规格输入和相应引擎适配器，再执行各规格内的数值、关卡、技能、性能及原型保真验收。

## 素材门户

`game_harness.material_portal.MaterialPortalClient` 直接使用团队维护的 `webmcp/material-portal.schema.json` 校验请求和结果，并检查 requestId、版本、候选数量、候选类型及 ID 唯一性。

配置 `MATERIAL_PORTAL_URL` 后，RAG 阶段可以通过 HTTP bridge 发送 v0.2 archetype 首轮请求，discussion.runId 使用真实任务 ID。可选 `MATERIAL_PORTAL_TOKEN` 用于服务端鉴权；`MATERIAL_PORTAL_SCHEMA` 可指定契约文件位置。HTTP bridge 需要由实际门户接入端提供，当前未配置真实服务。不会将 v0.2 静默降级发给只支持 v0.1 的服务。

- mock 结果保留轨迹，不能成为真实生成证据。
- 真候选仍标记为 `unverified_candidate`；来源、revision 和许可信息保持原样，未检查的内容不会变成“已验证”。
- 超时或契约错误返回警告，继续使用本地知识库，不伪造门户成功。
- 同一输入使用稳定 requestId 与 Idempotency-Key；不盲目重试可能计费的素材搜索。
- 异步取消传播至 HTTP 请求；无真实 MCP 作业协议时，不声称支持外部门户作业恢复。
- 响应必须匹配 runId、threadId、round，并校验澄清问题 ID、选项唯一性与默认选项归属。
- `needs_clarification` 会保留完整问题与候选并发出警告，不伪造答复；当前单轮 bridge 不向 Builder 注入这批未解决澄清的候选，仍可使用本地知识。

v0.2 的两套独立三轮追问预算、主应用追问命令、修订冲突、问题回答、原子额度预留与重启恢复已由 Node 研究 runtime 执行。新增 `/api/research/*` 只转发命令，不复制或重置其状态机。此处原有 `MaterialPortalClient` 仍只执行 round 0；带固定研究蓝图的生成绕过它，避免重复创建素材查询。

目前完成的是应用契约验证、HTTP bridge、研究 runtime、harness 适配器和浏览器 WebMCP 命令暴露。实际 MCP tool 名称、传输方式、图像 URI 解析、源代码获取和外部作业恢复仍需与门户负责人对齐。当前候选为 mock，蓝图保持 partial，不能进入正式 Builder；样例与集成测试通过不代表真实 MCP 服务已连接。
