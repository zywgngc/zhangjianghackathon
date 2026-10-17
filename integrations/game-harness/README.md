# 素材子智能体与主流程接入

`zx` 分支包含可运行的根目录 `game_harness/` 源码。整体保持 `codex/game-harness` 的模块划分和处理顺序：需求改写 → 检索 → 游戏设计 → HTML 生成 → 浏览器检查 → QA → 必要时修复。素材子智能体集中在 `game_harness/material_portal.py`；主流程只在提示与生成后嵌入环节增加素材支持。

## 安装与运行

使用 Python 3.10 或以上版本，在仓库根目录安装：

```text
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e .
python -m playwright install chromium
```

将根目录 `.env.example` 复制成 `.env`，填入模型服务地址、模型名和 API Key。密钥文件、下载缓存和生成产物不提交。示例默认启用 DeepSeek 与 `MATERIAL_PORTAL_URL=web://assets`；也可传入已有配置文件路径。

先检查运行条件，随后运行一个明确缩减的 HTML 核心玩法示例：

```text
python -X utf8 tools/run_game_harness.py --env-file .env --request examples/game-harness-assets/number-gate.request.json --check-only
python -X utf8 tools/run_game_harness.py --env-file .env --request examples/game-harness-assets/number-gate.request.json --build-output-tokens 32000
```

检查模式不调用模型；真实运行会调用配置的模型并消耗额度。示例总预算为 300000 tokens、最多 16 次调用、2 轮修复和 1800 秒。结果写入新的 `output/harness-real-*/`，已有目录不会被覆盖。`--harness-root` 默认指向本仓库，可显式指定另一份兼容的主流程源码。

免费测试真实素材搜索与下载：

```text
python -X utf8 tools/evaluate_online_assets.py --case number-gate
```

此命令不读取模型密钥、不调用模型，结果与图片预览写入 `output/online-assets-live/`。不指定 `--case` 时依次执行三个美术需求。

## 保持的组织结构

| 文件 | 职责与变化 |
| --- | --- |
| `game_harness/material_portal.py` | 可独立替换的素材检索、下载、来源许可、缓存、交付与 HTML 嵌入实现。 |
| `game_harness/agents.py` | 沿用原代理与调用方式，允许使用核验的 `asset://` 短引用，约束视角、风格和未知图集的使用。 |
| `game_harness/runner.py` | 沿用原检查点、预算、生成与修复流程；给模型素材元数据，在浏览器验证前统一嵌入原图。 |
| `game_harness/rag.py` | 沿用原 Retriever，素材门户结果与原 RAG 接口共同返回给设计和生成阶段。 |
| `webmcp/material-portal.schema.json` | 原 v0.2 门户协议；美术素材以 `reference_media` 交付。 |
| `asset_agent/` | 保留早期独立素材模块与 CLI；主流程的单文件门户不依赖此包。 |
| `tests/`、`tools/`、`examples/` | 回归、运行工具与输入示例；不依赖本机隐藏源码副本。 |

主流程来源为 `codex/game-harness` 提交 `470bdfebd46f9369d9fd614066ac99bdcb01c738`。`allow-embedded-assets.patch` 保留了相对该版本的两处素材接入差异，供仍在该旧版本上的协作者审阅；当前根目录源码已经包含接入，无需再次应用补丁。默认分支保持原状。

## 联网检索能力

`MATERIAL_PORTAL_URL` 支持三种已有配置方式：

- `web://assets`：实时查询 Kenney 和 OpenGameArt 的 2D 素材搜索，读取详情、确认 CC0 声明并下载实际 PNG/ZIP。
- `local://references`：15 条离线代码参考，供历史兼容与无网检索；不是美术素材联网搜索。
- HTTP(S) URL：调用团队自己的正式 v0.2 远端门户。空配置按原主流程关闭门户。

联网检索从描述、参考图片的文字说明和机制行为提取双语关键词；查询真实网站搜索页，不使用固定素材包清单。仅对允许的公开域名匿名 GET，模型 Key、门户 token 和 GitHub 凭据不会传给素材站。确认来源页的逐条 CC0 声明后，核验 ZIP 路径、解压预算、PNG 结构、CRC、有界解码、尺寸和文件 SHA256。许可不明、仅预览图、格式不支持和超出预算的资源会被跳过并记录原因。

当前每个请求最多处理 3 个概念、22 次 HTTP、40MB 网络响应和 65 秒总时限；单件下载最多 8MB，最多交付 4 个包、每包 6 张 PNG，另受调用方 `limit` 约束。HTTP 缓存有效期 24 小时，复用时核对哈希。公开站点不可用时可能得到部分结果或空结果，调用方应读取 `warnings` / `limitations`。

当前使用可解释的双语词表，没有选材 LLM 或参考图视觉分析。宽泛映射（如 `samurai → rpg`）会披露题材差异；文件名匿名的 tile 不猜测用途。素材可用不等于画风和视角已经匹配。

## 上下游接口

主流程现有调用保持不变：

```python
from game_harness.material_portal import MaterialPortalClient

portal = MaterialPortalClient(settings)
evidence = await portal.search(query, limit=4, run_id=run_id)
# {"documents": [...], "warnings": [...], "trace": {...}}
```

`documents` 包含来源、许可、素材 manifest 和少量图片预览。主流程使用 `prepare_asset_documents()` 将其转换成可用素材元数据与短引用，移除 Base64、manifest 本机路径等内容再发送给模型；下载文件和完整证据留在本地。

需要正式协议或独立素材交付时：

```python
result = await portal.search_request(v02_request)
# 严格符合 v0.2 MaterialSearchResult
handoff = await portal.collect_assets(v02_request)
# {"result": ..., "assets": [...], "manifests": [...], "limitations": [...]}
```

正式请求使用 `purpose="archetype"` 或 `"mechanic"`；`mechanic` 继续按协议携带 `mechanicId`、`requiredBehavior` 等字段。候选为 `kind="reference_media"`、`license.scope="assets"`，不把美术图片冒充玩法实现证据。PNG 本身可供 HTML、Phaser 或 Babylon 使用，主生成流程当前仍只支持原生 Canvas/DOM 的单文件 HTML。

每张交付素材包含 `assetId`、包内相对 `path`、Python handoff 的 `absolutePath` / `bundleRoot`、`sha256`、尺寸、格式、字节数、原始文件名、来源 URL、许可和作者。移动整个素材包后，manifest 的相对路径仍可读取。实际文件在 `DATA_DIR/material-assets/bundles/`，搜索审计在 `material-assets/searches/`，请求状态在 `material-portal.sqlite3`。

## 离线成品与修复

模型生成或修复源码时引用 `asset://<assetId>`，避免复制大量 Base64。`embed_asset_documents()` 核验 manifest 与文件，统一将多包短引用换成真实 PNG data URI，并附不参与页面布局的来源许可元数据。未知 ID、文件损坏和超过 300000 字节的最终 HTML 会失败，不把无法读取的资源交给浏览器。

每版保留：

- `vN.source.html`：模型产生的未内嵌源码，供下一轮修复读取。
- `vN.html`：已内嵌素材、供浏览器运行与 QA 检查的成品。
- `vN.asset-usage.json`：实际引用的素材 ID 与成品大小。
- `vN.report.json`：静态与浏览器检查结果。

浏览器仍禁止外部资源请求，成品可离线运行。显示尺寸由 CSS 或 Canvas 的 `drawImage` 控制，无需改动原始 PNG。独立使用时也可调用 `embed_assets(html, manifest_path)` 处理单包素材。

## 验证与当前边界

运行不调用付费模型的回归：

```text
python -m pip install -e ".[test,images,research]"
python -X utf8 -m pytest tests/test_harness_material_portal.py tests/test_harness_portal_relevance.py tests/test_harness_portal_web.py tests/test_online_asset_bundle.py tests/test_asset_document_embedding.py tests/test_asset_harness_integration.py
```

测试覆盖协议与幂等、HTTP、PNG/ZIP 校验、缓存损坏、许可、限额、取消，以及多包素材经过设计、生成、修复后进入离线 HTML 的流程；网络使用可控模拟响应。上面的 `evaluate_online_assets.py` 单独验证真实公开网站，真实生成命令验证模型与 Chromium。

既有真实素材实测共下载 44 个 PNG，覆盖三个游戏需求。随后提高预算跑了数字门枪手一例：8 次模型完成、199226 tokens、初版与两轮修复，最终实际使用 3 张 PNG；独立检查 40 项通过、4 项失败。Boss 通关和单步触控通过，自动修复引入数字门不显示、提前结算等回归，自动 QA 未批准。因此素材接入已经验证，整体生成质量仍需改进；不能把这些结果视为三个原始完整游戏的验收通过。

目前覆盖两家网站的 2D PNG/ZIP，未实现通用全网搜索、3D/音频/字体交付、动画分帧、视觉匹配或自动统一画风。找不到合适素材时返回缺项和原因并保留成功结果，不以无关候选补数。原始长用例的 Phaser/Babylon、TypeScript/Vite、多文件工程等要求仍不在当前 HTML 主流程范围内。运行产物和本机缓存不随源码发布，以上脚本可在新环境重新生成结果。
