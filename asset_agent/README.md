# 游戏材料与美术素材检索子智能体

提供两条独立流程：正式 Webmcp v0.2 请求检索游戏仓库、源码、算法文档与参考媒体；旧版布局 + prompt 请求检索、调整并交付图片、模型、音频和字体。版本字段决定接口路径，材料类型不会互相冒充。

独立模块自身兼容 Python 3.9+；本仓库整合后的安装环境要求 **Python 3.10+**。旧美术流程零第三方运行依赖；位图缩放使用可选 Pillow；正式 v0.2 契约校验使用可选 `research` 依赖。模块不生成完整游戏，也不代替 MAS 的规划、项目状态或全局追问预算。当前主流程实际使用 `game_harness/material_portal.py` 联网检索与内嵌图片，见 [接入说明](../integrations/game-harness/README.md)。

## 正式 Webmcp v0.2 接入

依据远端 `Webmcp` 分支 `437981e281281994c1f2377ab69b3c7871943b2e` 的正式提案开发。该版本的 `archetype` 指游戏原型来源，`mechanic` 指功能机制实现来源，材料类型是 `game_repository / source_code / algorithm_documentation / reference_media`。前面的美术接口继续作为兼容路径；不会把美术图标映射成源码候选。

安装到你使用的项目环境：

```powershell
python -m pip install -e ".[research]"
```

```python
import json
from pathlib import Path
from asset_agent import MaterialPortal

request = json.loads(Path("examples/webmcp-v02/mechanic-request.json").read_text(encoding="utf-8"))
with MaterialPortal(
    reference_catalog_dir="catalogs/references",
    research_cache_path=".asset-agent/research-requests.sqlite3",
) as portal:
    response = portal.search_sync(request)
    # async 主流程：response = await portal.search(request)
```

也可直接使用 `ResearchMaterialPortal`；CLI 支持文件和标准输入：

```powershell
python -m asset_agent research examples/webmcp-v02/mechanic-request.json
python tools/evaluate_research_portal.py
```

正式 Schema 原样保存在 `asset_agent/specs/material-portal-0.2.schema.json`，TS 类型在 `integrations/webmcp/`，来源与文件哈希记录在 `asset_agent/specs/provenance.json`。`examples/webmcp-v02/` 是原始**合成契约测试 fixtures**；输入示例可以调用真实检索，响应 fixtures 的候选不能加入真实索引。

已实现的边界：

- 按原始 Schema 校验完整请求与响应，回显 request/run/thread/round，校验候选类型、数量和唯一 ID；`limit` 为 1–10。
- 返回 `completed / partial / needs_clarification / failed`；零候选可以 `completed`，不表示机制已实现。已缓存父请求时校验澄清问题、默认值、答案与父链。
- SQLite 持久保存请求指纹和结果；相同 ID 改变输入会拒绝，并发相同请求仅派发一次。超时保留 pending 操作，重复调用读取原任务，不自动重复派发；完成缓存可跨实例读取。
- 支持同步 `options={"signal": threading.Event()}` 和 asyncio 取消；供应商按 `cancel_event.is_set()` 协作取消。预期错误返回正式错误码，显式取消抛 `CancelledError`。
- 默认禁止 mock 供应商和缓存 mock 结果；测试须明确 `allow_mock=True`，仍保留 `isMock:true`。
- 当前图片解析未接入，带 `images` 的搜索返回 `partial` 和明确限制；本地磁盘图片路径会拒绝。不会伪称图片参与排序。

本地真实参考库包含 **13 条候选、4 个公开仓库、24 份固定提交的源码/许可文本**，见 `catalogs/references/README.md`。查询只搜索本地元数据，不隐式访问网络、LLM 或付费服务。每条候选保留 revision、路径/符号、证据 URI、许可范围与限制。检索仅建立 discovered 来源候选，未安装运行上游游戏，也没有行为通过结论。可显式注入自己的同步 provider 对接后续检索服务。

完整接入说明与能力清单见 `integrations/webmcp/README.md` 和 `provider-capabilities.json`。当前按已选定的 **Python 模块**接入，没有部署远端 MCP 服务或浏览器工具。全局三轮追问预算、项目 revision 接受策略、最终候选评审和 Builder 仍由上游 MAS 负责；本模块只校验请求中的 round 范围。默认允许无本地父记录的完整快照跟进，并报告由上游验证；需本地严格父链时设置 `require_parent=True`（门面参数为 `require_research_parent=True`）。

进程中断前未完成的 pending 请求不会自动重放；可用 `get_request_status()` 检查，远端任务的对账/恢复需要供应商适配器提供相应能力。超时不是取消；调用方显式取消才停止接受该任务结果。

`tools/evaluate_research_portal.py` 用三个 tests 原型查询及六个机制查询执行真实索引检索，记录原始请求、正式响应和来源缺口。当前可发现武侠幸存者原型及路径、波次、索敌、战斗、经济、成长参考；数字门枪手和百炼英雄的完整仓库候选仍缺失，结果保留零候选。此结果不等于三个游戏的实现通过率。

```text
布局 + prompt
  → 查询规划（可接主 agent 的 LLM）
  → 类型 / 授权 / 风格 / 格式 / 动画 / 体积硬筛选
  → BM25 检索 + 可选向量相似度
  → 无候选槽位按需换词重试（默认关闭，不放宽硬约束）
  → 候选集约束选材（可接主 agent 的 LLM）
  → 显式 resize 请求生成派生文件 / 来源 / 哈希 / 最终预算校验
  → manifest.json + 素材文件 + CREDITS.txt + 缺口清单
```

## 美术素材：布局与文件交付

在仓库根目录运行，无须安装依赖。也可以 `python -m pip install -e . --no-build-isolation` 后从其他项目导入。

```python
import json
from pathlib import Path
from asset_agent import AssetAgent

request = json.loads(Path("examples/racing-ui.json").read_text(encoding="utf-8"))

with AssetAgent("catalogs/demo") as agent:
    # 只检索和规划，保留 transform_request，不写文件或执行缩放：
    plan = agent.plan(request)
    # 检索、选材、按需缩放并交付真实资源，返回与 manifest.json 一致的字典：
    handoff = agent.run(request, output_dir="output/my-first-bundle")

assert handoff["status"] == "ready"
for selection in handoff["selections"]:
    print(selection["slot_id"], selection["file"]["path"])
```

输出目录必须不存在或为空。重复运行时使用新的输出目录，已有文件不会被覆盖。

可运行的接入示例：

```powershell
python -m examples.main_agent_integration
python -m asset_agent validate --catalog catalogs/demo
python -m asset_agent search "氮气加速" --catalog catalogs/demo --type icon
python -m asset_agent run examples/racing-ui.json --catalog catalogs/demo --output output/racing-demo-cli
python -m asset_agent run examples/racing-ui-resize.json --catalog catalogs/demo --output output/racing-resized-cli
python -m asset_agent run examples/missing-character.json --catalog catalogs/demo --output output/missing-character-cli
python -m unittest discover -s tests -v
```

三个游戏请求分别演示「完整图标交付」「按指定尺寸交付 SVG」与「缺少带动画的 3D 模型时明确返回缺口」。示例素材已经随仓库提供，正常检索与交付不访问网络。SVG 调整不需要 Pillow。

## 兼容旧版：美术阶段检索接口

不带 `contractVersion/requestId` 的 `purpose / description / imageRefs / constraints / limit` 请求继续使用旧版美术语义；也可显式调用 `ArtMaterialPortal`。这个兼容接口与正式 v0.2 是不同契约，不会猜测转换。

```python
from asset_agent import MaterialPortal

portal = MaterialPortal("catalogs/demo")
request = {
    "purpose": "mechanic",
    "description": "暂停 pause HUD icon，透明 SVG 图标",
    "imageRefs": [],
    "constraints": {"engine": "Babylon.js", "platform": "web", "language": "TypeScript"},
    "limit": 5,
}
response = portal.search_sync(request)
# 在主流程的 async 函数中也可使用：
# response = await portal.search(request)
```

返回 `{candidates: [...], limitations: [...]}`。每个候选当前采用 `id / title / description / assetType / format / score / reasons / resource / source`；`resource` 包含 `kind: "local_file"`、绝对 `path`、实际文件的 `sha256 / bytes`，`source` 包含来源 URL、作者和授权。这是当前 Python 适配层的候选约定，主工程确定正式 `MaterialCandidate` 后可再映射字段。

`limit` 为 1–30 的整数，平台限定 `web`。描述中显式出现图标、3D 模型、精灵、图块、音频或字体时，使用文本规则限制素材类型，避免用奔跑图标冒充角色动画。这不是完整语义理解；建议上游按具体素材拆分请求。`archetype` 返回的单个候选也不代表已经组成整套一致风格的美术包。

当前 `imageRefs` 会接受并校验字符串，**尚未读取图片或参与排序**；`engine / language` 只保留为语境，未执行运行兼容性验证。默认使用本地 BM25，分数为相对排序。可以显式注入已有的 `embedding_provider`，不会自动配置模型或访问付费服务。所有这些限制都会随响应返回。`search` 不会执行 resize、生成素材或交付文件包；需要交付时，仍使用明确布局槽位的 `AssetAgent.run()`。

### tests 分支用例的覆盖评测

用例来自远端 **`tests`** 分支，固定版本 `81fc5366aa417754ccdba07d5d3dc08daa8f6a02`。Git 连接失败时通过经用户授权的官方只读 API 同步到 `.asset-agent/references/tests-81fc5366aa41/`，没有合并或切换开发分支。源路径中的冒号在 Windows 下映射为下划线，原路径、Git blob 和 SHA-256 记录在 `_snapshot_manifest.json`；评测前校验本地快照。

`examples/material-portal-cases.json` 从三份规格中人工抽取 36 条代表性美术请求并保留原文行号。每例各含 6 条核心美术请求与 6 条通用 UI 请求；不是完整资产清单。百炼英雄的文字明确要求开放场地，参考图的塔楼不用于改写该要求；该例也不添加金币或常规 HP 资源需求。

```powershell
python tools/evaluate_material_portal.py --snapshot path/to/verified-tests-snapshot
```

这个历史评测工具需要用 `--snapshot` 显式指定相同版本、带校验清单的快照；快照不随源码交付，也不是主流程运行依赖。根目录的三份原规格可通过 `python -X utf8 scripts/check_benchmarks.py` 检查。历史评测输出写入新的 `output/material-portal-evaluation-*.json`，保留每条请求、候选、限制和来源行号。当前 18 个图标的目录中，18 条 UI 请求均在前 5 个候选内找到对应功能图标（共 8 个不同图标），其中 17 条排在首位；百炼英雄的继续游戏请求把暂停排在了继续之前。18 条核心角色、场景、动画或音频请求没有候选。**这些是图库覆盖结果，不是三个游戏的完整美术通过率，也没有验证整体风格、颜色、动画或实际渲染效果。**

## 上游输入约定

完整样例在 `examples/racing-ui.json`，机器可读约定在 `schemas/asset-request.schema.json`。

```json
{
  "request_id": "game-001",
  "prompt": "卡通赛车，深色界面，暂停按钮采用白色剪影图标",
  "style": "silhouette",
  "layout": {
    "viewport": {"width": 1280, "height": 720},
    "slots": [
      {
        "slot_id": "pause",
        "type": "icon",
        "description": "暂停比赛",
        "tags": ["pause"],
        "bounds": {"x": 1200, "y": 20, "width": 48, "height": 48},
        "required": true
      }
    ]
  },
  "constraints": {
    "licenses": ["CC0-1.0", "CC-BY-3.0"],
    "max_asset_bytes": 65536,
    "max_total_bytes": 262144,
    "top_k": 5
  }
}
```

- `slot_id` 必须唯一，供下游把资源绑定回原布局。`bounds` 是设计视口内的坐标，子智能体原样保留。
- `type` 支持 `icon / sprite / tileset / model / audio / font`。
- 每个槽位可增加 `formats`、`packs`、`style`、`required_animations`；模型可受全局 `max_triangles` 限制。缺少三角数元数据的模型不能通过三角数上限筛选。
- `anchor`、`responsive`、`tint` 是传给下游的渲染提示，本模块不执行排版或变色；只有显式的 `resize` 会处理素材文件。
- 风格是精确标签约束；素材的 `generic` 风格可通配。建议入库时统一风格名称。
- 默认仅接受 `CC0-1.0` 和 `CC-BY-3.0`，预算为单个资源 8 MiB、总计 32 MiB；可显式调整。
- `min_score` 默认 0.01，剔除零匹配候选。排序分数不是概率，不能跨查询解释成置信度。
- 离线模式只用槽位描述和标签构造查询，避免整个游戏 prompt 中的某个关键词污染所有槽位；模糊槽位应启用下面的查询规划回调。

### 显式调整交付素材尺寸

`bounds` 表示游戏里如何摆放、显示素材，`resize` 表示生成一个什么尺寸的交付文件；两者互相独立。未设置 `resize` 的素材继续原样复制。

```json
{
  "slot_id": "pause",
  "type": "icon",
  "description": "暂停按钮",
  "bounds": {"x": 1200, "y": 20, "width": 48, "height": 48},
  "resize": {
    "width": 48,
    "height": 48,
    "mode": "contain",
    "resample": "lanczos",
    "allow_upscale": false
  }
}
```

`width`、`height` 必须是 1–8192 的整数，目标总像素还受运行时的 16M 上限限制。`mode` 支持 `contain`（默认，保持比例完整容纳）、`cover`（保持比例填满并裁切）和 `stretch`（直接适配宽高）；`resample` 支持 `lanczos`（默认）及像素素材常用的 `nearest`。位图默认 `allow_upscale: false`：`contain` 会保留较小的图像内容并补透明边，`cover` / `stretch` 如果需要放大则报错。SVG 是矢量图，可以按目标尺寸显示，`allow_upscale` 和位图重采样不限制其缩放。

当前仅处理静态 `icon` / `sprite`。SVG 修改 viewport、viewBox 相关显示参数并保留 SVG，不转换为位图；SVG 请求不应设置位图 `output_format`。PNG、JPEG、WebP 使用 Pillow，可显式指定 `output_format: "png" | "webp" | "jpeg"`；没有指定时通常保留原位图格式，但 JPEG 出现透明补边时会自动改为 PNG 并记录格式转换。显式要求 JPEG 而产物需要透明度时会报错。动画、图集重排、变色和模型转换不在本轮处理范围。

配置 `resize` 后，检索会在取前 K 个候选之前排除带动画元数据的素材；没有标注的位图动画会在实际解码时拒绝。SVG 处理面向自包含的静态矢量图：拒绝外链、嵌入图片、可执行内容和可能覆盖目标尺寸的根节点/样式表 CSS 几何声明；普通内部路径、渐变及填充样式可保留。

`slot.formats` 筛选的是 catalog 中的**原始格式**，`resize.output_format` 控制的是派生文件格式。例如可以用 `formats: ["png", "webp"]` 检索位图，再指定 `output_format: "png"`；若希望输出位图，应同时限定可接受的位图源格式，不能依赖该字段把检索到的 SVG 自动转成 PNG。

处理位图前安装可选依赖：

```powershell
python -m pip install -e ".[images]" --no-build-isolation
```

在本仓库安装 `python -m pip install -e ".[images]"`，包名为 `zhangjiang-game-harness`，`game-asset-agent` 命令保持可用。源素材始终保留在 catalog，派生文件只写入交付包。`.plan()` 会保留 `transform_request: {"resize": {...}}`，不会生成派生文件；`.run()` 才真正执行处理。检索阶段的单文件筛选及规划阶段的总预算仍按原件字节保守执行，因此一个原件若超过限制，不会先缩小再争取入选；交付阶段再按实际派生产物复核单文件和总预算。处理失败或最终文件超出预算会报错，不会交付不完整的成功包。

## 接入主 agent 的 LLM：RAG 选材

主流程已经有模型客户端时，注入两个 Python 回调即可；本模块不会另起聊天会话、隐式访问模型或读取密钥。

```python
with AssetAgent(
    "catalogs/demo",
    query_builder=build_queries_with_your_model,
    selector=select_assets_with_your_model,
) as agent:
    handoff = agent.run(request, "output/model-selected-bundle")
```

`build_queries_with_your_model(context)` 接收完整游戏描述、布局和约束，返回解析后的 Python 字典：

```json
{"queries": [{"slot_id": "pause", "query": "暂停比赛 pause"}]}
```

它用于把「主要操作」「玩家角色」等模糊槽位，结合 prompt 扩展为具体检索词。未返回的槽位沿用原描述。未知槽位、重复槽位和空查询会被拒绝。

### 无候选时有限重试

请求顶层可以设置：

```json
{"missing_policy": {"retry_rounds": 2}}
```

`retry_rounds` 范围为 0–2，默认 0。大于 0 时必须注入 `query_builder`。首次检索没有候选时，最多再让查询规划回调换词 N 次，仅重试没有候选的槽位；授权、类型、风格、所需动画和预算约束始终保持不变。已有候选但被选择器省略、复用限制或总预算淘汰的槽位，不因此获得约束豁免。

查询回调 context 在原有 `request` 之外包含 `phase`（`initial` / `retry`）和 `retry_round`；重试阶段还提供 `previous_queries` 和 `missing_slots`。回调仍返回 `{"queries": [{"slot_id": "...", "query": "..."}]}`，重试阶段应只为缺口槽位提供新查询。主 agent 可结合已尝试词与缺口描述改写查询，不需要另起一个子 agent 会话。

重试后仍找不到资源，会继续返回 `partial` 或 `unresolved`；缺口记录含 `attempted_queries`、`actions`、`generation_brief`。`actions` 是带 `type` 和 `status: "suggested"` 的建议动作对象；`generation_brief` 是 `status: "requested"`、`context.dispatch_status: "not_dispatched"` 的待生成任务，预算不足类缺口则为 `null`，优先建议选择更小资源或由主流程决定是否调整预算。这些字段不表示已调用外部生成器。必需槽位不会被自动替换成不相关资源或占位图；可选槽位可以省略。

`select_assets_with_your_model(context)` 接收原始请求，以及每个槽位真实检索得到的候选元数据、体积和排序说明，返回：

```json
{
  "assignments": [
    {
      "slot_id": "pause",
      "asset_id": "game-icons/guard13007/pause-button",
      "reason": "暂停符号与该槽位语义一致，且符合剪影风格与体积要求"
    }
  ]
}
```

候选不合适时省略该槽位。回调只能选择该槽位候选集中的 ID；虚构 ID、跨槽位选择、重复分配均会报错。模型返回后仍会复核预算、复用限制和文件完整性。

系统提示词模板在 `prompts/query-builder.txt` 和 `prompts/selector.txt`。建议主 agent 以结构化输出模式调用自己的模型，再将解析后的字典返回。素材描述属于检索证据，不应当作模型指令。

未传 `selector` 时，使用确定性的排序策略，报告为 `ranked_policy`；传入回调后报告为 `rag_callback`，该名称表示调用了选择接口，不证明回调内部实际使用了哪种模型。离线基线可以直接运行，但不等同于已经启用了 LLM RAG。

## 可选向量检索

可注入任意实现以下接口的本地或远程 embedding provider：

```python
class MyEmbeddingProvider:
    model_id = "my-model-name-and-version"

    def embed(self, texts):
        # 返回与 texts 一一对应、维度一致的有限浮点数向量。
        return your_existing_embedding_client(texts)
```

也提供 HTTP `/embeddings` 协议适配器：

```python
from asset_agent import AssetAgent, HTTPEmbeddingProvider

provider = HTTPEmbeddingProvider(
    base_url=your_embedding_base_url,  # 包含服务所需的 /v1 等路径
    model=your_embedding_model,
    api_key=your_embedding_api_key,
)
with AssetAgent(
    "catalogs/demo",
    embedding_provider=provider,
    cache_path=".asset-agent/embeddings.sqlite3",
    query_builder=build_queries_with_your_model,
    selector=select_assets_with_your_model,
) as agent:
    handoff = agent.run(request, "output/hybrid-bundle")
```

HTTP 请求体为 `{model, input: [text, ...]}`，响应需包含与输入对应的 `data[].embedding`，可带 `index`。base URL 不允许携带用户名或密码；密钥只用于请求头，不写入 manifest。

当前混合排序为 `0.65 × 归一化 BM25 + 0.35 × max(0, 余弦相似度)`。向量按服务端点、模型身份、文本哈希缓存到 SQLite。模型空间变更应更新 `model_id`。服务失败时明确报告 `lexical_fallback` 和原因，不伪称使用了向量。

原型没有下载或验证真实向量模型；单元测试使用假 provider/模拟 HTTP，仅验证协议、融合、缓存隔离和降级。后续应以你们的素材与查询集评估真实模型检索质量。

## 素材库与导入

目录结构：

```text
catalogs/demo/
  catalog.json
  UPSTREAM-LICENSE.txt
  assets/<author>/<icon>.svg
```

当前提供 18 个来自 [Game-icons 官方仓库](https://github.com/game-icons/icons) 的真实 SVG 图标，固定上游版本为 `82d948812bfe3f269ef8f731dcdb07b08160edc4`。每条记录保留作者、CC BY 3.0 许可、来源 URL 和 SHA-256。**样例库仅覆盖图标，尚未预装角色、场景、3D 模型或音效。**

正常使用无需下载。需要重建时，可以在联网环境显式运行：

```powershell
python tools/bootstrap_demo_catalog.py --output catalogs/demo-rebuilt
```

导入其他来源时，先下载并解压素材包，再显式声明来源、授权和风格：

```python
from asset_agent import import_pack

result = import_pack(
    source_dir="downloads/kenney-ui",
    catalog_dir="catalogs/library",
    pack="kenney-ui",
    style="flat",
    license_id="CC0-1.0",
    author="Kenney",
    source_url="https://kenney.nl/assets/ui-pack",
    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    tags=["ui", "flat"],
)
```

这个调用不负责在线下载，也不会猜测授权。源目录与目标目录应分开，不能互相包含。相同文件和元数据重复导入会跳过，内容变化或未知文件冲突会报错，不覆盖已有内容。

可在素材包内增加 `pack.json`，给单个资源补充语义和动画要求：

```json
{
  "defaults": {"tags": ["ui", "flat"]},
  "assets": {
    "icons/coin.svg": {
      "name": "金币",
      "description": "金币计数、货币与收集奖励",
      "tags": ["coin", "currency", "金币", "flat"],
      "type": "icon"
    }
  }
}
```

允许覆盖 `name / description / tags / type / animations / width / height / triangles / attribution`；`tags` 覆盖而非合并。格式、路径和来源由导入流程管理。图集的帧、碰撞体、骨骼重定向、模型单位与朝向目前需在资源准备阶段处理，本模块不自动生成。

导入支持 SVG/PNG/WebP/JPG、GLB/glTF、WAV/OGG/MP3、WOFF/WOFF2/TTF/OTF。自动读取 SVG/PNG 尺寸和 glTF/GLB 动画名；其他允许格式原样复制，不做完整解码。只支持资源内嵌的自包含 glTF/GLB，外部 `.bin` 或纹理引用会被拒绝，需要先打包到模型内部。

模型动画元数据只可声明文件中实际存在的 clip；未命名动画不会被编造成可按名字播放的标签。检索匹配可以忽略大小写，交付时会恢复文件中的真实动画名称。

## 下游交付约定

机器可读约定在 `schemas/asset-handoff.schema.json`。

```text
output/my-first-bundle/
  manifest.json
  CREDITS.txt
  assets/<content-hash>.svg
```

`manifest.json` 含原布局、逐槽位的 `asset_id`、资源格式、渲染方式、相对路径、SHA-256、字节数、来源授权、选材理由、候选替代项和未解决槽位。

有实际变换时，`selection.format` 和 `metadata.width / height` 描述最终交付文件；`file` 的路径、哈希、字节数以及 `usage.runtime_url` 全部指向派生产物。`source_file: {sha256, bytes, format}` 单独记录原件指纹，不提供原件运行路径；`source_metadata` 保留原元信息，`transforms` 记录实际执行操作。`CREDITS.txt` 也记录变换。下游应加载 `file` 指定的结果，不能把 `expected_file` 或 `source_file` 的原件哈希拿来校验派生文件。

```python
bundle_dir = Path("output/my-first-bundle")
for item in handoff["selections"]:
    local_file = bundle_dir / item["file"]["path"]
    runtime_url = item["usage"]["runtime_url"]
    # 下游按 slot_id 绑定资源，并根据 render_as / fit / bounds 渲染。
```

主流程需要把 `bundle_dir` 与 `handoff` 一起交给下游；如果上传到对象存储，则传交付包的根 URL。清单内部使用相对路径，移动整个目录后仍可使用。下游应先检查 `status`，再决定是否接受部分素材或要求补充。

主 agent 处理缺口时，应遍历 `unresolved`：必需槽位先根据 `actions` 和 `generation_brief` 安排人工补库、连接已有生成工具或调整产品需求；如果获得新素材，先显式确认来源授权并导入，再重新检索。可选槽位可按原布局约定省略。当前模块只提供这些待处理任务，没有接入在线素材供应商，也不会自动执行生成或制造占位资源。

- `ready`：所有必需槽位已满足；可选槽位仍可能出现在 `unresolved`。
- `partial`：已有选材，但仍有必需槽位未满足。
- `unresolved`：必需槽位未满足且未选出任何资源。
- 文件错误、非法回调、哈希变化或复制失败会抛异常；不会交付一个伪造的 ready 包。

交付会在临时同级目录完成校验后整体发布。相同内容的资源只保留一份；同一素材用于多个槽位时保留多条绑定，不同 resize 请求可以产生不同文件。规划阶段的预算估算不能代替最终校验：交付时按实际派生文件字节重新检查单文件和总预算，不含 manifest/署名文本和运行时解码内存。最终游戏应保留 CREDITS 中所需的作者、来源、许可和修改说明。

## 协作与后续扩展

- 主流程接 `AssetAgent.plan()` 或 `.run()`，不需要启动 HTTP 服务。每个并行工作线程使用独立实例，SQLite 缓存连接不跨线程共享。
- 接入自有 LLM 与 embedding 后，用独立评测集验证相关性、风格一致性、无结果判断和交付成功率。
- 扩大资源覆盖时，先导入完整风格包并补充中英标签；预览图多模态检索、在线供应商连接器、自动模型转换与大规模向量索引目前未实现。
- `asset_agent/catalog.py` 与 `retrieval.py` 管目录和搜索；`agent.py` 管查询/选材；`ingest.py` 管导入；`delivery.py` 管交付。CLI 使用同一套 Python 接口。
