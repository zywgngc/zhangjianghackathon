# 精简合入的素材组件

以默认分支 `2115544188aef34789aa242c061f84c9cf307a70` 为基础，选择性接入 `zx@2fb926c3645c5d81376070b58bb54a4b857c5d84` 的现成能力。保留默认分支的 Phaser/Babylon 工程生成、研究工作台、完整规格与发布检查，不用旧 Harness 覆盖它们。

## 本次合入范围

- `game_harness/asset_material_portal.py`：独立在线收集、离线来源元数据、正式 v0.2 请求、缓存、PNG/ZIP 核验及 HTML 内嵌工具。
- `rag.py`、`agents.py`、`runner.py`：单文件 HTML 流程显式选择素材后端；设计与生成只接收净化元数据，构建后解析已核验的 `asset://` 引用。现有 HTTP 门户与研究蓝图关联继续保留。
- 素材专项测试、两个实际运行工具、明确缩减的 HTML 请求示例，以及安装/使用说明。
- 必要的 Windows 内核锁和工程子进程兼容修复，保持 POSIX 行为及单实例边界。

旧 `asset_agent/` 原型、`catalogs/` 素材与第三方源码缓存、旧 `prompts/` 和 `schemas/`、重复协议目录、历史补丁、旧评测与玩法复验脚本不导入。完整原始测试资料使用默认分支现有的连字符目录，不复制下划线目录。它们仍保留在 `zx`。

## 配置和 Python 调用

默认 `MATERIAL_PORTAL_URL` 为空，继续关闭素材网络访问；现有 HTTP(S) 地址仍调用原来的 v0.2 桥。设置 `web://assets` 会在 HTML 流程真实搜索 Kenney/OpenGameArt、读取来源许可并下载有界 PNG/ZIP；设置 `local://references` 使用文件中内置的来源元数据，不需要外部图库。

```python
from game_harness.config import Settings
from game_harness.asset_material_portal import MaterialPortalClient

settings = Settings(portal_url="web://assets")
portal = MaterialPortalClient(settings)
evidence = await portal.search("2D forest monster sword sprites", limit=4, run_id="my-run")
# 正式协议：await portal.search_request(v02_request)
# 独立文件交付：await portal.collect_assets(v02_request)
```

正式请求沿用 `webmcp/material-portal.schema.json`；协议不重复维护。在线检索使用匿名受限 GET，不将模型 Key 或门户 Token 发给素材站。失败、素材缺项、许可不明或不支持的格式会明确返回，不伪造候选。图片未经视觉模型排序，不保证题材、朝向、动画和画风一致。

`Retriever.search_html()` 供 HTML 流程调用。普通 `search()` 保持项目检索语义，不把仅供 HTML 的内置素材后端自动接入 v2，也不向工程编码器发送下载缓存路径、图片 Base64 或无法解析的 `asset://`。HTTP 门户保持原有接口。

v2 工程的 `public/assets/` 写入、工程资源配额与源码 SHA 更新，以及 Node 研究服务的正式素材传输仍需独立适配。本次不把 HTML 的 300KB 内嵌限制套在 Vite 工程上，也不宣称完整 Phaser/Babylon 游戏已通过素材验收。

## 本地验证与示例

安装依赖按根 README 的 `python -m pip install -e ".[test]"`。先安装 Chromium，再检查运行环境：

```text
python -X utf8 tools/run_game_harness.py --env-file /path/to/private.env --request examples/game-harness-assets/number-gate.request.json --check-only
python -X utf8 tools/run_game_harness.py --env-file /path/to/private.env --request examples/game-harness-assets/number-gate.request.json --build-output-tokens 32000
```

第二条命令会调用真实模型。原始长规格不经过该缩减入口，也不会因此改变正式 case 的验收状态。`--env-file` 只指向本机忽略的配置，密钥不写入请求或命令参数。`data/`、`output/` 和缓存均不提交。

不调用模型、显式检验真实素材网站：

```text
python -X utf8 tools/evaluate_online_assets.py --case number-gate
```

代码回归使用模拟模型与固定网络响应；真实网站可用性、素材语义以及生成后的玩法是另外的验证范围。此前 `zx` 的真实 HTML 试跑已证明图片可下载并在浏览器绘制，但仍观察到手机留白、题材匹配不足、内嵌未使用图片和自动 QA 误报。这些结果不等同当前完整工程的验收。
