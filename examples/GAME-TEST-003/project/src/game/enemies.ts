// src/game/enemies.ts - Enemy and Boss lifecycle management
// Owner: entity-team (f07-enemies-boss)

import type { GameState, GameConfig, GameEvent, Entity } from './types';
import type { PRNG } from './prng';

/**
 * Update all normal enemies each tick.
 * Enemies are stationary; this handles:
 * 1. Removing defeated (hp <= 0) enemies
 * 2. Updating metrics
 */
export function updateEnemies(
  state: GameState,
  dt: number,
  config: GameConfig,
  rng: PRNG,
  emit: (e: GameEvent) => void
): void {
  if (state.phase !== 'PLAYING') return;

  let enemyCount = 0;

  for (let i = state.entities.length - 1; i >= 0; i--) {
    const e = state.entities[i];
    if (!isNormalEnemy(e)) continue;

    if (!e.active || e.hp <= 0) {
      // Combat owns the real defeat/absorption event and metric. Retain an
      // inactive entity for state history instead of counting it a second time.
      e.active = false;
      continue;
    }

    enemyCount++;
  }

  state.metrics.enemies = enemyCount;
}

// Backward-compatible import surface; the real lifecycle lives in boss.ts and
// returns a completion signal. It never bypasses Simulation's save owner.
export { updateBoss } from './boss';

/**
 * Deadlock detection: player cannot defeat any remaining enemy or boss.
 * Returns true if deadlock is detected.
 */
export function checkDeadlock(state: GameState, config: GameConfig): boolean {
  if (state.phase !== 'PLAYING') return false;

  const playerPower = state.player.power;
  const comparison = config.comparison;

  let hasRemainingEnemy = false;
  let canDefeatAny = false;

  for (const e of state.entities) {
    if (!e.active || e.hp <= 0) continue;
    if (!isNormalEnemy(e) && !isBossEntity(e)) continue;

    hasRemainingEnemy = true;

    if (canPlayerDefeat(playerPower, e.power, comparison)) {
      canDefeatAny = true;
      break;
    }
  }

  // Deadlock: there are enemies left but player can't defeat any of them
  // Also check if there are no weapons left to pick up that could help
  if (hasRemainingEnemy && !canDefeatAny) {
    // Check if there are any collectible weapons remaining
    const hasWeaponsLeft = state.entities.some(
      e => e.active && e.kind === 'weapon'
    );
    if (!hasWeaponsLeft) {
      return true;
    }
  }

  return false;
}

// --- Internal helpers ---

function isNormalEnemy(e: Entity): boolean {
  return e.kind === 'enemy' || e.group === 'enemy';
}

function isBossEntity(e: Entity): boolean {
  return e.kind === 'boss' || e.group === 'boss';
}

function canPlayerDefeat(playerPower: number, enemyPower: number, comparison: string): boolean {
  switch (comparison) {
    case 'P > E':
      return playerPower > enemyPower;
    case 'P >= E':
      return playerPower >= enemyPower;
    default:
      return playerPower > enemyPower;
  }
}
