# 游造 · 中文 HTML 游戏生成 Harness

用户输入一句游戏描述，系统完成 **需求改写 → RAG 检索 → 设计与引擎路由 → 代码生成 → Chromium 实测 → 截图与代码 QA → 限次修复 → 已验证预览**。

首版所有角色默认使用 `qwen3.8-max`，通过 OpenAI 兼容 Chat Completions 接口调用。当前可运行引擎是原生 Canvas 和 DOM，输出单文件离线中文游戏。Phaser、Babylon.js 和 React 的依赖模板尚未实现，不会被路由选中。

## 在 210 启动

工作目录：`/home/zhaoyiwen/zhaoyiwen_nfs_data/zhangjianghackathon`。

```bash
cd /home/zhaoyiwen/zhaoyiwen_nfs_data/zhangjianghackathon
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
cp .env.example .env
# 编辑 .env，设置 OPENAI_API_KEY。不要提交 .env。
bash scripts/run.sh
```

若系统缺少 `ensurepip`，可在项目内引导虚拟环境：

```bash
python3 -m pip install --target .bootstrap virtualenv
PYTHONPATH=.bootstrap python3 -m virtualenv .venv
```

浏览器打开 `http://127.0.0.1:18080`。远程访问可建立 SSH 隧道：

```bash
ssh -N -L 18080:127.0.0.1:18080 GPU_210
```

内网直接访问可用 `HOST=0.0.0.0 bash scripts/run.sh`，共享环境可设置 `HARNESS_API_TOKEN`，在页面“访问设置”输入同一口令。一个数据目录只能有一个服务进程，**不要使用多个 uvicorn workers 或 reload 模式**。任务内部并发由 `MAX_CONCURRENT_RUNS` 控制。

`requirements.lock.txt` 记录 210 的 Python 3.10 实测依赖快照；复现时先 `pip install -r requirements.lock.txt`，再安装本项目。Chromium 版本由对应 Playwright 安装命令确定。

## 验证

```bash
# 不消耗模型额度：队列、恢复、预算、修复门禁、哈希、接口与真实浏览器测试
RUN_BROWSER_TESTS=1 .venv/bin/python -m pytest -q
# 消耗少量模型额度：真实文本、图片理解与中文文字识别
.venv/bin/python scripts/probe_model.py
```

## 组件接入

- **模型角色**：`MODEL_REWRITE`、`MODEL_DESIGN`、`MODEL_BUILD`、`MODEL_QA`、`MODEL_REPAIR` 可分别覆盖模型。`AgentClient` 是统一适配边界。
- **已有 RAG**：配置 `RAG_API_URL`（完整检索 URL）；POST `{"query":"…","top_k":4}`，返回 `{"documents":[{"id":"…","title":"…","content":"…","source":"…"}]}`。可配置 `RAG_API_TOKEN`。检索失败记录警告并回退本地知识库。
- **本地知识库**：`RAG_DIR` 指向额外 Markdown 目录，启动时建立中英文分词 BM25 索引，证据随任务保存。默认内置生命周期、输入碰撞、移动布局与棋盘规则。
- **外部 agent / 引擎 / 测试器**：接口、任务契约与扩展点见 [harness 架构与边界](docs/harness.md)。

## API

交互文档：`/docs`。启用口令时，请在 API 请求头带 `Authorization: Bearer <token>`。

```bash
curl http://127.0.0.1:18080/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"description":"做一个太空接星星游戏，100分胜利，支持手机操作","engine":"auto"}'
```

`GET /api/runs/{id}` 查看状态，`GET /api/runs/{id}/events?after=0` 增量读取轨迹。`POST …/cancel` 取消，`POST …/resume` 从检查点恢复失败任务（保留原预算）。只有通过浏览器与 QA 的版本可以通过 `GET …/preview/{version}` 预览。源码与报告可从 `GET …/artifacts/{name}` 下载。

## 已有设计资料

保留原始 [系统提示词](system_prompt.txt)、[架构 Markdown](web-game-generator-mas-plan.md)、[架构 HTML](web-game-generator-mas-plan.html) 及 `diagrams/`。原始提示词面向“原型复刻＋人工确认”；本 harness 按当前 hackathon 的一句话自动生成需求运行，自动记录补充假设。原提示词文件没有被直接注入运行时。

已验证的是 Chromium 的加载、状态机、操作冒烟、暂停、继续、重开、三种屏幕尺寸，以及截图/代码模型审查；不代表完整玩法、iOS Safari、微信或性能验收全部通过。真实测试证据保存在 `data/runs/<id>/`。

## 团队新增组件与验收规格

[WebMCP 架构及素材门户契约](webmcp/README.md) 定义了团队外部素材门户的接入边界；当前文档是提案，示例为 mock，不能当成真实检索证据。

[WebMCP 当前交接说明](webmcp/handoff.md) 列出了交付物、组件归属、输入输出及后续工作；其中的研究 MAS runtime 和真实门户接入仍待实现。

`tests` 分支提供数字门枪手、武林幸存者、百炼英雄的长规格和参考图片。它们包含严格的引擎与内容要求，与 Python 回归测试分别管理；能力预检不会将不支持的引擎自动改为 Canvas。

运行 `.venv/bin/python scripts/check_benchmarks.py` 查看三份规格的能力预检；也可访问 `/api/benchmarks`。素材门户 bridge、mock 证据规则和当前能力缺口见 [团队组件接入状态](docs/team-integration.md)。
