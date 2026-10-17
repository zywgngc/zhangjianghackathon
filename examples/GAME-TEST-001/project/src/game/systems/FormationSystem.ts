import type { Vec3 } from '../types';

const SOLDIER_SPACING = 0.8;
const MAX_COLS = 15;

/**
 * Recalculate formation positions for a given number of soldiers.
 * Arranges soldiers in rows and columns centered around x=0, z=0.
 * Optimized for up to 200 soldiers.
 */
export function recalculateFormation(count: number): Vec3[] {
  if (count <= 0) return [];

  const clamped = Math.min(Math.max(0, count), 200);
  const cols = Math.min(clamped, MAX_COLS);
  const rows = Math.ceil(clamped / cols);

  const positions: Vec3[] = new Array(clamped);
  let idx = 0;

  for (let r = 0; r < rows && idx < clamped; r++) {
    const soldiersInRow = Math.min(cols, clamped - idx);
    const offsetX = (soldiersInRow - 1) * SOLDIER_SPACING * 0.5;
    const offsetZ = r * SOLDIER_SPACING;

    for (let c = 0; c < soldiersInRow && idx < clamped; c++) {
      positions[idx] = {
        x: c * SOLDIER_SPACING - offsetX,
        y: 0,
        z: -offsetZ,
      };
      idx++;
    }
  }

  return positions;
}
