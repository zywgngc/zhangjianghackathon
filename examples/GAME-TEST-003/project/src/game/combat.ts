// src/game/combat.ts - Combat power comparison and resolution system
// Owner: combat-team (f05-combat-power)

import type { GameState, GameConfig, GameEvent, Entity } from './types';
import type { PRNG } from './prng';

/** Production entity groups and configured enemy archetypes are combat targets. */
export function isCombatTarget(entity: Entity, config: GameConfig): boolean {
  if (entity.kind === 'weapon' || entity.kind === 'pickup') return false;
  return entity.kind === 'enemy' || entity.kind === 'boss' ||
    entity.group === 'enemy' || entity.group === 'boss' ||
    config.enemies.some(e => e.kind === entity.kind) ||
    config.bosses.some(b => b.id === entity.id);
}

/**
 * Core combat update loop.
 * Handles attack animation timing, absorption timing, strict P > E comparison,
 * interaction locking, enemy defeat, boss victory, and event emission.
 *
 * @param state  - Mutable game state (modified in place)
 * @param dt     - Delta time in seconds
 * @param config - Game configuration constants
 * @param rng    - Deterministic PRNG instance (reserved for future combat variance)
 * @param emit   - Event sink for combat events
 */
export function updateCombat(
  state: GameState,
  dt: number,
  config: GameConfig,
  _rng: PRNG,
  emit: (e: GameEvent) => void
): void {
  // Only process combat during active gameplay
  if (state.phase !== 'PLAYING') {
    return;
  }

  const attackRange = config.enemyInteractionRange; // 1.2
  const attackDuration = config.attackDuration; // 0.6s
  const absorptionDuration = config.absorptionDuration; // 0.5s

  // --- Phase 1: Attack Animation ---
  if (state.attackTimer > 0) {
    state.attackTimer -= dt;
    if (state.attackTimer <= 1e-9) {
      state.attackTimer = 0;
      resolveAttack(state, config, emit);
    }
    return; // Locked during attack animation
  }

  // --- Phase 2: Absorption Animation ---
  if (state.absorptionTimer > 0) {
    state.absorptionTimer -= dt;
    if (state.absorptionTimer <= 1e-9) {
      state.absorptionTimer = 0;
      const pending = state.pendingAbsorption;
      if (pending) {
        const oldPower = state.player.power;
        state.player.power = Math.floor(Math.max(config.powerMin,
          Math.min(config.powerMax, oldPower + pending.power)));
        emit({seq:0,simTime:state.simTime,type:'power_absorbed',data:{
          entityId:pending.entityId,oldPower,newPower:state.player.power,
          absorbedPower:state.player.power-oldPower,fromX:pending.x,fromZ:pending.z}});
        state.pendingAbsorption = undefined;
      }
      state.interactionLock = false;
    }
    return; // Locked during absorption animation
  }

  // --- Phase 3: Auto-attack target when in range ---
  if (state.interactionLock) {
    return;
  }

  if (!state.targetId) {
    return;
  }

  const target = state.entities.find(e => e.id === state.targetId && e.active);
  if (!target) {
    // Target no longer exists or inactive, clear selection
    state.targetId = null;
    state.path = [];
    return;
  }

  // A selected weapon remains a navigation/pickup target. Never consume it
  // through enemy defeat logic, even while it is inside attack range.
  if (!isCombatTarget(target, config)) return;

  // Calculate distance to target (XZ plane)
  const dx = target.x - state.player.x;
  const dz = target.z - state.player.z;
  const distSq = dx * dx + dz * dz;
  const rangeSq = attackRange * attackRange;

  if (distSq <= rangeSq) {
    // Within attack range - validate strict inequality P > E
    if (state.player.power > target.power) {
      // Valid attack - start attack animation
      state.attackTimer = attackDuration;
      state.interactionLock = true;

      emit({
        seq: state.metrics.events++,
        simTime: state.simTime,
        type: 'combat_attack_start',
        data: {
          playerId: 'player',
          targetId: target.id,
          playerPower: state.player.power,
          enemyPower: target.power
        }
      });
    } else {
      // Invalid: P <= E, cannot defeat this enemy
      emit({
        seq: state.metrics.events++,
        simTime: state.simTime,
        type: 'combat_invalid_warning',
        data: {
          reason: 'power_insufficient',
          playerPower: state.player.power,
          enemyPower: target.power,
          targetId: target.id
        }
      });
      // Clear target to prevent repeated warnings every frame
      state.targetId = null;
      state.path = [];
    }
  }
}

/**
 * Resolves a completed attack: defeats enemy, absorbs power, checks boss/victory.
 */
function resolveAttack(
  state: GameState,
  config: GameConfig,
  emit: (e: GameEvent) => void
): void {
  if (!state.targetId) {
    state.interactionLock = false;
    return;
  }

  const targetIndex = state.entities.findIndex(e =>
    e.id === state.targetId && e.active && isCombatTarget(e, config));
  if (targetIndex === -1) {
    state.targetId = null;
    state.interactionLock = false;
    state.path = [];
    return;
  }

  const target = state.entities[targetIndex];

  // Re-validate strict inequality at resolution time (defensive)
  if (!(state.player.power > target.power)) {
    emit({
      seq: state.metrics.events++,
      simTime: state.simTime,
      type: 'combat_invalid_warning',
      data: {
        reason: 'power_changed_during_attack',
        playerPower: state.player.power,
        enemyPower: target.power,
        targetId: target.id
      }
    });
    state.targetId = null;
    state.interactionLock = false;
    state.path = [];
    return;
  }

  // Absorb enemy power at configured rate (default 100% = 1)
  const absorbedPower = Math.floor(target.power * config.enemyAbsorption);
  const oldPower = state.player.power;
  let newPower = oldPower + absorbedPower;

  // Clamp to valid integer range [powerMin, powerMax]
  newPower = Math.max(config.powerMin, Math.min(config.powerMax, newPower));
  // Ensure integer
  newPower = Math.floor(newPower);

  state.pendingAbsorption = {entityId:target.id,power:absorbedPower,oldPower,x:target.x,z:target.z};

  // Deactivate enemy
  target.active = false;
  target.hp = 0;

  // Update metrics
  state.metrics.enemiesDefeated++;
  state.metrics.totalDamage += target.power;

  // Emit defeat event
  emit({
    seq: state.metrics.events++,
    simTime: state.simTime,
    type: 'enemy_defeated',
    data: {
      enemyId: target.id,
      enemyKind: target.kind,
      enemyPower: target.power,
      absorbedPower: absorbedPower,
      playerOldPower: oldPower,
      playerNewPower: oldPower,
      pendingAbsorption: absorbedPower
    }
  });

  // Check if defeated entity is a boss -> trigger victory
  const levelConfig = config.levels.find(l => l.id === state.level);
  const isBoss = levelConfig && levelConfig.boss &&
    (target.kind === 'boss' || target.group === 'boss' || target.id === levelConfig.boss.id);

  if (isBoss) {
    // The owning Simulation finalizes victory after the real absorption.
    // This module never bypasses completion, unlock or persistence.
    emit({
      seq: state.metrics.events++,
      simTime: state.simTime,
      type: 'boss_defeated',
      data: {
        bossId: target.id,
        bossName: levelConfig!.boss.name,
        level: state.level
      }
    });
    emit({
      seq: state.metrics.events++,
      simTime: state.simTime,
      type: 'level_victory',
      data: {
        level: state.level,
        finalPower: state.player.power
      }
    });
  }

  // Start absorption timer
  state.absorptionTimer = config.absorptionDuration;
  // Clear target and path after resolution
  state.targetId = null;
  state.path = [];
  // interactionLock remains true until absorption completes
}
