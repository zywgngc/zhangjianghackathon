# React TypeScript Phaser Babylon 工程集成与生产 Simulation

完整规格项目使用可信 Vite 模板和固定引擎依赖。规划中的导航 Navigation、门 Gate、战斗 Combat、Boss、经济 Economy、技能 Skill、存档 Persistence、UI、Audio、Scene 模块必须接入真实入口。Simulation 组合这些已实现模块，持有唯一 GameState、随机数和事件流；不要在集成时重新写一份缩减玩法，把专项模块留成无人使用的文件。

先读取当前源码的真实导出签名，再调用；规划中的接口可能已过时。React App 必须使用真实场景与现有页面组件，bridge 绑定同一 Simulation 和 SceneHandle。源码可达性审计会指出未接线的运行模块，但静态 import 不证明函数被调用，完整玩法仍需独立运行检查。

胜利是一次真实的终态转换：由 Boss 实际死亡触发，停止继续更新，奖励、完成关卡、解锁和保存只执行一次。禁止到时间空跑直接胜利、重复发金币、重开清除永久进度。固定数值始终以本项目完整原文为准。
