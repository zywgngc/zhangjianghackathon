# 离线游戏运行与生命周期
HTML5 游戏默认使用原生 Canvas 2D 或 DOM。所有 CSS、JavaScript 内联，一个文件无需构建工具离线打开。Canvas 避免远程图片，用几何图形、渐变、程序粒子、系统字体绘制。DOM 更适合消除、棋盘、卡牌、文字和回合制界面。Canvas 适合接物、射击、平台跳跃、躲避和动作游戏。外部引擎必须预先由受信任模板提供本地依赖，不能临时从 CDN 加载。

状态机 ready → running ↔ paused → won/lost。restart 从任意状态清空敌人、玩家状态、计时器、粒子和临时输入，将分数与 ticks 归零并进入 running。只注册一次事件监听，只启动一个 requestAnimationFrame 主循环，更新依赖 state，不重复创建循环。暂停时停止物理和生成计时。document.visibilitychange 可暂停。游戏暂停和继续按钮必须符合当前状态。

积分持久化必须 try/catch localStorage，因为隔离预览可能没有存储权限。游戏逻辑不能依赖存储成功。游戏结果明确展示胜利/失败原因和重新开始按钮。
