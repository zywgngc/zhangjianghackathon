# 新仓库内容与 harness 的接入状态

`zx` 的主流程基于 `codex/game-harness@470bdfebd46f`，保留上游整合的 `prompting` 设计资料、`tests` 分支三份完整游戏规格和 `Webmcp` 门户契约，并接入真实在线美术检索与离线图片交付。原始规格、配图和契约内容保留，未以缩减 Canvas 版本替代明确的第三方引擎要求；规格目录中的冒号映射为下划线以兼容 Windows。

[WebMCP 交接说明](../webmcp/handoff.md) 保留原分支的研究 MAS 提案与当时的状态快照，区分研究 MAS、浏览器 WebMCP 命令适配器和外部 MCP 素材门户。当前 harness 的改写、生成与 QA 角色不等同于该提案中的多候选研究、决策/版本、蓝图编译流水线；后者仍需独立实现。当前可执行素材接口以[素材接入说明](../integrations/game-harness/README.md) 为准。

## 完整游戏验收用例

```bash
python -X utf8 scripts/check_benchmarks.py
# CI 中要求所有用例均可运行：当前应退出 2，表示存在能力缺口
python -X utf8 scripts/check_benchmarks.py --require-runnable
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

主流程 RAG 阶段发送 v0.2 archetype 首轮请求，`discussion.runId` 使用真实任务 ID。`MATERIAL_PORTAL_URL` 选择后端：

- `web://assets`：当前配置模板默认启用的在线美术检索，匿名查询 Kenney / OpenGameArt，下载并核验许可和 PNG/ZIP 文件。美术候选以 `reference_media`、`license.scope="assets"` 交付。
- `local://references`：固定离线代码参考检索，不执行在线素材搜索。
- HTTP(S) URL：调用团队提供的 v0.2 远端门户；可选 `MATERIAL_PORTAL_TOKEN` 用于该服务的鉴权。远端地址需要自行提供，不能把内置在线搜索当作已经连接某个 MCP 服务。

空配置按原流程关闭门户；`MATERIAL_PORTAL_SCHEMA` 可指定契约文件位置。不会将 v0.2 静默降级发给只支持 v0.1 的服务。

在线美术的文件与清单保存在 `DATA_DIR/material-assets/`。RAG 文档附来源、许可、尺寸、文件哈希与交付清单，主流程将其转为简短 `asset://` 图片引用给设计、生成和修复阶段；浏览器验证前把实际 PNG 内嵌到 HTML，保存 `vN.source.html` 和 `vN.asset-usage.json`。来源元数据不参与页面布局。找不到素材或站点失败时保留成功结果并披露限制；不以无关素材补数。

- mock 结果保留轨迹，不能成为真实生成证据。
- 代码与远端研究候选仍标记为 `unverified_candidate`；来源、revision 和许可信息保持原样。完成本地字节核验的美术文档标记为 `downloaded_hash_checked_art`，表示文件已下载并通过检查，不表示画风、视角或玩法适配已验证。
- 超时或契约错误返回警告，继续使用本地知识库，不伪造门户成功。
- 同一输入使用稳定 requestId 与 Idempotency-Key；不盲目重试可能计费的素材搜索。
- 异步取消传播至 HTTP 请求；无真实 MCP 作业协议时，不声称支持外部门户作业恢复。
- 响应必须匹配 runId、threadId、round，并校验澄清问题 ID、选项唯一性与默认选项归属。
- `needs_clarification` 会保留完整问题与候选并发出警告，不伪造答复；当前单轮 bridge 不向 Builder 注入这批未解决澄清的候选，仍可使用本地知识。

最新 v0.2 的两套独立三轮追问预算、主应用追问命令、修订冲突、问题回答、原子额度预留与重启恢复属于研究 MAS 的后续 runtime 工作。本 bridge 仅执行 round 0；schema 样例校验不代表这些讨论状态机已实现。

目前完成应用契约验证、HTTP bridge、固定代码参考、真实网站美术搜索下载，以及生成与修复阶段的离线图片内嵌。在线检索尚无参考图视觉分析、选材 LLM、自动统一画风或动画分帧；当前交付限 2D PNG/ZIP。实际 MCP tool 名称、传输方式、图像 URI 解析、源代码获取、外部作业恢复和 browser WebMCP 命令暴露仍需与门户负责人对齐。样例通过 schema 校验不代表已经连接真实 MCP 服务；当前真实试跑结果与已知缺陷见[验证记录](source-validation.md)。
