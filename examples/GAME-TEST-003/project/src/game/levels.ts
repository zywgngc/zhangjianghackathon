// src/game/levels.ts - Level management, entity placement, victory/reset logic
// Owner: level-team

import type { GameState, GameConfig, Entity } from './types';
import { PRNG } from './prng';

/**
 * Initialize a level: set phase, player stats, spawn enemies/weapons/boss/obstacles.
 */
export function initLevel(
  state: GameState,
  levelId: number,
  config: GameConfig,
  rng: PRNG
): void {
  const levelConfig = config.levels.find((l) => l.id === levelId);
  if (!levelConfig) return;

  // Clamp to valid range
  const clampedLevel = Math.max(1, Math.min(levelId, config.levels.length));

  state.phase = 'PLAYING';
  state.level = clampedLevel;
  state.simTime = 0;

  // Official placements use a map centered at world (0, 0).
  // Keep the spawn on its center line, inside both signed map bounds.
  state.player.x = 0;
  state.player.y = 0;
  state.player.z = 2;
  state.player.hp = 100;
  state.player.maxHp = 100;
  state.player.power = levelConfig.initialPower;
  state.player.soldiers = 0;

  // Clear interaction state
  state.targetId = null;
  state.path = [];
  state.interactionLock = false;
  state.attackTimer = 0;
  state.absorptionTimer = 0;

  // Reset metrics for this level run
  state.metrics.updateLoops = 0;
  state.metrics.projectiles = 0;
  state.metrics.enemies = 0;
  state.metrics.events = 0;
  state.metrics.shotsFired = 0;
  state.metrics.totalDamage = 0;
  state.metrics.enemiesDefeated = 0;
  state.metrics.skillAttacks = { W01: 0 };

  // Build entities array
  state.entities = [];

  // Spawn enemies from level placement config
  for (const placement of levelConfig.enemies) {
    const enemyCfg = config.enemies.find((e) => e.id === placement.enemyId);
    const kind = enemyCfg ? enemyCfg.kind : 'goblin';
    const hp = enemyCfg ? enemyCfg.hp : 1;
    const enemy: Entity = {
      id: `enemy_${placement.enemyId}_${state.entities.length}`,
      kind,
      x: placement.x,
      y: 0,
      z: placement.z,
      hp,
      power: placement.power,
      active: true,
      group: 'enemy',
    };
    state.entities.push(enemy);
  }

  // Spawn weapons from level placement config
  for (const wp of levelConfig.weapons) {
    const weaponCfg = config.weapons.find((w) => w.id === wp.weaponId);
    const bonus = weaponCfg ? weaponCfg.bonus : 5;
    const weapon: Entity = {
      id: `weapon_${wp.weaponId}_${state.entities.length}`,
      kind: 'weapon',
      x: wp.x,
      y: 0,
      z: wp.z,
      hp: 1,
      power: 0,
      active: true,
      group: 'weapon',
      bonus,
    };
    state.entities.push(weapon);
  }

  // Spawn boss
  const bossEntity: Entity = {
    id: `boss_${levelConfig.boss.id}`,
    kind: 'boss',
    x: levelConfig.boss.x,
    y: 0,
    z: levelConfig.boss.z,
    hp: levelConfig.boss.hp,
    power: levelConfig.boss.power,
    active: true,
    group: 'boss',
  };
  state.entities.push(bossEntity);

  // Spawn obstacles as entities for collision reference
  for (const obs of levelConfig.obstacles) {
    const obstacle: Entity = {
      id: `obs_${obs.id}`,
      kind: obs.kind,
      x: obs.x,
      y: 0,
      z: obs.z,
      hp: 999999,
      power: 0,
      active: true,
      group: 'obstacle',
      operand: obs.width,
      bonus: obs.depth,
    };
    state.entities.push(obstacle);
  }

  // Update progress tracking
  if (clampedLevel > state.progress.unlockedLevel) {
    // Only allow playing unlocked levels; don't auto-unlock here
  }
}

/**
 * Check if the current level is won.
 * Victory condition: boss of current level is defeated (hp <= 0 or inactive).
 */
export function checkVictory(state: GameState, config: GameConfig): boolean {
  if (state.phase !== 'PLAYING') return false;

  const levelConfig = config.levels.find((l) => l.id === state.level);
  if (!levelConfig) return false;

  // Find the boss entity for this level
  const bossId = `boss_${levelConfig.boss.id}`;
  const boss = state.entities.find((e) => e.id === bossId);

  // Boss defeated means victory
  if (boss && !boss.active) {
    return true;
  }
  if (boss && boss.hp <= 0) {
    return true;
  }

  // Also check if all enemies and boss are dead/inactive
  const activeHostiles = state.entities.filter(
    (e) => e.active && (e.group === 'enemy' || e.group === 'boss')
  );
  if (activeHostiles.length === 0 && state.entities.some((e) => e.group === 'boss')) {
    return true;
  }

  return false;
}

/**
 * Reset the current level to initial state for replay.
 * Preserves progress (unlocked levels, completed levels, upgrades).
 */
export function resetLevel(state: GameState, config: GameConfig): void {
  const currentLevel = state.level;
  const savedProgress = { ...state.progress };
  const savedSkills = {
    active: [...state.skills.active],
    passive: [...state.skills.passive],
    ultimate: [...state.skills.ultimate],
    upgradeChoices: [...state.skills.upgradeChoices],
  };
  const savedEffectiveStats = { ...state.effectiveStats };

  // Create a deterministic seed from level id for consistent reset
  // We use a simple PRNG-like approach with fixed seed per level
  const resetRng: PRNG = createDeterministicRng(currentLevel * 31337);

  initLevel(state, currentLevel, config, resetRng);

  // Restore persistent data that should survive reset
  state.progress = savedProgress;
  state.skills = savedSkills;
  state.effectiveStats = savedEffectiveStats;
  state.phase = 'PLAYING';
}

/**
 * Unlock next level after completing current one.
 */
export function unlockNextLevel(state: GameState, config: GameConfig): void {
  const currentLevel = state.level;
  const completedLevels = state.progress.completedLevels;

  if (!completedLevels.includes(currentLevel)) {
    completedLevels.push(currentLevel);
  }

  const maxLevel = config.levels.length;
  if (currentLevel < maxLevel && state.progress.unlockedLevel <= currentLevel) {
    state.progress.unlockedLevel = currentLevel + 1;
  }
}

/**
 * Full game reset: clear all progress, start from level 1.
 */
export function fullReset(state: GameState, config: GameConfig): void {
  state.progress.gold = 0;
  state.progress.unlockedLevel = 1;
  state.progress.completedLevels = [];
  state.progress.upgrades = {};

  state.skills.active = [];
  state.skills.passive = [];
  state.skills.ultimate = [];
  state.skills.upgradeChoices = [];

  state.effectiveStats.maxHp = 100;
  state.effectiveStats.damageMultiplier = 1;
  state.effectiveStats.damageReduction = 0;
  state.effectiveStats.moveSpeed = 5;
  state.effectiveStats.cooldownReduction = 0;
  state.effectiveStats.healOnKillChance = 0;
  state.effectiveStats.xpMultiplier = 1;
  state.effectiveStats.rangeMultiplier = 1;

  state.phase = 'MENU';
  state.level = 0;
  state.entities = [];
  state.targetId = null;
  state.path = [];
  state.interactionLock = false;
  state.attackTimer = 0;
  state.absorptionTimer = 0;
  state.simTime = 0;
}

/** Use the shared seeded Mulberry32 implementation, including its real state. */
function createDeterministicRng(seed: number): PRNG {
  return new PRNG(seed);
}
