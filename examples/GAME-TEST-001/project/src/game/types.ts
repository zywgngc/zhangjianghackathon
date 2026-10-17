// src/game/types.ts
// Feature f01-config-types: All shared type definitions for the game.

export type Phase = 'MENU' | 'PLAYING' | 'PAUSED' | 'UPGRADE' | 'VICTORY' | 'DEFEAT' | 'DEADLOCK' | 'SETTINGS' | 'UPGRADES' | 'LEVEL_UP';

export type GateOperator = '+' | '-' | '×' | '÷';

export type ObstacleKind = 'sandbag' | 'barbedWire' | 'mine';

export type EntityKind = 'soldier' | 'enemy' | 'boss' | 'projectile' | 'gate' | 'obstacle';

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface GateDef {
  operator: GateOperator;
  operand: number;
}

export interface GateGroupDef {
  gates: GateDef[];
  z: number;
}

export interface ObstacleDef {
  kind: ObstacleKind;
  x: number;
  z: number;
  loss: number;
}

export interface EnemyDef {
  id: string;
  x: number;
  z: number;
  hp: number;
}

export interface LevelConfig {
  level: number;
  name: string;
  theme: string;
  duration: number;
  bossHp: number;
  bossActivationZ?: number;
  reward: number;
  gateGroups: GateGroupDef[];
  obstacles: ObstacleDef[];
  enemies: EnemyDef[];
  trackLength: number;
}

export interface WeaponConfig {
  attacksPerSecond: number;
  bulletDamage: number;
  projectileSpeed: number;
  projectileLifetime: number;
}

export interface UpgradeInfo {
  id: string;
  name: string;
  level: number;
  maxLevel: number;
  baseCost: number;
}

export interface UpgradeConfig {
  maxLevel: number;
  baseCost: number;
  types: { id: string; name: string }[];
}

export interface GameConfig {
  permanentUpgrades?: Array<{id:string;canonicalId:string;name:string;maxLevel:number;baseCost:number}>;
  enemies?: Array<EnemyDef & {kind:'enemy';level:number}>;
  levels: LevelConfig[];
  weapon: WeaponConfig;
  upgrades: UpgradeConfig;
  roadWidth: number;
  maxSoldiers: number;
  minSoldiers: number;
  forwardSpeed: number;
  lateralSpeed: number;
  clampX: number;
  smoothing: number;
  initialSoldiers: number;
  obstacleLoss: Record<ObstacleKind, number>;
}

export interface PlayerState {
  x: number;
  y: number;
  z: number;
  hp: number;
  maxHp: number;
  power: number;
  soldiers: number;
}

export interface Entity {
  id: string;
  kind: EntityKind;
  x: number;
  y: number;
  z: number;
  hp: number;
  power: number;
  active: boolean;
  group?: number | string;
  maxHp?: number;
  consumed?: boolean;
  activationZ?: number;
  archetype?: string;
  obstacleType?: ObstacleKind;
  animation?: string;
  hitTime?: number;
  previousX?: number;
  previousY?: number;
  previousZ?: number;
  operator?: GateOperator;
  operand?: number;
  bonus?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  lifetime?: number;
  targetId?: string;
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
  upgradeChoices: SkillEntry[];
  effectiveStats: EffectiveStats;
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
  bulletDamage?: number;
}

export interface ProgressState {
  gold: number;
  unlockedLevel: number;
  completedLevels: number[];
  upgrades: Record<string, number>;
  audio: AudioSettings;
}

export interface AudioSettings {
  music: boolean;
  sfx: boolean;
}

export interface SkillAttackMetrics {
  [key: string]: number;
}

export interface MetricsState {
  updateLoops: number;
  ticks?: number;
  pools?: { projectiles: number; reusable: number };
  projectiles: number;
  enemies: number;
  events: number;
  shotsFired: number;
  totalDamage: number;
  enemiesDefeated: number;
  skillAttacks: SkillAttackMetrics;
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
}

export type InputCommandType =
  | 'start'
  | 'selectLevel'
  | 'pause'
  | 'resume'
  | 'restart'
  | 'move'
  | 'selectTarget'
  | 'chooseUpgrade'
  | 'purchase'
  | 'setAudio'
  | 'menu'
  | 'upgrades'
  | 'settings';

export interface InputCommand {
  type: InputCommandType;
  level?: number;
  seed?: number;
  x?: number;
  y?: number;
  z?: number;
  dx?: number;
  dy?: number;
  id?: string;
  ground?: { x: number; z: number };
  music?: boolean;
  sfx?: boolean;
  args?: Record<string, unknown>;
}

export type GameEventType =
  | 'damage'
  | 'death'
  | 'gate'
  | 'xp'
  | 'upgrade'
  | 'victory'
  | 'defeat'
  | 'navigation'
  | 'shoot'
  | 'spawn'
  | 'collision';

export interface GameEvent {
  seq: number;
  simTime: number;
  type: GameEventType;
  data: Record<string, unknown>;
}

export interface SimulationOptions {
  level?: number;
  seed?: number;
  fixture?: FixtureData;
  save?: SaveData;
  isolated?: boolean;
}

export interface FixtureData {
  phase?: Phase;
  level?: number;
  player?: Partial<PlayerState>;
  skills?: Partial<SkillsState>;
  progress?: Partial<ProgressState>;
  enemies?: Partial<Entity>[];
  weapons?: Partial<WeaponConfig>;
  gates?: Array<GateGroupDef | (Partial<Entity> & { operator: GateOperator | '*' | '/'; operand: number })>;
  obstacles?: Array<Partial<Entity> & { kind?: string; loss?: number }>;
  boss?: Partial<Entity> | null;
  trackEnd?: number;
  spawnEnabled?: boolean;
}

export interface SaveData {
  gold: number;
  unlockedLevel: number;
  completedLevels: number[];
  upgrades: Record<string, number>;
  audio: AudioSettings;
}

export interface EngineInfo {
  name: string;
  version: string;
  running: boolean;
}

export interface ProjectedCoord {
  x: number;
  y: number;
}

export type EventSink = (event: GameEvent) => void;

export interface Projectile {
  id: string;
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  damage: number;
  lifetime: number;
  active: boolean;
  targetId?: string;
}

export interface SkillDef {
  id: string;
  name: string;
  description: string;
  maxLevel: number;
  baseCost: number;
  costMultiplier: number;
  type: 'active' | 'passive' | 'ultimate';
  effects: Record<string, number[]>;
}
