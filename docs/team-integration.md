# 新仓库内容与 harness 的接入状态

本轮整合 `prompting` 主分支、`tests` 分支的三份完整游戏规格，以及 `Webmcp` 分支的素材门户提案。原始规格、配图和契约文件保留，未以 Canvas 版本替代明确的第三方引擎要求。

最新 [WebMCP 交接说明](../webmcp/handoff.md) 区分了研究 MAS、浏览器 WebMCP 命令适配器和外部 MCP 素材门户。当前 harness 的改写、生成与 QA 角色不等同于该文档提出的多候选研究、决策/版本、蓝图编译流水线；后者仍需独立实现并连接下游 Builder。

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

配置 `MATERIAL_PORTAL_URL` 后，RAG 阶段可以通过 HTTP bridge 发送 v0.1 archetype 请求。可选 `MATERIAL_PORTAL_TOKEN` 用于服务端鉴权；`MATERIAL_PORTAL_SCHEMA` 可指定契约文件位置。HTTP bridge 需要由实际门户接入端提供，当前未配置真实服务。

- mock 结果保留轨迹，不能成为真实生成证据。
- 真候选仍标记为 `unverified_candidate`；来源、revision 和许可信息保持原样，未检查的内容不会变成“已验证”。
- 超时或契约错误返回警告，继续使用本地知识库，不伪造门户成功。
- 同一输入使用稳定 requestId 与 Idempotency-Key；不盲目重试可能计费的素材搜索。
- 异步取消传播至 HTTP 请求；无真实 MCP 作业协议时，不声称支持外部门户作业恢复。

目前完成的是应用契约验证、HTTP bridge 和本地样例回归。实际 MCP tool 名称、传输方式、图像 URI 解析、源代码获取、外部作业恢复和 browser WebMCP 命令暴露仍需与门户负责人对齐。样例通过 schema 校验不代表已经连接真实 MCP 服务。
