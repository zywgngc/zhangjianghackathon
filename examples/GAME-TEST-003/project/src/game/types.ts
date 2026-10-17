// src/game/types.ts - Unified type protocol for Power Hunter (百炼英雄)
// Owner: config-team

export type Phase = 'MENU' | 'PLAYING' | 'PAUSED' | 'UPGRADE' | 'VICTORY' | 'DEFEAT' | 'DEADLOCK';

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface PlayerState {
  x: number;
  y: number;
  z: number;
  hp: number;
  maxHp: number;
  power: number;
  soldiers: number;
  facing?: number;
  rotationY?: number;
  animation?: 'idle' | 'walk' | 'attack' | 'absorb';
}

export interface Entity {
  id: string;
  kind: string;
  x: number;
  y: number;
  z: number;
  hp: number;
  power: number;
  active: boolean;
  archetype?: string;
  name?: string;
  group?: string;
  operator?: string;
  operand?: number;
  bonus?: number;
}

export interface SkillEntry {
  id: string;
  level: number;
}

export interface UltimateEntry {
  id: string;
}

export interface SkillsState {
  active: SkillEntry[];
  passive: SkillEntry[];
  ultimate: UltimateEntry[];
  upgradeChoices: string[];
}

export interface ProgressState {
  gold: number;
  unlockedLevel: number;
  completedLevels: number[];
  upgrades: Record<string, number>;
  audio: { music: boolean; sfx: boolean };
}

export interface SkillAttackCounts {
  W01: number;
  [key: string]: number;
}

export interface MetricsState {
  updateLoops: number;
  projectiles: number;
  enemies: number;
  events: number;
  shotsFired: number;
  totalDamage: number;
  enemiesDefeated: number;
  skillAttacks: SkillAttackCounts;
}

export interface EffectiveStats {
  maxHp: number;
  damageMultiplier: number;
  damageReduction: number;
  moveSpeed: number;
  cooldownReduction: number;
  healOnKillChance: number;
  xpMultiplier: number;
  rangeMultiplier: number;
}

export interface GameState {
  phase: Phase;
  level: number;
  simTime: number;
  player: PlayerState;
  entities: Entity[];
  skills: SkillsState;
  progress: ProgressState;
  metrics: MetricsState;
  effectiveStats: EffectiveStats;
  targetId: string | null;
  path: Vec3[];
  interactionLock: boolean;
  attackTimer: number;
  absorptionTimer: number;
  pendingAbsorption?: {entityId:string;power:number;oldPower:number;x:number;z:number};
  uiScreen?: 'main' | 'game' | 'levels' | 'settings';
}

export type Snapshot = Readonly<GameState>;

export interface GameEvent {
  seq: number;
  simTime: number;
  type: string;
  data: Record<string, unknown>;
}

// --- Configuration Types ---

export interface EnemyConfig {
  id: string;
  name: string;
  kind: string;
  basePower: number;
  hp: number;
}

export interface WeaponConfig {
  id: string;
  name: string;
  bonus: number;
}

export interface BossConfig {
  id: string;
  name: string;
  power: number;
  hp: number;
  x: number;
  z: number;
}

export interface ObstacleConfig {
  id: string;
  kind: 'wall' | 'rock' | 'pillar' | 'tree';
  x: number;
  z: number;
  width: number;
  depth: number;
}

export interface LevelEntityPlacement {
  enemyId: string;
  power: number;
  x: number;
  z: number;
}

export interface LevelWeaponPlacement {
  weaponId: string;
  x: number;
  z: number;
}

export interface LevelConfig {
  id: number;
  name: string;
  theme: string;
  initialPower: number;
  mapWidth: number;
  mapHeight: number;
  enemies: LevelEntityPlacement[];
  weapons: LevelWeaponPlacement[];
  boss: BossConfig;
  obstacles: ObstacleConfig[];
}

export interface NavGrid {
  width: number;
  height: number;
  cellSize: number;
  /** World-space origin; omitted legacy grids retain origin (0, 0). */
  originX?: number;
  originZ?: number;
  blocked: boolean[];
}

export interface EngineInfo {
  resources?: Record<string, number>;
  name: string;
  version: string;
  running: boolean;
}

export interface IsolationParams {
  caseId: string;
  seed?: number;
  fixture?: SimulationFixture;
  render?: boolean;
  ui?: boolean;
}

export interface SimulationHandle {
  snapshot(): Snapshot;
  config(): GameConfig;
  dispatch(command: InputCommand): void;
  advance(milliseconds: number): void;
  events(afterSequence?: number): GameEvent[];
  dispose(): void;
}

export interface GameConfig {
  levels: LevelConfig[];
  enemies: EnemyConfig[];
  weapons: WeaponConfig[];
  bosses: BossConfig[];
  playerMoveSpeed: number;
  playerCollisionRadius: number;
  enemyInteractionRange: number;
  weaponPickupRange: number;
  attackDuration: number;
  absorptionDuration: number;
  powerMin: number;
  powerMax: number;
  comparison: string;
  enemyAbsorption: number;
}

// --- Input Commands ---

export type InputCommand =
  | { type: 'menu' }
  | { type: 'openLevels' }
  | { type: 'settings' }
  | { type: 'start'; level: number; seed?: number }
  | { type: 'selectLevel'; level: number }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'restart' }
  | { type: 'move'; x?: number; y?: number; z?: number; dx?: number; dy?: number }
  | { type: 'selectTarget'; id?: string; ground?: { x: number; z: number } }
  | { type: 'chooseUpgrade'; id: string }
  | { type: 'purchase'; id: string }
  | { type: 'setAudio'; music?: boolean; sfx?: boolean };

// --- Simulation Options ---

export interface SimulationFixture {
  level?: number;
  phase?: 'MENU' | 'PLAYING' | 'UPGRADE';
  progress?: Partial<ProgressState>;
  player?: Partial<PlayerState>;
  skills?: Partial<SkillsState>;
  enemies?: Array<Partial<Entity> & Pick<Entity, 'id' | 'x' | 'z'>>;
  weapons?: Array<Partial<Entity> & Pick<Entity, 'id' | 'x' | 'z'>>;
  gates?: Entity[];
  obstacles?: Array<Partial<ObstacleConfig> & Pick<ObstacleConfig, 'id' | 'x' | 'z' | 'width' | 'depth'>>;
  boss?: (Partial<Entity> & Pick<Entity, 'id' | 'x' | 'z'>) | null;
  trackEnd?: { x: number; z: number };
}

/** @deprecated Use SimulationSave instead */
export type SaveData = SimulationSave;

export interface SimulationSave {
  progress?: Partial<ProgressState>;
}

export interface SimulationOptions {
  level?: number;
  seed?: number;
  fixture?: SimulationFixture;
  save?: SimulationSave;
  /** Isolated worlds never read or write the main localStorage save. */
  persist?: boolean;
}
