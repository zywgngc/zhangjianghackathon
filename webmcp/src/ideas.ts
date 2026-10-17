import type { ClarificationQuestion } from '../discussion.ts';
import type { Engine, MaterialCandidate } from '../material-portal.ts';
import type {
  AcceptanceCheck,
  Decision,
  IdeaProposal,
  Mechanic,
  Plan,
  Run,
  StartRequest,
} from './model.ts';

export const isIdea = (input: StartRequest): boolean => !input.caseId;
export const chosen = (run: Run, id: string): string =>
  run.decisions.find((d) => d.id === id)?.value ?? '';
export const selectedEngine = (run: Run): Engine => chosen(run, 'engine') as Engine;

const archetypes = [
  {
    id: 'runner',
    label: '跑酷闯关',
    pattern: /跑酷|跑道|数字门|runner|running|gate/i,
    rationale: '自动前进，沿途选择路线或收益，抵达终点。',
  },
  {
    id: 'survivor',
    label: '生存战斗',
    pattern: /生存|幸存|围攻|割草|射击|surviv|shoot|horde/i,
    rationale: '移动躲避、应对敌群，通过战斗和升级形成循环。',
  },
  {
    id: 'puzzle',
    label: '规则解谜',
    pattern: /解谜|消除|合并|谜题|puzzle|merge|match/i,
    rationale: '离散操作改变棋盘状态，达成目标后结算。',
  },
  {
    id: 'platformer',
    label: '平台跳跃',
    pattern: /跳跃|平台|platform|jump/i,
    rationale: '移动、起跳、落地和障碍判定组成关卡挑战。',
  },
];
const optional = (
  id: string,
  prompt: string,
  value: string,
  options: [string, string][],
): ClarificationQuestion => ({
  questionId: id,
  prompt,
  required: false,
  defaultOptionId: value,
  options: options.map(([optionId, label]) => ({ optionId, label })),
});

/** Explicit fallback: keyword suggestions over text, never inferred image contents or verified evidence. */
export function propose(input: StartRequest, candidates: MaterialCandidate[] = []): IdeaProposal {
  const known = [
    input.brief ?? '',
    ...(input.knownFeatures ?? []),
    ...(input.images ?? []).map((i) => i.description),
  ].filter((s) => s.trim());
  const text = known.join('\n');
  const ranked = archetypes
    .map((a) => ({
      ...a,
      score: a.pattern.test(text) ? 10 : 0,
      candidateIds: candidates
        .filter((c) => a.pattern.test([c.title, c.summary, ...c.coverageHints].join(' ')))
        .map((c) => c.candidateId),
    }))
    .sort((a, b) => b.score + b.candidateIds.length - (a.score + a.candidateIds.length));
  return {
    basis: candidates.length ? 'candidate_hints' : 'rule_fallback',
    known,
    imageInterpretation: 'not_performed',
    warnings: [
      '这是可修改的提案；文字原意保留在已知信息中，规则规划器无法保证理解任意描述或识别否定、冲突与复合玩法。请检查补全方案是否符合原意。',
      ...(input.images?.length
        ? ['参考图片已转交素材门户，本组件未识别图片内容；图片描述是用户输入，不是视觉识别结果。']
        : []),
      candidates.length
        ? '候选提示只用于推荐排序，尚未验证相似性与源码行为。'
        : '当前没有可用于推导的真实候选，推荐来自内置玩法模板；不表示已找到相似源码。',
    ],
    alternatives: ranked.map((a) => ({
      id: a.id,
      label: a.label,
      rationale: `${a.rationale} ${a.score ? '文字关键词提示此方向，仍需确认。' : '可作为其他玩法方向。'}`,
      candidateIds: a.candidateIds,
    })),
  };
}

export function ideaQuestions(
  proposal: IdeaProposal,
  currentArchetype?: string,
): ClarificationQuestion[] {
  const archetype = currentArchetype || proposal.alternatives[0].id;
  const mechanicQuestion: Record<string, ClarificationQuestion> = {
    runner: optional('routeRule', '跑道上的主要选择是什么？', 'gates', [
      ['gates', '数字门增减队伍（推荐）'],
      ['obstacles', '只躲避障碍、收集分数'],
    ]),
    survivor: optional('attackMode', '攻击如何触发？', 'automatic', [
      ['automatic', '自动攻击，专注走位（推荐）'],
      ['manual', '主动瞄准与攻击'],
    ]),
    puzzle: optional('puzzleRule', '核心解谜规则是什么？', 'merge', [
      ['merge', '相同数值合并（推荐）'],
      ['match', '三个相同元素连线消除'],
    ]),
    platformer: optional('jumpRule', '跳跃能力如何设置？', 'single', [
      ['single', '单次跳跃（推荐）'],
      ['double', '允许二段跳'],
    ]),
  };
  return [
    optional(
      'archetype',
      '从哪类游戏原型开始？',
      proposal.alternatives[0].id,
      proposal.alternatives.map((a) => [a.id, a.label]),
    ),
    optional('engine', '使用什么浏览器引擎？', 'phaser', [
      ['phaser', 'Phaser · 2D（推荐）'],
      ['html', '原生 HTML / Canvas'],
      ['babylon', 'Babylon.js · 3D 表现'],
    ]),
    optional('difficulty', '挑战程度如何？', 'normal', [
      ['normal', '普通（推荐）'],
      ['easy', '轻松'],
      ['hard', '困难'],
    ]),
    optional('visualStyle', '视觉风格如何？', 'minimal', [
      ['minimal', '简洁几何（推荐）'],
      ['pixel', '像素'],
      ['illustrated', '插画'],
    ]),
    optional('controls', '主要面向什么操作方式？', 'hybrid', [
      ['hybrid', '键鼠 + 触屏（推荐）'],
      ['keyboard', '仅键鼠'],
      ['touch', '仅触屏'],
    ]),
    optional('progression', '是否需要跨局成长？', 'session', [
      ['session', '只保留本局状态（推荐）'],
      ['persistent', '保存已解锁内容与进度'],
    ]),
    mechanicQuestion[archetype] ?? mechanicQuestion.runner,
    optional('implementationStyle', '实现方案偏好？', 'engine_native', [
      ['engine_native', '优先贴合引擎（推荐）'],
      ['portable', '优先可移植算法'],
    ]),
    optional('candidateCount', '每项机制最多比较几个候选？', '3', [
      ['3', '三个（推荐）'],
      ['2', '两个'],
      ['1', '一个'],
    ]),
  ];
}

export function ideaDecisions(input: StartRequest, proposal: IdeaProposal): Decision[] {
  const preferences = input.preferences ?? {};
  return ideaQuestions(proposal, preferences.archetype).map((q) => {
    const preference = preferences[q.questionId as keyof typeof preferences];
    return {
      id: q.questionId,
      value: preference !== undefined ? String(preference) : q.defaultOptionId!,
      source: preference !== undefined ? 'saved_preference' : 'system_default',
      locked: false,
      rationale: q.prompt,
    };
  });
}

/** Keep confirmed choices; replace obsolete archetype-specific questions with explicit defaults. */
export function refreshIdeaQuestions(run: Run): void {
  if (!run.proposal) return;
  const questions = ideaQuestions(run.proposal, chosen(run, 'archetype'));
  run.decisions = questions.map(
    (q) =>
      run.decisions.find((d) => d.id === q.questionId) ?? {
        id: q.questionId,
        value: q.defaultOptionId!,
        source: 'system_default',
        locked: false,
        rationale: q.prompt,
      },
  );
  run.decisionBatch.questions = questions;
}

export function ideaPlan(run: Run, candidates: MaterialCandidate[]): Plan {
  const mechanics: Mechanic[] = [],
    checks: AcceptanceCheck[] = [];
  const add = (
    id: string,
    title: string,
    behavior: string[],
    dependencies: string[],
    decisions: string[],
  ) => {
    const ids = behavior.map((requirement, i) => {
      const check = {
        id: `IDEA/${id}/${i + 1}`,
        category: title,
        requirement,
        derivedFrom: decisions,
        provisional: true,
      };
      checks.push(check);
      return check.id;
    });
    mechanics.push({
      id,
      title,
      behavior,
      dependencies,
      checks: ids,
      ownedState: [`${id} 状态`],
      inputs: ['生效配置', '暂停感知的模拟时间', ...dependencies.map((d) => `${d} 事件`)],
      outputs: [`${id} 事件`, '可观测的验收状态'],
    });
  };
  const controls = chosen(run, 'controls');
  add(
    'input',
    '输入与操作',
    [
      controls === 'touch'
        ? '触屏操作可完成核心动作，松手后清除输入。'
        : controls === 'keyboard'
          ? '键盘与指针可完成核心动作，失焦后清除输入。'
          : '键鼠与触屏均可完成核心动作，松手或失焦后清除输入。',
    ],
    [],
    ['controls'],
  );
  const archetype = chosen(run, 'archetype');
  let core: string;
  if (archetype === 'runner') {
    add(
      'forward-motion',
      '前进与横向移动',
      ['角色持续前进，横向移动限制在跑道边界内；暂停时停止模拟。'],
      ['input'],
      ['archetype'],
    );
    core = 'route-events';
    add(
      core,
      '路线选择与触发',
      [
        chosen(run, 'routeRule') === 'gates'
          ? '经过数字门时对队伍数量做一次加减运算，结果不低于零；同一门不可重复结算。'
          : '接触障碍扣除生命，收集物加分；同一物体仅结算一次。',
      ],
      ['forward-motion'],
      ['archetype', 'routeRule'],
    );
    add(
      'goal',
      '终点与失败结算',
      ['抵达终点判定胜利，生命或队伍归零判定失败；结束后不再结算碰撞。'],
      [core],
      ['archetype'],
    );
  } else if (archetype === 'survivor') {
    add(
      'enemy-pursuit',
      '自由移动与敌人追逐',
      ['玩家在区域内自由移动；敌人追逐玩家，碰撞伤害使用统一结算入口。'],
      ['input'],
      ['archetype'],
    );
    core = 'combat';
    add(
      core,
      '攻击与伤害判定',
      [
        chosen(run, 'attackMode') === 'manual'
          ? '瞄准并主动触发攻击；未命中时不造成伤害。'
          : '冷却结束后自动选择有效敌人攻击；未命中时不造成伤害。',
        '敌人死亡只结算一次，移除后不再被选中。',
      ],
      ['enemy-pursuit'],
      ['archetype', 'attackMode'],
    );
    add(
      'goal',
      '生存目标与局内升级',
      [
        '击败敌人获得局内经验；升级时从三个增益中选择，选择期间暂停战斗。',
        '默认生存 180 秒获胜，生命归零失败；重开清除局内经验与敌人。',
      ],
      [core],
      ['archetype'],
    );
  } else if (archetype === 'puzzle') {
    core = 'board-rules';
    add(
      core,
      '棋盘规则与结算',
      [
        chosen(run, 'puzzleRule') === 'match'
          ? '交换相邻元素，三个及以上同类元素连线后消除；无有效消除则撤销交换。'
          : '沿所选方向移动格子，相同数值合并为双倍；同一格每步最多参与一次合并。',
        '随机生成的新局面至少存在一个合法操作。',
      ],
      ['input'],
      ['archetype', 'puzzleRule'],
    );
    add(
      'goal',
      '解谜目标与重开',
      ['累计达到 1000 分获胜，无合法操作时失败；重开清空本局分数。'],
      [core],
      ['archetype'],
    );
  } else {
    core = 'platform-motion';
    add(
      core,
      '重力、跳跃与落地',
      [
        chosen(run, 'jumpRule') === 'double'
          ? '空中最多追加一次跳跃，落地后重置跳跃次数。'
          : '仅落地时可起跳，按住跳跃不会在空中重复触发。',
        '重力和平台碰撞按模拟时间计算，不允许穿过地面。',
      ],
      ['input'],
      ['archetype', 'jumpRule'],
    );
    add(
      'goal',
      '关卡终点与重试',
      ['抵达关卡终点获胜；掉出关卡扣除生命，生命归零失败；重开恢复角色与关卡状态。'],
      [core],
      ['archetype'],
    );
  }
  const difficulty = chosen(run, 'difficulty');
  const allowance = difficulty === 'easy' ? 5 : difficulty === 'hard' ? 1 : 3;
  add(
    'challenge',
    '难度与容错配置',
    [
      archetype === 'puzzle'
        ? `本局提供 ${allowance} 次合法操作提示；提示不直接改变棋盘。`
        : `本局初始容错为 ${allowance} 点生命${archetype === 'runner' && chosen(run, 'routeRule') === 'gates' ? '（数字门玩法对应初始队伍数量）' : ''}；失败后可重开。`,
    ],
    [core],
    ['difficulty', 'archetype'],
  );
  if (chosen(run, 'progression') === 'persistent')
    add(
      'save-progress',
      '跨局存档',
      ['保存已解锁内容与最好成绩；重开仅清除临时状态，损坏存档恢复为初始状态。'],
      ['goal'],
      ['progression'],
    );
  const crossCuttingChecks: AcceptanceCheck[] = [
    {
      id: 'IDEA/lifecycle',
      category: '生命周期',
      requirement: '开始、暂停、结束和重开状态互斥；重开不重复注册监听器。',
      derivedFrom: ['archetype'],
      provisional: true,
    },
    {
      id: 'IDEA/presentation',
      category: '表现风格',
      requirement: `以${{ minimal: '简洁几何', pixel: '像素', illustrated: '插画' }[chosen(run, 'visualStyle')] ?? '简洁几何'}风格表现游戏；视觉替换不改变玩法状态。`,
      derivedFrom: ['visualStyle'],
      provisional: true,
    },
  ];
  return {
    engine: selectedEngine(run),
    mechanics,
    crossCuttingChecks,
    acceptanceDraft: [...checks, ...crossCuttingChecks].map((c) => ({
      ...c,
      source: 'system_proposal',
    })),
    archetypeCandidateIds: candidates.map((c) => c.candidateId),
    foundations: {
      coordinates:
        selectedEngine(run) === 'babylon'
          ? '三维场景承载所选玩法，输入映射至玩法平面。'
          : '二维世界坐标；渲染缩放与输入转换分离。',
      timing: '使用暂停感知的模拟时间，避免独立定时器驱动玩法。',
      entityOwnership: '单一游戏状态；机制通过声明的事件协作。',
    },
  };
}
