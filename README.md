# 游造 · 中文游戏长程生成 Harness

输入完整中文游戏规格，由规划、编码、修复和独立验证代理持续协作，交付 **React + TypeScript + Vite + Phaser / Babylon.js 多文件工程、离线运行资源和逐项验收证据**。系统默认使用 `qwen3.8-max`，通过 OpenAI 兼容 Chat Completions 接口调用。

v2 已补齐长规格入口、真实第三方引擎模板、工具式编码循环、功能 DAG、检查点恢复及项目交付。**三个官方大型 case 已在 210 使用完整原文开始真实实现，目前尚未全部验收通过。** 引擎模板或 Python 回归测试通过，不代表这些游戏已经完成。

已合并团队新增的 [WebMCP 研究服务与工作台](webmcp/runtime.md)，包括持久任务、受限讨论及三份参考规格；GitHub 原型检索默认仅读本地缓存，显式开启后受持久请求预算和冷却保护。Node 研究服务的材料门户尚未配置，只有显式 fixtures 模式使用合成候选。Python 单文件 HTML 流程现可显式启用真实美术检索，见 [素材组件](docs/material-assets.md)。组件边界见 [WebMCP 交接文档](webmcp/handoff.md)。

本集成分支增加可选的 `/api/research` 适配器与 `/research` 工作台入口，见 [WebMCP 组件接入说明](docs/webmcp-integration.md)。研究组件不接管项目编排、编码、验收或发布；固定蓝图的接收钩子目前仅接入 v1 `/api/runs`，v2 `/api/projects` 仍由 harness 负责人维护。

团队整合分支 `codex/final-webmcp-harness` 的启动与请求保护补充也已纳入当前开发分支；其固定基线、验证范围和待接接口见 [整合交接记录](docs/final-webmcp-harness.md)。团队同步使用默认分支 `prompting`。

## 两条工作流

| 模式 | 输入与引擎 | 执行与交付 |
| --- | --- | --- |
| 完整规格项目（v2，默认） | 最多 1,000,000 字符；保留原文与源 SHA；锁定 Phaser / Babylon.js | 功能 DAG → 有文件归属的编码工具循环 → TypeScript / Vite 构建 → 独立浏览器、数值与视觉验证 → 修复；交付项目 ZIP |
| 单文件小游戏（v1） | 最多 8,000 字符；Canvas / DOM | 需求改写 → RAG → 设计 → HTML 生成 → 三尺寸浏览器与截图 QA → 有限修复；交付 HTML |

完整规格模式直接执行已批准的规则，不通过简短 rewrite 删减技能、关卡、数值或验收项。工作台支持完整 TXT 导入、三个官方 case 按钮、功能依赖进度、逐 T 验收状态、原文和详细报告下载。构建后的候选可以预览及下载，但会明确标注“尚未全部验收”；只有完整发布门禁通过才显示正式项目。

## 可选素材组件与本地运行

本次从 `zx` 精简合入联网素材收集与 HTML 交付接线，保留当前 v2 项目、研究服务、模板和工作台。`game_harness/asset_material_portal.py` 是新增的独立收集模块；现有 HTTP `material_portal.py` 保持兼容。旧 `asset_agent/` 原型、第三方源码缓存、重复协议与历史评测工具留在 `zx`，不属于默认分支的运行依赖。

默认配置保持不联网。需要在单文件 HTML 流程中使用真实 PNG 时，在自己的 `.env` 中设置 `MATERIAL_PORTAL_URL=web://assets`；`local://references` 提供内置的离线源码元数据。v2 项目与 Node 研究服务继续使用各自原有接口，本次不自动向 Vite 工程写入素材。完整边界、直接 Python 调用及验证方法见 [素材接入说明](docs/material-assets.md)。

Windows 本地运行需 Python 3.10+；研究侧车另需满足 `webmcp/package.json` 的 Node 版本。安装 `python -m pip install -e ".[test]"` 与 `python -m playwright install chromium` 后，复制 `.env.example` 为 `.env`，填写配置，再执行：

```powershell
python -X utf8 -m uvicorn game_harness.app:app --host 127.0.0.1 --port 18080 --workers 1
```

主服务、工程构建和编码工作区均保留独占文件锁。Windows 与 POSIX 使用各自内核锁，不能让两个进程同时占用同一目录。已审阅修复中新增源文件的原子发布仍依赖 POSIX 目录句柄；Windows 的基础服务与构建验证不代表所有项目管理路径都已跨平台，完整回归在 Linux 上执行。

## 在 210 启动

工作目录：`/home/zhaoyiwen/zhaoyiwen_nfs_data/zhangjianghackathon`。需要 Python 3.10+、Node.js 22 和 npm 10；210 已安装对应 Node/npm。首轮安装需要访问 Python/npm 包源，运行产物不依赖 CDN。

```bash
cd /home/zhaoyiwen/zhaoyiwen_nfs_data/zhangjianghackathon
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
# 首次部署时复制；已有 .env 请保留并按 .env.example 补充配置。
cp .env.example .env
# 编辑 .env 设置 OPENAI_API_KEY；不要提交密钥。
bash scripts/run.sh
```

若系统缺少 `ensurepip`，可在项目内引导虚拟环境：

```bash
python3 -m pip install --target .bootstrap virtualenv
PYTHONPATH=.bootstrap python3 -m virtualenv .venv
```

浏览器打开 `http://127.0.0.1:18080`。从本机访问 210：

```bash
ssh -N -L 18080:127.0.0.1:18080 GPU_210
```

默认绑定 loopback；受控内网可用 `HOST=0.0.0.0 bash scripts/run.sh`。共享部署可设置 `HARNESS_API_TOKEN`，在工作台“访问设置”输入同一口令。一个数据目录只运行一个服务进程，使用一个 uvicorn worker，任务并发由 `MAX_CONCURRENT_RUNS` 控制。`requirements.lock.txt` 是 Python 依赖快照；项目模板自带固定 npm 依赖版本与完整性锁文件。

## 启动与跟踪完整项目

工作台点击官方 case 按钮，或调用 API；启用口令时添加 `Authorization: Bearer <token>`。每次 POST 创建一个新任务，已有任务应按原 ID 跟踪或恢复。

```bash
curl -X POST http://127.0.0.1:18080/api/benchmarks/GAME-TEST-002/runs
```

自定义规格调用 `POST /api/projects`：

```json
{"specification":"在这里提交完整原始游戏规格，包含所有玩法、数值、关卡、操作方式和逐项验收标准。","engine":"auto"}
```

| API | 用途 |
| --- | --- |
| `GET /api/projects/{id}` | 任务、功能 DAG 状态计数与验收报告 |
| `GET /api/projects/{id}/candidate` | 当前源码的完整候选构建；尚无有效构建返回 409，属于等待状态 |
| `GET /api/projects/{id}/archive` | 源码、构建资源与证据 ZIP；候选也可下载，查看验收状态区分正式交付 |
| `GET /api/runs/{id}/events?after=0` | 增量轨迹 |
| `GET /api/runs/{id}/artifacts/{name}` | 原文、计划、工具观察及报告附件 |
| `POST /api/runs/{id}/cancel` / `resume` | 取消或恢复；沿用原任务的调用、token 和总时限 |
| `GET /api/benchmarks` | 官方规格资料与运行能力预检，不代表游戏验收结果 |

API 文档：`/docs`。原小游戏接口 `POST /api/runs` 保留，参数为 `description` 与 `engine: auto/canvas/dom`。

项目默认预算为 **600 次模型请求、6,000,000 个已报告 token、6 小时总时限**，按任务持久记录；各功能工具回合和整体验收修复也有上限。配置名为 `PROJECT_MAX_MODEL_CALLS`、`PROJECT_MAX_TOTAL_TOKENS`、`PROJECT_TIMEOUT_SECONDS`、`PROJECT_FEATURE_STEPS`、`PROJECT_REPAIR_ROUNDS`。单文件模式保留自己的较小预算。模型网络中断时未返回的 usage 无法等同实际计费。

单功能工具租约达到回合上限时，只有主机观察到源码 SHA 实质变化才能续接，最多 8 个租约；无进展故障最多 3 次尝试。续接不重置项目全局调用、token 或时限。

## 验证与交付

```bash
# 不调用模型：规格、状态/预算/恢复、工具边界、验收器和浏览器回归
RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest -q
# 显式安装锁定 npm 依赖，真实验证两个引擎、离线缓存与 opaque iframe
RUN_PROJECT_BUILDS=1 .venv/bin/python -m pytest -q tests/test_project_runtime.py
# 仅检查三份原始资料与运行能力，不生成游戏
.venv/bin/python scripts/check_benchmarks.py --require-runnable
# 消耗少量模型额度：文本、SSE 和基础图片理解探针
.venv/bin/python scripts/probe_model.py
```

已有工程可独立重建并逐项验收，默认不请求模型、不修改已有源码；构建会更新该工程的 `node_modules/`、`dist/` 和 `.harness/`。缺少模板文件或保护配置不一致会如实失败，报告目录必须独立且为空。未全部通过（包括人工或视觉待验）退出码为 1；只有显式添加 `--vision` 才会使用已配置的 QA 模型审查真实截图。

```bash
.venv/bin/python scripts/validate_project.py --case-id GAME-TEST-003 \
  --workspace data/runs/<run-id>/project --output data/independent-validation/<new-id>
.venv/bin/python scripts/export_project.py --run-dir data/runs/<run-id> \
  --output examples/<case-name>
```

导出不重建或修复源码：必须已有与当前源码 SHA 一致的成功构建。输出目录非空时拒绝覆盖；源码、`playable.html`、原始报告和逐文件 SHA 清单在临时目录校验完成后原子发布，不包含 `node_modules/`、`.env` 或 `.harness/`。`delivery-status.json` 独立重算证据与候选／正式状态，原始报告保留原字节和来源 hash；导出成功不表示游戏已通过全部验收。

项目 ZIP 的 `project/` 包含源码与 `dist/`，`evidence/` 包含规格、计划和报告。进入 `project/` 后可运行 `npm ci --ignore-scripts`、`npm run build`、`npm run preview`。已构建的 `dist/` 可通过本地静态 HTTP 服务离线运行；多文件 ES 模块不要求直接双击 HTML。

已有有效构建的交付包另附可离线双击的 `playable.html` 和绑定产物 SHA 的 `delivery-manifest.json`；可运行候选仍须依据逐项报告判断是否完成正式验收。

工作台使用 `sandbox="allow-scripts"` 的 opaque iframe，不开启 `allow-same-origin`。仅当前 run 的游戏存档通过受限桥持久化，父页口令、其他任务的存档和其他本地存储不会注入游戏。

生成源码的构建通过宿主机 Node/npm/TypeScript/Vite 子进程执行；允许命令和工具链由主机固定，环境不转发模型密钥等凭据。**当前没有 OS 级容器或虚拟机隔离**，子进程仍拥有部署用户的系统权限。文件边界、禁用安装脚本和无凭据环境不能替代系统沙箱。权限和证据门禁详见 [运行架构](docs/harness.md)。

## 当前证据与团队接入

三个官方 case 的引擎锁与完整规格已接入，现有真实任务如下。已保存的源码、离线 HTML 和原始证据包见 [游戏交付目录](examples/README.md)；状态以绑定同一源码 SHA 的逐项验收为准。

| case | 引擎 | 本轮真实任务 ID | 当前正式验收 |
| --- | --- | --- | --- |
| 数字门枪手 `GAME-TEST-001` | Babylon.js | `5534e13dfdc74d1aa810f2beb719e5ed` | 54 / 54 通过，已交付 |
| 武林幸存者 `GAME-TEST-002` | Phaser | `c0e2aefc313246fc879c5a89f505bdfb` | 完整 88 项验收推进中 |
| 百炼英雄／战力吞噬者 `GAME-TEST-003` | Babylon.js | `11fc08ffeb364846aadb87751eb842cf` | 82 / 83 通过；T69 待真实手机验证，交付为候选 |

v1 打地鼠真实生成任务 `b475570322d5409dadfc5549ceae0053` 已通过浏览器和截图 QA，另用真实键盘操作验证胜、负、暂停及重开。它是独立的小型游戏证据，不替代上述大型 case。

- [Harness 架构、恢复和验收门禁](docs/harness.md)
- [团队组件、三 case 与素材门户接入状态](docs/team-integration.md)
- [长程任务框架调研与设计取舍](docs/long-horizon-research.md)

模型角色可单独配置；项目每个功能按标题、游戏名和引擎检索已有 RAG，保存文档 ID、来源与警告，编码工具可按需读取检索结果。现有 RAG 支持本地 Markdown BM25、`RAG_API_URL` 与可选 `MATERIAL_PORTAL_URL` HTTP bridge；检索不能覆盖原文或宿主指令。外部 RAG/门户真实地址仍未提供，完整讨论状态机仍需与团队对接，不把 mock 样例当成已联网证据。原始 `system_prompt.txt`、架构图及 `webmcp/` 提案完整保留；原始提示词作为设计资料，未直接作为宿主指令执行。
