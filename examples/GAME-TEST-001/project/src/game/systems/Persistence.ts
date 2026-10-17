// src/game/systems/Persistence.ts
// Feature f06-economy: localStorage persistence with try/catch.

import type { SaveData } from '../types';

const STORAGE_KEY = 'digital_gate_gunner_save';

export class Persistence {
  save(data: SaveData): void {
    try {
      const serialized = JSON.stringify(data);
      localStorage.setItem(STORAGE_KEY, serialized);
    } catch (_e) {
      // Silently fail if localStorage is unavailable or quota exceeded
    }
  }

  load(): SaveData | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (this.isValidSaveData(parsed)) {
        return parsed as SaveData;
      }
      return null;
    } catch (_e) {
      // Silently fail on parse errors or storage access issues
      return null;
    }
  }

  private isValidSaveData(obj: unknown): obj is SaveData {
    if (!obj || typeof obj !== 'object') return false;
    const d = obj as Record<string, unknown>;
    if (typeof d.gold !== 'number' || !Number.isFinite(d.gold)) return false;
    if (typeof d.unlockedLevel !== 'number' || !Number.isFinite(d.unlockedLevel)) return false;
    if (!Array.isArray(d.completedLevels)) return false;
    for (const cl of d.completedLevels) {
      if (typeof cl !== 'number' || !Number.isFinite(cl)) return false;
    }
    if (!d.upgrades || typeof d.upgrades !== 'object' || Array.isArray(d.upgrades)) return false;
    const upgrades = d.upgrades as Record<string, unknown>;
    for (const key of Object.keys(upgrades)) {
      if (typeof upgrades[key] !== 'number' || !Number.isFinite(upgrades[key])) return false;
    }
    if (!d.audio || typeof d.audio !== 'object' || Array.isArray(d.audio)) return false;
    const audio = d.audio as Record<string, unknown>;
    if (typeof audio.music !== 'boolean') return false;
    if (typeof audio.sfx !== 'boolean') return false;
    return true;
  }
}
