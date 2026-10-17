# Babylon Phaser 场景、输入与资源生命周期

createScene(container, simulation) 渲染真实生产状态，engineInfo 返回实际引擎名称、版本、running 及资源数量；projectEntity 使用当前真实 camera 投影计算可点击的 viewport 坐标，不能返回猜测的固定位置。输入控制使用 UI 同一 dispatch 入口，相机控制不要抢走游戏拖拽或触屏操作。

数量变化必须有对应实体视觉：兵阵不能只放大一个角色；数学门需要实际运算符、数值和颜色；战力标签对应真实角色与战力。具体构图和美术方向遵从原文及参考图，不把这一知识片段当作游戏规格。

高频射击和粒子使用有界对象池或可复用场景资源，不为每发弹药新建大粒子系统、纹理、监听器和 timeout。resize 不重置模拟。dispose 释放 scene、engine、React 根、音频、计时器、监听和循环。压力测试需要真实引擎绘制与独立资源观察，不能根据逻辑实体数量声称帧率或真机性能通过。
