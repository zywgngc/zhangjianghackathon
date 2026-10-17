# 素材组件接入评估与实施边界

本文评估 `zx@2fb926c3645c5d81376070b58bb54a4b857c5d84`，采用 `git show/diff`、Python AST 解析及仓库内文件哈希比对。仅完成静态验证：未启动素材服务、下载资源、安装依赖、调用模型或执行上游游戏。本文不改变三个正式 case 的验收状态，也不要求重启正在运行的服务。

## 当前状态

| 边界 | 代码中已有的能力 | 尚未接通的部分 |
| --- | --- | --- |
| Harness ↔ WebMCP 研究服务 | `/research`、`/api/research/*`、Node24 / SQLite 研究侧车与版本关联，详见 [研究组件接入](webmcp-integration.md) | v2 `/api/projects` 尚未接收研究蓝图；本次未重验部署状态 |
| WebMCP ↔ 机制材料门户 | `PortalAdapter` / `MaterialPortalPort.search` 契约 | 生产入口仍使用 `UnconfiguredMaterialPortal`；fixtures 模式不能作为真实素材来源 |
| v2 ↔ 检索 | 按功能调用 `Retriever`，保存 `project-retrieval-<feature>.json`；已有可选 HTTP `MaterialPortalClient` | 尚未连接 zx 的 Python 素材模块或联网下载器 |
| zx 素材组件 | 本地素材检索与交付、v0.2 来源发现、独立联网 PNG 采集实现 | 没有部署 HTTP / MCP 服务；旧 HTML 内嵌接线不等于 v2 工程接线 |

本次确认：17 个 `asset_agent` Python 文件可静态解析；18 个 demo SVG、24 份固定提交的参考文本全部匹配目录中的 SHA256。zx 的 v0.2 Schema、TypeScript 类型和 discussion 定义与当前 WebMCP 逐字节相同。这些检查不证明运行兼容、视觉贴合或游戏行为通过。

## 保留清单与合并方式

保留以下组件及其来源记录，采用选择性导入；不要整体覆盖主线：

- `asset_agent/**`：检索、契约校验、持久请求、导入、转换和交付；保留 `specs/` 的 Schema 与 provenance。
- `catalogs/demo/**`：18 个图标、目录元数据和 `UPSTREAM-LICENSE.txt`。
- `catalogs/references/**`：13 条来源候选、24 份固定提交文本、哈希、README 和许可说明。缓存源码只作参考，不能自动执行或作为已验证玩法导入。
- `schemas/asset-request.schema.json`、`schemas/asset-handoff.schema.json`、`prompts/query-builder.txt`、`prompts/selector.txt`。
- 对应 examples、素材专项测试、`tools/bootstrap_*`、`tools/evaluate_*`。bootstrap 和 online evaluation 会联网，应保持显式执行。

重要联网实现位于 zx 的 [`game_harness/material_portal.py`](https://github.com/zywgngc/zhangjianghackathon/blob/2fb926c3645c5d81376070b58bb54a4b857c5d84/game_harness/material_portal.py)，而非 `asset_agent/`。建议将其先保留为独立模块，例如 `asset_agent/harness_portal.py`，再由薄适配器引用；该文件本身不依赖旧 Harness 类。保留其协议、采集和字节校验专项测试。不要直接替换当前同名 HTTP 客户端，也不要复制旧 `runner.py` 的接线补丁。

`tools/run_game_harness.py`、`tools/verify_*_game.py` 和旧 HTML 示例保留为历史工具；其简化玩法检查不能替代当前完整规格入口和正式验收器。

## 实际接口、依赖与缺口

| Python 入口 | 输入与输出 | 默认行为 |
| --- | --- | --- |
| `AssetAgent.plan(request)` / `run(request, output_dir)` | `request_id/prompt/layout.slots/constraints` → 计划或素材文件、manifest、CREDITS、缺项 | 本地 BM25；query builder、selector、embedding 均须显式注入 |
| `ArtMaterialPortal.search(request)` | 旧 `purpose/description/imageRefs/constraints/limit` → candidates、limitations | 本地美术检索，不负责下载、resize 或打包 |
| `ResearchMaterialPortal.search(request, options)` | 正式 v0.2 request → 正式 result | 本地来源目录、SQLite 幂等、协作取消；不执行来源代码 |
| 独立保留的 `MaterialPortalClient` | `search(query, limit, run_id)` → documents/warnings/trace；另有 `search_request`、`collect_assets` | 显式选择 `local://references`、`web://assets` 或 HTTP 门户 |

它们是 Python 模块／CLI，不是已存在的服务地址。选择性导入完成后可先用以下入口验证本地组件；本次没有执行这些命令：

```sh
python -m asset_agent validate --catalog catalogs/demo
python -m asset_agent research request.json --catalog catalogs/references --cache /path/to/isolated/material-requests.sqlite3
```

仓库环境要求 Python 3.10+。本地美术核心主要使用标准库；v0.2 校验使用 jsonschema，位图 resize 可选 Pillow。联网采集使用当前已有的 httpx/jsonschema。合并 `pyproject.toml` 时应追加包发现、可选依赖、CLI 和 `asset_agent/specs/*.json`，同时保留现有 `project_templates/**/*`；不要用 zx 的配置覆盖它。目录数据目前不会自动进入 wheel，应明确配置可信 catalog 路径或另外完成资源打包。

`web://assets` 只覆盖 Kenney / OpenGameArt 的 PNG、ZIP 和逐来源页 CC0 声明检查：每请求最多 3 个概念、22 次 HTTP、40MB 响应、65 秒；单次下载最多 8MB，最多 4 包、每包 6 张 PNG，仍受调用方 limit 约束。3D、音频、字体、图集分帧和视觉匹配未实现。图像引用的描述可参与文字检索，图片本身未做视觉分析。不得将这些静态源码中定义的能力写成当前服务实测成功。

## 与 v2 的具体冲突

1. zx 携带旧 `470bdf` Harness：缺少当前工程运行时、编码工具、独立验收和 Node 研究服务，app 只保留 Canvas/DOM。必须保留当前 v2 的 app、runner、工作台、测试和锁文件。
2. 其 `request_for()` 固定 `engine=html`、`purpose=archetype`、`round=0`。功能检索应使用真实引擎、feature/mechanic ID、项目 revision 和讨论关联，不能靠查询文字冒充这些字段。
3. `asset://`→data URI 的旧接线面向单文件 HTML，默认最终大小 300KB；不能套用于 Vite bundle，也不能绕过 v2 的构建与预览资源校验。
4. 素材组件允许的单件尺寸可能为 4–8MiB；当前 `ProjectWorkspace` 为每文件 512,000 字节、总计 12,000,000 字节、500 文件。导入前应明确选择较小文件或生成有来源记录的派生件；不自动放宽工程限制。
5. 本地交付含 `absolutePath/bundleRoot/manifest` 路径，联网正式候选的 `source.paths` 也可能指向本机 manifest。它们只能留在主机私有证据中，不能直接透传给浏览器或模型。
6. 三份原始规格与当前连字符目录中的文本完全一致，不需要重复导入下划线目录。素材 `ready`、候选 `isMock:false` 或检索 `completed` 都不代表正式游戏通过。

## 最小桥接方案（待实现）

先接本地来源发现与少量明确槽位，在线采集默认关闭；不要把每个功能的 RAG 查询都变成一次下载任务。现有完整规格、功能 DAG 和批准的引擎仍是权威输入。

**来源检索桥：** 新增一个仅后端使用的适配器，满足现有接口；一种最小传输方案如下，路由目前不存在：

```text
POST /internal/materials/search
Authorization: Bearer <backend-only token>
body: 原样 MaterialSearchRequest v0.2
response: 原样 MaterialSearchResult v0.2
```

Python 端持有 `ResearchMaterialPortal` 的生命周期、可信 catalog 路径和独立缓存；Node 端实现 `PortalAdapter` 的 HTTP 调用及 AbortSignal 传播。只允许 loopback 或明确配置的后端地址，口令不进入浏览器、模型或公开素材站请求。只有确认相同 requestId 的并发、超时与重启行为后，才可声明 `idempotentRequests=true`。全局两套追问预算、revision、父链与迟到结果取舍仍由现有研究 orchestrator 维护。

正式请求应携带真实 run/thread/round、projectRevision、引擎；mechanic 请求还需 `mechanicId/requiredBehavior`。Schema、关联字段、候选类型和 mock 标记在两端校验。来源结果保持 discovered/unverified；不自动改成 behavior_verified，不因找不到候选而补入不相关素材。

**工程素材写入：** 宿主显式选择已核验的素材后，在该 run 的写入锁内执行：

1. 将原始请求、结果、下载／转换参数、许可和 SHA 保存到 run 私有附件，例如 `project-assets-<feature>.json`；图片与源码候选使用不同证据分类。
2. 只从宿主登记的 bundle 根读取文件；核验实际字节、路径／符号链接、类型、尺寸、SHA 和工程配额。拒绝模型提供的任意本机路径。
3. 用 `ProjectWorkspace.write_file("public/assets/<content-sha>.png", bytes)` 写入；保留来源、许可和派生关系。模型只收到项目相对路径、用途和简短元数据，不收到 Base64 或主机绝对路径。
4. 重新生成整个工程的 source SHA，并绑定后续构建、截图、验收和导出 manifest。任何素材增删或派生件变化都使原源码结论失效，不能修改报告中的旧 source SHA 来复用结果。
5. 使用当前 Vite 构建、`inline_preview()` 和 ZIP 导出流程；验证没有残留 `asset://`、本机路径或外部资源依赖。保持 opaque iframe、CSP 和 run 独立存档边界。

不得向正在正式验收的工程追加素材或修改既有报告。当前 case2 全规格验收继续按原流程执行；本方案用于独立接入验证及之后明确启用的任务。

## 接入后的验收顺序

1. 跑素材目录、检索、转换、交付、research contract/store 专项测试；核验已安装包中的 Schema、模板和 catalog 配置。保留当前 v2/WebMCP 全套回归，不能用 zx 的旧测试替换。
2. 经真实 Node ↔ Python 边界验证 requestId 冲突、关联字段、并发去重、超时后查询、取消、重启、mock 拒绝与澄清状态；先使用本地目录，不调用模型或联网下载。
3. 独立导入少量 SVG/PNG 到 Phaser 和 Babylon 工程，真实构建并在禁止外部请求的浏览器与 sandbox 预览中确认图片解码、布局和导出文件哈希。
4. 故障用例覆盖损坏哈希、目录逃逸、符号链接、超配额、畸形 PNG、ZIP 解压预算及跨 run 读取；失败保留明确缺项，不伪造成功。
5. 在线采集另作显式 opt-in：记录实际 provider、请求／响应预算、来源页、下载 SHA 和可用性；网络失败返回 partial/failed。视觉、许可范围和动画适配另审，不把采集成功升级为玩法证据。
6. 任何正式游戏采用新素材后，按新 source SHA 重跑原文要求的机械、视觉和人工／真机项。未执行、未观测或仍需人工确认的项保持原状态。

本文件交付的是静态评估和后续接入步骤，不是素材服务上线或三个游戏通过的证明。相关现状见 [团队组件状态](team-integration.md) 与 [Harness 架构](harness.md)。
