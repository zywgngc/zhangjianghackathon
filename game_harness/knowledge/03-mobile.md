# 手机横竖屏布局与中文体验
HTML meta viewport width=device-width,initial-scale=1,viewport-fit=cover。body margin 0；外层 min-height:100dvh，padding 用 env(safe-area-inset-*)。容器宽度 min(100%,960px)，box-sizing:border-box。游戏可视区域按可用高度变化，用 max-height 避免横屏操作区被推到屏幕外。小屏双列改为上下排列，横屏缩小说明区，不隐藏必要操作。

触屏按钮至少 44px，间距明确，触控区 touch-action:none；菜单正常滚动。按键反馈用颜色、位移和轻微粒子，不能只靠声音。系统中文字体：system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif。HUD 中文显示得分、生命、目标和暂停，初始屏明确玩法，胜负界面明确重开。弱化装饰，保持游戏区对比和清晰反馈。
