# Web Game Generator

中文描述 → 需求整理 → 知识与素材检索 → 设计 → HTML 生成 → 浏览器检查 → QA / 修复。交付能在电脑和手机浏览器打开的离线单文件游戏，以及版本、来源和测试记录。

`zx` 分支包含完整主智能体和素材子智能体。主流程沿用 `codex/game-harness@470bdfebd46f` 的模块划分，素材接入集中在 `game_harness/material_portal.py`；`agents.py` 与 `runner.py` 补充已核验图片的选择、短引用和内嵌交付。

## 安装与运行

需要 Python 3.10+，建议 3.11。在仓库根目录执行：

```sh
python -m venv .venv
# Windows PowerShell
.venv/Scripts/Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[test,images,research]"
python -m playwright install chromium
```

复制 `.env.example` 为 `.env` 并填写 `OPENAI_API_KEY`。示例采用已试跑的 DeepSeek 配置，预算为 300000 token、最多 16 次调用与 2 次修复；模型名、接口和各角色模型均可调整。截图 QA 需要模型支持图像输入；使用纯文本模型时设 `VISION_QA=false`。

```sh
python -X utf8 -m uvicorn game_harness.app:app --host 127.0.0.1 --port 18080 --workers 1
```

浏览器打开 `http://127.0.0.1:18080`。同一个 `DATA_DIR` 只启动一个服务进程，Windows 与 Linux 均使用文件锁防止重复启动。运行产物位于 `data/`，密钥、缓存、虚拟环境和产物均排除提交。

也可绕过工作台，运行一个已明确缩减为 Canvas 核心玩法的例子：

```sh
python -X utf8 tools/run_game_harness.py --request examples/game-harness-assets/number-gate.request.json --build-output-tokens 32000
```

该入口默认加载 `.env`；若本机已有 `agent.env` 则优先使用它，可用 `--env-file .env` 明确指定。加 `--check-only` 只检查配置与浏览器，不调用模型。

## 源码组织

```text
game_harness/          主流程、API、工作台、模型、RAG、验证与持久化
  material_portal.py   可单文件替换的素材门户；主流程实际使用此模块
  knowledge/          随项目维护的玩法与适配知识
  static/             工作台页面
asset_agent/          独立素材库、检索、缺失后备与 resize 模块
webmcp/               主流程使用的门户契约、schema 与协议示例
catalogs/             小型固定素材/参考代码测试库，附来源与许可
examples/             Python 接入示例、门户请求与缩减玩法请求
tests/                主流程、素材检索、内嵌交付与浏览器测试
tools/                命令行试跑、素材评测与玩法复验工具
scripts/              模型探针、用例检查与 Linux 启动入口
docs/                 运行架构、团队接入与验证说明
integrations/         素材接入说明与协议兼容资料
case*_*/              原始长规格与参考图，文件内容保持不变
```

完整主流程从本地隔离目录归入上述标准目录，无需 `.asset-agent/` 中的源码副本。保留独立 `asset_agent/` 模块以支持现有调用和素材处理；主流程的门户不依赖该包。原始规格中的冒号目录名在 Windows 上映射为下划线，规格及参考图字节保持原样。

## 素材子智能体

设置 `MATERIAL_PORTAL_URL=web://assets` 后，门户从 Kenney / OpenGameArt 检索公开美术资源，执行受限下载、许可与文件检查，并返回带来源、尺寸、角色提示和 `asset://...` 短引用的结构化素材。生成器选择图片后，主流程将核验后的 PNG 内嵌到 HTML；来源与许可保留为不影响页面排版的元数据。失败或无匹配时返回限制，不伪造找到素材。

`local://references` 检索固定的玩法代码参考，不能代替在线美术检索；也可以设置 HTTP(S) 门户地址调用团队实现。在线检索本身无需模型密钥。独立 `asset_agent/` 另提供本地图库导入、可选 API 选材、缺失素材后备与 resize，详情见 [模块说明](asset_agent/README.md)。

初次接入请读 [素材接入说明](integrations/game-harness/README.md)、[主流程架构](docs/harness.md) 和 [团队接口](docs/team-integration.md)。当前运行时支持原生 Canvas / DOM；不承诺完整复刻三个 Phaser / Babylon 长规格。素材搜索命中、画风一致和最终玩法正确性仍需要各自验证。

## 验证

```sh
python -X utf8 -m pytest -q
# PowerShell: $env:RUN_BROWSER_TESTS="1"
# macOS / Linux: export RUN_BROWSER_TESTS=1
python -X utf8 -m pytest -q tests/test_browser.py tests/test_workbench.py
python -X utf8 scripts/check_benchmarks.py
```

普通测试使用模拟模型/网络，不消耗模型额度；浏览器测试需要已安装 Chromium。固定 catalog 保留上游原始字节，以便 SHA-256 校验。实际生成效果和已知缺陷见 [验证记录](docs/source-validation.md)。

## 架构设计资料

Proposed multi-agent architecture for browser games, including mobile browsers.

[Editable Markdown / Mermaid](web-game-generator-mas-plan.md) · [Standalone HTML export](web-game-generator-mas-plan.html)

## 1. System architecture

![System architecture](diagrams/system-architecture.svg)

[SVG](diagrams/system-architecture.svg) · [Editable DOT source](diagrams/system-architecture.dot)

## 2. Engine routing

![Engine routing](diagrams/engine-routing.svg)

[SVG](diagrams/engine-routing.svg) · [Editable DOT source](diagrams/engine-routing.dot)

## 3. Developer choices during generation

![Developer choices during generation](diagrams/developer-decisions.svg)

[SVG](diagrams/developer-decisions.svg) · [Editable DOT source](diagrams/developer-decisions.dot)

The HTML embeds all three diagrams and works offline. Download it to use the print / Save PDF controls. The Markdown contains editable Mermaid diagrams; the DOT files can be rendered with Graphviz.
