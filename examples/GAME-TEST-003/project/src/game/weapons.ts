// src/game/weapons.ts - Weapon collection and economy system
// Owner: economy-team (f06-weapons)

import type { GameState, GameConfig, GameEvent } from './types';
import { PRNG } from './prng';

const WEAPON_KIND = 'weapon';

/**
 * Updates weapon pickup logic each simulation tick.
 * Checks distance between player and active weapon entities,
 * applies power bonus on pickup, marks entity inactive, and emits event.
 *
 * @param state - Mutable game state
 * @param dt - Delta time in seconds (unused for instant pickup but kept for interface consistency)
 * @param config - Game configuration containing weaponPickupRange
 * @param rng - Deterministic PRNG instance
 * @param emit - Event emitter function
 */
export function updateWeapons(
  state: GameState,
  dt: number,
  config: GameConfig,
  rng: PRNG,
  emit: (e: GameEvent) => void
): void {
  if (state.phase !== 'PLAYING') {
    return;
  }

  const pickupRange = config.weaponPickupRange;
  const pickupRangeSq = pickupRange * pickupRange;

  const px = state.player.x;
  const py = state.player.y;
  const pz = state.player.z;

  for (let i = 0; i < state.entities.length; i++) {
    const entity = state.entities[i];

    // Only process active weapons
    if (!entity.active || entity.kind !== WEAPON_KIND) {
      continue;
    }

    // Calculate squared distance (3D)
    const dx = px - entity.x;
    const dy = py - entity.y;
    const dz = pz - entity.z;
    const distSq = dx * dx + dy * dy + dz * dz;

    // Check if within pickup range
    if (distSq <= pickupRangeSq) {
      // Apply weapon bonus to player power
      const bonus = entity.bonus ?? 0;
      state.player.power += bonus;

      // Clamp power to configured bounds
      if (state.player.power > config.powerMax) {
        state.player.power = config.powerMax;
      }
      if (state.player.power < config.powerMin) {
        state.player.power = config.powerMin;
      }

      // Mark weapon as collected (single collection limit)
      entity.active = false;

      // Emit pickup event
      state.metrics.events++;
      emit({
        seq: state.metrics.events,
        simTime: state.simTime,
        type: 'WEAPON_PICKUP',
        data: {
          entityId: entity.id,
          weaponName: entity.group ?? '',
          bonus: bonus,
          newPower: state.player.power,
        },
      });
    }
  }
}
