# Simulation 固定步进、独立实例与状态归属

advance(milliseconds) 消费请求的全部时间，分固定步长执行生产系统并保留余量；不要套用浏览器长帧限制，把三秒截为十帧。simTime 用秒。updateLoops 表示有效模拟驱动数，累计帧数另存 ticks。测试首次 advance 停止自动 RAF 更新，避免双更新，生产渲染继续读取同一状态。

独立 fixture 先初始化合法关卡和 PLAYING，再覆盖声明的初始实体、属性、时间与几何。为 partial 实体补齐真实初始化必需字段；boss:null 和 spawnEnabled:false 要生效。不能先覆盖 fixture 又用 startLevel 清掉它，不能注入胜利、奖励或测试答案。数值菜单的允许范围见主机完整协议。

持续伤害状态、弹池、实体缓存、计时器和随机数属于每个 GameState/Simulation；模块全局 Map、数组或计时器会让相同 ID 的独立实例串状态。重开和 dispose 清理各自资源。render:true/ui:true 复用生产渲染器及同一 React 游戏视图，返回真实容器和资源句柄，不能用离屏占位页面代替；独立存档与音频不能污染活动游戏。
