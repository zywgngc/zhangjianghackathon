# 游戏工程交付

状态：候选，尚未完成全部正式验收。具体依据见 delivery-status.json；完整原始报告与逐文件校验清单位于 delivery.zip 内的 evidence/ 与 delivery-manifest.json。

直接打开 playable.html 可离线运行。源码位于 project/，已构建的多文件版本位于 delivery.zip 内的 project/dist/；不包含 node_modules、凭据或 .harness。
已安装生产依赖的许可证与声明文件位于 licenses/npm/，精确版本见 project/package-lock.json。
使用与仓库一致的 Python/Node 依赖后，可重新运行 scripts/validate_project.py 对源码独立验收。

GAME-TEST-003：82项通过，0项失败；T69「真实手机移动流畅度」尚待实际设备验证，当前交付属于候选。
