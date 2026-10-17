// src/game/systems/CollisionSystem.ts
// Feature f03-combat: Obstacle collision detection and soldier deduction.

import type { GameState, GameEvent } from '../types';

export type EventSink = (event: Omit<GameEvent, 'seq' | 'simTime'>) => void;

/**
 * Collision radius for player-obstacle interaction (units).
 */
const PLAYER_COLLISION_RADIUS = 1.5;
const PLAYER_COLLISION_RADIUS_SQ = PLAYER_COLLISION_RADIUS * PLAYER_COLLISION_RADIUS;

/**
 * Checks all active obstacles against the player position.
 * When a collision is detected, deducts soldiers according to obstacle kind:
 *   sandbag: 2, barbedWire: 3, mine: 5
 * If soldiers reach 0 or below, phase transitions to DEFEAT.
 */
export function checkObstacleCollisions(
  state: GameState,
  emit: EventSink
): void {
  if (state.phase !== 'PLAYING') return;
  if (state.player.soldiers <= 0) return;

  const px = state.player.x;
  const pz = state.player.z;

  for (const entity of state.entities) {
    if (!entity.active) continue;
    if (entity.kind !== 'obstacle') continue;

    const dx = px - entity.x;
    const dz = pz - entity.z;
    const distSq = dx * dx + dz * dz;

    if (distSq <= PLAYER_COLLISION_RADIUS_SQ) {
      // Determine loss from the entity's power field (set by config/level setup)
      // Fallback to default values based on bonus or operand if power not set
      let loss = entity.power || 0;

      // If loss wasn't pre-assigned, derive from kind via bonus field mapping
      if (loss <= 0 && entity.bonus !== undefined) {
        loss = entity.bonus;
      }

      // Default losses per spec if still unresolved
      if (loss <= 0) {
        // Infer from entity id or use defaults: sandbag=2, barbedWire=3, mine=5
        if (entity.id.includes('sandbag')) loss = 2;
        else if (entity.id.includes('barbedWire') || entity.id.includes('wire')) loss = 3;
        else if (entity.id.includes('mine')) loss = 5;
        else loss = 2; // safe default
      }

      state.player.soldiers -= loss;
      if (state.player.soldiers < 0) {
        state.player.soldiers = 0;
      }

      // Deactivate obstacle after collision (one-time hit)
      entity.active = false;

      emit({
        type: 'collision',
        data: {
          obstacleId: entity.id,
          loss: loss,
          remainingSoldiers: state.player.soldiers,
        },
      });

      state.metrics.events++;

      // Check defeat condition: soldiers reach zero
      if (state.player.soldiers <= 0) {
        state.phase = 'DEFEAT';
        emit({
          type: 'defeat',
          data: {
            reason: 'soldiers_depleted',
            level: state.level,
            simTime: state.simTime,
          },
        });
        state.metrics.events++;
        // Stop processing further collisions once defeated
        return;
      }
    }
  }
}

/** Normal enemies remove one living soldier once when they contact the squad. */
export function checkEnemyCollisions(state: GameState, emit: EventSink): void {
  if (state.phase !== 'PLAYING') return;
  for (const e of state.entities) {
    if (!e.active || e.kind !== 'enemy') continue;
    if (Math.hypot(e.x-state.player.x, e.z-state.player.z) > 1.2) continue;
    e.active=false;e.hp=0;e.animation='death';state.player.soldiers=Math.max(0,state.player.soldiers-1);state.metrics.enemiesDefeated++;
    emit({type:'death',data:{entityId:e.id,kind:'enemy',source:'contact',x:e.x,z:e.z}});
    emit({type:'collision',data:{enemyId:e.id,loss:1,remainingSoldiers:state.player.soldiers,x:e.x,z:e.z}});
    if(state.player.soldiers===0){state.phase='DEFEAT';emit({type:'defeat',data:{reason:'enemy_contact'}});return;}
  }
}
