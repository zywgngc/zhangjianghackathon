// src/game/save.ts - localStorage persistence manager
// Owner: system-team (f02-prng-save)

import type { SimulationSave, ProgressState } from './types';

const SAVE_KEY = 'power_hunter_save_v1';

/**
 * SaveManager handles reading and writing persistent game data to localStorage.
 * Only saves unlocked levels, completed levels, upgrades, and audio preferences.
 * Temporary state (current level progress, timers, positions) is never persisted.
 * All localStorage access is wrapped in try/catch for safety.
 */
export class SaveManager {
  /**
   * Load saved game data from localStorage.
   * Returns null if no save exists or if loading fails.
   */
  static load(): SimulationSave | null {
    try {
      const raw = localStorage.getItem(SAVE_KEY);
      if (!raw) return null;

      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;

      // Validate and sanitize the loaded data
      const save: SimulationSave = {};

      if (parsed.progress && typeof parsed.progress === 'object') {
        const p = parsed.progress as Partial<ProgressState>;
        const progress: Partial<ProgressState> = {};

        if (typeof p.unlockedLevel === 'number' && Number.isFinite(p.unlockedLevel)) {
          progress.unlockedLevel = Math.max(1, Math.floor(p.unlockedLevel));
        }

        if (Array.isArray(p.completedLevels)) {
          progress.completedLevels = p.completedLevels.filter(
            (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && Number.isInteger(v)
          );
        }

        if (p.upgrades && typeof p.upgrades === 'object' && !Array.isArray(p.upgrades)) {
          const upgrades: Record<string, number> = {};
          for (const [key, val] of Object.entries(p.upgrades)) {
            if (typeof val === 'number' && Number.isFinite(val)) {
              upgrades[key] = val;
            }
          }
          progress.upgrades = upgrades;
        }

        if (p.audio && typeof p.audio === 'object') {
          progress.audio = {
            music: typeof p.audio.music === 'boolean' ? p.audio.music : true,
            sfx: typeof p.audio.sfx === 'boolean' ? p.audio.sfx : true,
          };
        }

        if (typeof p.gold === 'number' && Number.isFinite(p.gold)) {
          progress.gold = Math.max(0, Math.floor(p.gold));
        }

        save.progress = progress;
      }

      return save;
    } catch {
      // localStorage may be unavailable (private browsing, quota exceeded, etc.)
      return null;
    }
  }

  /**
   * Save game data to localStorage.
   * Only persists progress-related fields; ignores temporary state.
   */
  static save(data: SimulationSave): void {
    try {
      if (!data || typeof data !== 'object') return;

      // Build a clean save object with only persistent fields
      const toSave: SimulationSave = {};

      if (data.progress) {
        const p = data.progress;
        const progress: Partial<ProgressState> = {};

        if (typeof p.unlockedLevel === 'number') {
          progress.unlockedLevel = p.unlockedLevel;
        }
        if (Array.isArray(p.completedLevels)) {
          progress.completedLevels = [...p.completedLevels];
        }
        if (p.upgrades && typeof p.upgrades === 'object') {
          progress.upgrades = { ...p.upgrades };
        }
        if (p.audio) {
          progress.audio = {
            music: typeof p.audio.music === 'boolean' ? p.audio.music : true,
            sfx: typeof p.audio.sfx === 'boolean' ? p.audio.sfx : true,
          };
        }
        if (typeof p.gold === 'number') {
          progress.gold = p.gold;
        }

        toSave.progress = progress;
      }

      const serialized = JSON.stringify(toSave);
      localStorage.setItem(SAVE_KEY, serialized);
    } catch {
      // Silently fail if localStorage is unavailable or quota exceeded
    }
  }

  /**
   * Clear all saved game data from localStorage.
   */
  static clear(): void {
    try {
      localStorage.removeItem(SAVE_KEY);
    } catch {
      // Silently fail if localStorage is unavailable
    }
  }
}
