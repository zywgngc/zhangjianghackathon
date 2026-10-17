# 可玩游戏与验收证据

| 游戏 | 正式验收 | 交付 |
| --- | --- | --- |
| GAME-TEST-001 数字门枪手 | 54 / 54 通过 | [离线 HTML](GAME-TEST-001/playable.html)、[源码](GAME-TEST-001/project/)、[完整证据包](GAME-TEST-001/delivery.zip) |
| GAME-TEST-003 百炼英雄 | 82 / 83 通过；T69 待真实手机验证，当前为候选 | [离线 HTML](GAME-TEST-003/playable.html)、[源码](GAME-TEST-003/project/)、[完整证据包](GAME-TEST-003/delivery.zip) |

下载后直接打开 `playable.html`。源码按各游戏的 `package-lock.json` 安装依赖后可重新构建。

`delivery-status.json` 记录依据原始证据重新计算的验收状态；`artifact-manifest.json` 记录本目录文件的 SHA256。
完整证据包保留原始规格、验收报告、截图、构建记录、模块接入检查、依赖声明和独立的逐文件清单。
每个验收结果只适用于报告绑定的源码 SHA256；后续修改需要重新验收。
