// Presentation only: API enums, source evidence, and exported blueprints stay unchanged.
const labels = {
  'GAME-TEST-001': '数字门枪手',
  'GAME-TEST-002': '武林幸存者',
  'GAME-TEST-003': '战力吞噬者',
  queued: '排队中',
  running: '研究中',
  waiting_input: '等待补充信息',
  partial: '部分完成（待验证）',
  completed: '已完成',
  failed: '研究失败',
  cancelled: '已取消',
  archetypes: '研究相似游戏原型',
  planning: '拆分玩法机制',
  mechanics: '检索机制候选',
  review: '整理证据与蓝图',
  finished: '本轮结束',
  engine: '游戏引擎',
  gameplay: '玩法规格',
  implementationStyle: '实现偏好',
  candidateCount: '每次检索的候选目标数',
  engine_native: '优先贴合引擎',
  portable: '优先可移植算法',
  html: '原生网页',
  phaser: 'Phaser',
  babylon: 'Babylon.js',
  canvas: 'Canvas',
  dom: 'DOM',
  specification: '规格要求',
  system_default: '系统默认',
  saved_preference: '创建时的偏好',
  developer_choice: '开发者选择',
  research_context: '已有研究信息',
  'squad-movement': '队伍移动与阵型',
  'arithmetic-gates': '一次性算术门',
  'automatic-shooting': '索敌、子弹与伤害',
  'hazard-collision': '障碍与敌人碰撞',
  'boss-progression': '首领与关卡状态机',
  'persistent-economy': '奖励与永久升级',
  'movement-spawning': '移动、追踪与刷怪',
  'skill-runtime': '自动技能执行',
  'status-effects': '状态效果与抗性',
  'experience-upgrades': '经验与升级选项',
  'skill-evolution': '技能进化依赖',
  'combat-bosses': '伤害、掉落与首领战',
  'persistent-progression': '经济与永久成长',
  'click-navigation': '目标选择与寻路',
  'power-comparison': '严格确定的战力规则',
  'interaction-lifecycle': '一次性战斗与拾取',
  'level-solvability': '确定性关卡推进',
  'boss-unlocks': '首领胜利与关卡解锁',
  'persistent-state': '进度与设置存档',
};
export const label = (value) => (Object.hasOwn(labels, value) ? labels[value] : String(value));

// Match exact source strings so changed provider content is never covered by an outdated translation.
const sourceTranslations = new Map([
  ['Squad movement and formation', '队伍移动与阵型'],
  [
    'Advance at 8 world units/second; horizontal speed 6; clamp squad center X to [-5.5,5.5].',
    '前进速度为每秒 8 个世界单位，横移速度为 6；队伍中心 X 限制在 [-5.5, 5.5]。',
  ],
  [
    'Drag release stops horizontal input. Keep movement frame-rate independent.',
    '松开拖动后停止横向输入；移动距离不受帧率影响。',
  ],
  ['Single-use arithmetic gates', '一次性算术门'],
  [
    'Apply addition, subtraction, multiplication, or floor division to the whole squad.',
    '对整个队伍人数执行加、减、乘或向下取整的除法。',
  ],
  [
    'Clamp soldier count to [0,200]; reject division by zero. One gate per group, activated once by squad center.',
    '士兵数量限制在 [0, 200]，禁止除以零；每组只能选择一道门，由队伍中心触发一次。',
  ],
  ['Targeting, projectiles, and damage', '索敌、子弹与伤害'],
  [
    'Each living soldier attacks twice/second; base bullet damage 1; bullet speed 30 and lifetime at most 3 seconds.',
    '每名存活士兵每秒攻击 2 次；子弹基础伤害为 1，速度为 30，存活时间不超过 3 秒。',
  ],
  [
    'Damage requires actual hits. Retarget valid enemies ahead; pool projectiles without reducing simulated firepower.',
    '只有实际命中才造成伤害；重新锁定前方有效敌人，使用子弹对象池但不降低模拟火力。',
  ],
  ['Hazard and enemy collisions', '障碍与敌人碰撞'],
  [
    'Enemy contact removes one soldier and the enemy once.',
    '接触敌人时移除 1 名士兵及该敌人，只结算一次。',
  ],
  [
    'Barricade, wire, and mine losses are 2,3,5 respectively; each obstacle triggers once.',
    '路障、铁丝网和地雷分别损失 2、3、5 名士兵；每个障碍只触发一次。',
  ],
  ['Boss and level state machine', '首领与关卡状态机'],
  [
    'Boss HP zero wins; reaching the end with living boss loses; zero soldiers loses.',
    '首领生命值归零即胜利；到达终点时首领仍存活，或士兵数量归零，均判定失败。',
  ],
  [
    'Three levels, unlock next on victory, retry resets temporary state.',
    '共 3 个关卡；胜利后解锁下一关，重试时重置临时状态。',
  ],
  ['Rewards and permanent upgrades', '奖励与永久升级'],
  [
    'Keep gold and upgrade configuration separate from run state.',
    '金币与升级配置独立于单局运行状态保存。',
  ],
  [
    'Apply specified costs/rewards exactly; persist permanent upgrades and preserve them across retry.',
    '严格使用规格中的花费和奖励；永久升级持久保存，重试时保留。',
  ],
  ['Movement, pursuit, and spawning', '移动、追踪与刷怪'],
  [
    'Virtual joystick and keyboard free movement; enemies approach from all directions.',
    '支持虚拟摇杆和键盘自由移动；敌人从四周接近。',
  ],
  [
    'Use configured spawn schedules and pool up to 200 normal enemies.',
    '按配置的时间表刷怪，使用对象池管理最多 200 个普通敌人。',
  ],
  ['Automatic skill execution', '自动技能执行'],
  [
    'Implement 12 active skills with five levels, independent cooldowns and effect handlers; no visual-only attacks.',
    '实现 12 种主动技能，每种 5 级，分别管理冷却和效果；攻击必须有实际作用。',
  ],
  [
    'Use additive level multipliers 1.2/1.4, cap cooldown reduction at 40%; six active and six passive slots maximum.',
    '等级倍率按加法累计，取 1.2 / 1.4；冷却缩减上限为 40%，主动和被动技能各最多 6 个槽位。',
  ],
  ['Status effects and resistance', '状态效果与抗性'],
  [
    'Burn and poison refresh duration rather than stacking indefinitely.',
    '灼烧与中毒刷新持续时间，不可无限叠加。',
  ],
  [
    'Bosses resist freeze as a 30% slow and are immune to stun/knockback.',
    '首领受到冰冻时改为减速 30%，并免疫眩晕和击退。',
  ],
  ['Experience and upgrade sampling', '经验与升级选项'],
  [
    'XP_next(L)=ceil(4+1.5*L+0.12*L^2). Carry excess XP and process all level-ups sequentially.',
    '升级经验：XP_next(L)=ceil(4+1.5*L+0.12*L^2)。保留溢出经验，连续升级按顺序逐次处理。',
  ],
  [
    'Offer up to three unique valid choices; exclude maxed or unavailable skills and use repeatable rewards when exhausted.',
    '每次提供最多 3 个互不重复的有效选项；排除满级或不可用技能，选项耗尽时提供可重复奖励。',
  ],
  [
    'Free selections at 5 and 15 seconds; guarantee a new skill option at 15 seconds if only one skill is owned.',
    '第 5 秒和第 15 秒提供免费选择；第 15 秒若仅拥有 1 个技能，须保证出现新技能选项。',
  ],
  ['Evolution dependency graph', '技能进化依赖'],
  [
    'Six evolution recipes; enforce prerequisites and preserve passive levels.',
    '实现 6 种进化配方，严格检查前置条件，并保留被动技能等级。',
  ],
  [
    'Ice-fire evolution consumes two base skills and occupies one slot. Valid progression must permit an ultimate within 180 seconds.',
    '冰火进化消耗 2 个基础技能，占用 1 个槽位；有效成长路线须允许在 180 秒内获得终极技能。',
  ],
  ['Damage, drops, and boss encounters', '伤害、掉落与首领战'],
  [
    'Player contact invulnerability is 0.6 seconds; healing cannot exceed maximum HP.',
    '玩家接触受伤后的无敌时间为 0.6 秒；治疗不可超过生命值上限。',
  ],
  [
    'Victory requires boss defeat, not timer survival; boss attacks have warnings and scheduled spawns.',
    '必须击败首领才能胜利，仅存活到计时结束不算胜利；首领按时间表出现，攻击须有预警。',
  ],
  ['Economy and permanent progression', '经济与永久成长'],
  [
    'Apply configured gold drops/rewards and all six permanent upgrades.',
    '按配置执行金币掉落与奖励，实现全部 6 项永久升级。',
  ],
  [
    'Persist permanent upgrades; reset temporary skills and passives on new runs.',
    '保存永久升级；开始新一局时重置临时主动与被动技能。',
  ],
  ['Selection and navigation', '目标选择与寻路'],
  [
    'Tap enemy, weapon, or ground to navigate through the open field; do not use tower floors or joystick control.',
    '点击敌人、武器或地面，在开放场地中寻路；不采用塔层结构或摇杆控制。',
  ],
  [
    'Avoid walls, handle retargeting and unreachable targets, and stop within interaction range without teleporting.',
    '绕开墙体，处理切换目标和无法到达的目标；在交互范围内停下，不可瞬移。',
  ],
  ['Strict deterministic power rules', '严格确定的战力规则'],
  [
    'Only P>E permits combat; equality or a stronger enemy must not trigger movement, combat, power loss, or death.',
    '仅当玩家战力 P > 敌人战力 E 时允许战斗；相等或敌人更强时，不触发移动、战斗、战力损失或死亡。',
  ],
  [
    'Use one authoritative positive integer power value; no HP, critical hits, or random combat outcomes.',
    '仅使用一个权威的正整数战力值；不引入生命值、暴击或随机战斗结果。',
  ],
  ['One-time combat and collection', '一次性战斗与拾取'],
  [
    'Resolve attacks only in range after facing/animation; add enemy power once and deactivate defeated targets.',
    '进入范围、完成朝向与动画后才结算攻击；只增加一次敌人战力，并使已击败目标失效。',
  ],
  [
    'Weapons add configured bonuses once and become unselectable; keep UI and simulation power consistent.',
    '武器只增加一次配置的战力，之后不可再选；界面与模拟状态中的战力保持一致。',
  ],
  ['Deterministic level progression', '确定性关卡推进'],
  [
    'Three levels start at power 10,20,30, with boss powers 150,500,1500.',
    '3 个关卡的初始战力分别为 10、20、30；首领战力分别为 150、500、1500。',
  ],
  [
    'Retain all specified enemy/weapon values, require reachable objects and at least one strict-inequality winning route.',
    '保留规格中的全部敌人和武器数值；对象必须可达，且至少存在一条始终满足严格战力比较的胜利路线。',
  ],
  ['Boss victory and level unlocking', '首领胜利与关卡解锁'],
  [
    'Boss defeat triggers victory and next-level unlock; invalid boss selection triggers neither victory nor defeat.',
    '击败首领后判定胜利并解锁下一关；无效的首领选择既不触发胜利，也不触发失败。',
  ],
  [
    'Completed levels remain replayable; restart resets temporary powers but preserves unlocks.',
    '已完成关卡可以重玩；重新开始时重置临时战力，保留解锁进度。',
  ],
  ['Saved progression and settings', '进度与设置存档'],
  [
    'Persist unlocked/completed levels and audio settings.',
    '持久保存已解锁和已完成的关卡，以及音频设置。',
  ],
  [
    'Keep temporary target/path/combat state separate from permanent progression.',
    '临时目标、路径与战斗状态独立于永久进度管理。',
  ],
]);
export const sourceText = (value) => sourceTranslations.get(value) ?? value;

const errorMessages = {
  INVALID_INPUT: '提交内容不符合要求，请检查输入。',
  LOCK_CONFLICT: '当前选择与规格中的锁定要求冲突，请保留规定的引擎和玩法。',
  FOLLOW_UP_LIMIT_REACHED: '本任务的追问额度已用完，已有结果仍可查看和导出。',
  STALE_REVISION: '研究版本已更新，请先刷新，再提交。',
  STALE_BLUEPRINT: '蓝图版本已更新，请刷新后重新检查交接。',
  PARENT_CONFLICT: '追问对应的上一条请求已变化，请刷新后重试。',
  ROUND_CONFLICT: '追问轮次已变化，请刷新后重试。',
  CLARIFICATION_CONFLICT: '这批问题已更新，请刷新后回答最新问题。',
  IDEMPOTENCY_CONFLICT: '请求编号与已有内容冲突，请刷新后重试。',
  NOT_READY: '当前版本的蓝图尚未就绪，请稍后刷新。',
  NOT_FOUND: '未找到该任务或资源，请刷新任务列表。',
  RUN_CLOSED: '此任务已关闭，无法继续研究。已有记录仍保留。',
  DEADLINE_EXCEEDED: '本任务已达到运行时限，无法继续追加研究。',
  UNAUTHORIZED: '尚未通过访问验证，请重新打开本地页面或检查工作台访问口令。',
  FORBIDDEN: '当前访问被拒绝，请通过本地工作台地址打开页面。',
  RESEARCH_NOT_CONFIGURED: '尚未配置研究服务，请检查联合启动设置。',
  RESEARCH_AUTH_FAILED: '研究服务的后端口令不匹配，请检查服务配置。',
  RESEARCH_TIMEOUT: '研究服务响应超时，请先刷新查看请求是否已被接收。',
  RESEARCH_UNAVAILABLE: '暂时无法连接研究服务，请检查服务是否运行。',
  INVALID_RESEARCH_RESPONSE: '研究服务返回的数据格式异常，请查看错误原文。',
  RESEARCH_NOT_BUILDABLE: '当前研究结果尚不满足生成交接条件。',
};
export function errorMessage(error) {
  if (Object.hasOwn(errorMessages, error.code)) return errorMessages[error.code];
  if (error.status === 401 || error.status === 403) return errorMessages.UNAUTHORIZED;
  if (/[\u4e00-\u9fff]/.test(error.message)) return error.message;
  return '操作未完成，请检查服务连接或刷新后重试。详细原因可查看下方错误原文。';
}

export function handoffReasons(blockers) {
  const reasons = [];
  const unverified = [];
  for (const blocker of blockers) {
    const [code, detail] = blocker.split(':');
    if (code === 'unverified_mechanic') {
      unverified.push(label(detail));
    } else if (code === 'research_not_ready') {
      reasons.push('当前蓝图仍为部分完成，尚未达到可交接状态。');
    } else if (
      code === 'quick_run_handoff_engine_not_supported' ||
      code === 'engine_adapter_not_installed'
    ) {
      reasons.push(`此交接入口只支持单文件小游戏；${label(detail)} 蓝图需要接入完整项目流程。`);
    } else if (code === 'unsupported_builder_engine') {
      reasons.push(`当前生成入口不支持所选引擎：${label(detail)}。`);
    } else if (code === 'unresolved_research_gaps') {
      reasons.push('研究蓝图中仍有尚未解决的证据缺口。');
    } else if (code === 'synthetic_or_unknown_source_evidence') {
      reasons.push('候选资料包含模拟数据，或来源真实性尚未确认。');
    } else {
      reasons.push(`其他交接条件未满足（来源原文）：${blocker}`);
    }
  }
  if (unverified.length) reasons.push(`以下机制尚未通过行为验证：${unverified.join('、')}。`);
  return reasons;
}
