import type { GameState, GameConfig } from '../types';

/** Same +Z production coordinates in the renderer, authored levels and fixtures. */
export function updateNavigation(state: GameState, dt: number, inputX: number | null, config: GameConfig): void {
  if (state.phase !== 'PLAYING') return;
  state.player.z += config.forwardSpeed * dt;
  if (inputX !== null) {
    const target = Math.max(-config.clampX, Math.min(config.clampX, inputX));
    const desired = (target - state.player.x) * (1 - Math.exp(-dt / Math.max(config.smoothing, 0.001)));
    const step = config.lateralSpeed * dt;
    state.player.x += Math.max(-step, Math.min(step, desired));
  }
  state.player.x = Math.max(-config.clampX, Math.min(config.clampX, state.player.x));
}
