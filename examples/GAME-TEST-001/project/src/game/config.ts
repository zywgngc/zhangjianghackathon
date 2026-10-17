// src/game/config.ts
// Feature f01-config-types: Complete game configuration data, constants, and upgrade formulas.

import type {
  GameConfig,
  LevelConfig,
  GateGroupDef,
  ObstacleDef,
  EnemyDef,
} from './types';

/** Parse gate operator string from approved configuration to typed operator. */
function parseOperator(op: string): '+' | '-' | '×' | '÷' {
  if (op === '+' || op === '-' || op === '×' || op === '÷') return op;
  // Fallback for ASCII variants that might appear in source data
  if (op === '*' || op === 'x' || op === 'X') return '×';
  if (op === '/') return '÷';
  return '+';
}

/** Parse operand string to number. */
function parseOperand(val: string): number {
  const n = Number(val);
  return Number.isFinite(n) ? n : 0;
}

/** Build gate groups from the approved configuration gate arrays. */
function buildGateGroups(
  gates: string[][],
  trackLength: number
): GateGroupDef[] {
  const count = gates.length;
  if (count === 0) return [];
  // Distribute gates evenly along the track, starting at 20% and ending at 85%
  const startFraction = 0.2;
  const endFraction = 0.85;
  const groups: GateGroupDef[] = [];
  for (let i = 0; i < count; i++) {
    const fraction =
      count === 1
        ? (startFraction + endFraction) / 2
        : startFraction + (endFraction - startFraction) * (i / (count - 1));
    const z = trackLength * fraction;
    const pair = gates[i];
    const groupGates = pair.map((g) => {
      // Each gate string like "+5" or "×2" or "-3" or "÷2"
      const opStr = g.charAt(0);
      const valStr = g.substring(1);
      return {
        operator: parseOperator(opStr),
        operand: parseOperand(valStr),
      };
    });
    groups.push({ gates: groupGates, z });
  }
  return groups;
}

/** Generate deterministic obstacles for a level based on its index. */
function generateObstacles(levelIndex: number, trackLength: number): ObstacleDef[] {
  const obstacles: ObstacleDef[] = [];
  const kinds: Array<'sandbag' | 'barbedWire' | 'mine'> = levelIndex === 0
    ? ['sandbag', 'barbedWire'] : ['sandbag', 'barbedWire', 'mine'];
  const losses: Record<string, number> = {
    sandbag: 2,
    barbedWire: 3,
    mine: 5,
  };
  // Deterministic placement using simple hash-like pattern
  const count = 4 + levelIndex * 2;
  for (let i = 0; i < count; i++) {
    const seed = (levelIndex + 1) * 1000 + i * 137;
    const kindIdx = seed % kinds.length;
    const kind = kinds[kindIdx];
    const xRange = 5.0;
    // Pseudo-deterministic x position
    const x = ((seed * 7 + 13) % 1000) / 1000 * xRange * 2 - xRange;
    // Spread obstacles ahead between 15% and 90% of the +Z track.
    const zFraction = 0.15 + (((seed * 31 + 7) % 1000) / 1000) * 0.75;
    const z = trackLength * zFraction;
    obstacles.push({
      kind,
      x: Math.round(x * 100) / 100,
      z: Math.round(z * 100) / 100,
      loss: losses[kind],
    });
  }
  return obstacles;
}

/** Generate deterministic enemies for a level based on its index. */
function generateEnemies(levelIndex: number, trackLength: number): EnemyDef[] {
  const enemies: EnemyDef[] = [];
  // C1-R06: Normal enemy HP is fixed at 10 across all levels per spec 07.4
  const normalHp = 10;
  const count = 3 + levelIndex * 2;
  for (let i = 0; i < count; i++) {
    const seed = (levelIndex + 1) * 2000 + i * 251;
    const xRange = 4.5;
    const x = ((seed * 11 + 23) % 1000) / 1000 * xRange * 2 - xRange;
    // Enemies placed ahead between 30% and 80% of the +Z track.
    const zFraction = 0.3 + (((seed * 41 + 17) % 1000) / 1000) * 0.5;
    const z = trackLength * zFraction;
    enemies.push({
      id: `enemy_L${levelIndex + 1}_${i}`,
      x: Math.round(x * 100) / 100,
      z: Math.round(z * 100) / 100,
      hp: normalHp,
    });
  }
  return enemies;
}

/** Approved raw level data from specification review. */
const RAW_LEVELS = [
  {
    level: 1,
    name: '沙漠前线',
    duration: 45,
    bossHp: 150,
    reward: 100,
    gates: [['+5', '+2'], ['×2', '-3'], ['+10', '÷2'], ['×2', '-5']],
  },
  {
    level: 2,
    name: '战地防线',
    duration: 60,
    bossHp: 300,
    reward: 200,
    gates: [['+8', '+3'], ['×2', '-5'], ['+12', '÷2'], ['×2', '-8'], ['+20', '÷3']],
  },
  {
    level: 3,
    name: '军事基地',
    duration: 75,
    bossHp: 500,
    reward: 300,
    gates: [
      ['+10', '+4'],
      ['×3', '-8'],
      ['÷2', '+15'],
      ['×2', '-10'],
      ['+20', '÷3'],
      ['×2', '-15'],
    ],
  },
];

const THEMES = ['desert', 'battlefield', 'military_base'];

/** Build full LevelConfig array from approved data. */
function buildLevels(): LevelConfig[] {
  return RAW_LEVELS.map((raw, idx) => {
    // Track length derived from forwardSpeed * duration
    const trackLength = 8 * raw.duration;
    const gateGroups = buildGateGroups(raw.gates, trackLength);
    const obstacles = generateObstacles(idx, trackLength);
    const enemies = generateEnemies(idx, trackLength);
    return {
      level: raw.level,
      name: raw.name,
      theme: THEMES[idx] || 'desert',
      duration: raw.duration,
      bossHp: raw.bossHp,
      bossActivationZ: Math.min(trackLength - 20, (gateGroups[gateGroups.length-1]?.z ?? trackLength*.8) + 12),
      reward: raw.reward,
      gateGroups,
      obstacles,
      enemies,
      trackLength,
    };
  });
}

/** C1-R04: Spec 10.2 requires exactly two upgrades: Initial Soldier Count and Weapon Damage. */
const UPGRADE_TYPES = [
  { id: 'soldiers', name: '初始士兵数' },
  { id: 'damage', name: '武器伤害' },
];

/** C1-R05: Spec 10.5 cost formula is 100*(currentLevel+1). baseCost param kept for interface compat. */
export function getUpgradeCost(_baseCost: number, currentLevel: number): number {
  return 100 * (currentLevel + 1);
}

/** The complete game configuration matching all approved values. */
export const GAME_CONFIG: GameConfig = {
  levels: buildLevels(),
  weapon: {
    attacksPerSecond: 2,
    bulletDamage: 1,
    projectileSpeed: 30,
    projectileLifetime: 3,
  },
  upgrades: {
    maxLevel: 10,
    baseCost: 100,
    types: UPGRADE_TYPES,
  },
  roadWidth: 12,
  maxSoldiers: 200,
  minSoldiers: 0,
  forwardSpeed: 8,
  lateralSpeed: 6,
  clampX: 5.5,
  smoothing: 0.1,
  initialSoldiers: 1,
  obstacleLoss: {
    sandbag: 2,
    barbedWire: 3,
    mine: 5,
  },
};

/** Get a specific level config by level number (1-indexed). */
export function getLevelConfig(level: number): LevelConfig | undefined {
  return GAME_CONFIG.levels.find((l) => l.level === level);
}

/** Constants used across the game. */
export const GAME_CONSTANTS = {
  TICK_RATE: 1000 / 60, // ~16.67ms per tick
  PLAYER_Y: 0,
  BOSS_Z_OFFSET: -10, // +Z world: spawn ten units before the positive track end.
  GATE_WIDTH: 4, // Half-width of each gate lane
  SOLDIER_SPREAD: 0.5, // Lateral spread per soldier in formation
  PROJECTILE_Y: 1, // Height of projectiles
} as const;
