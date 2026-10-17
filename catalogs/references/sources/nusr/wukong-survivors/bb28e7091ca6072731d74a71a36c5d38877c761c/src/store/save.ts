import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { DEFAULT_SAVE, PERMANENT_UPGRADES } from "../constant";
import type { GameSave, PermanentUpgradeType, MapType } from "../types";
import { useShallow } from "zustand/react/shallow";
import { useAppStore } from "./app";

const SAVE_KEY = "wu_kong_survivors_save";

// Zustand Store interface
interface SaveStore extends GameSave {
  // Actions
  addGold: (amount: number) => void;
  spendGold: (amount: number) => boolean;
  addKills: (amount: number) => void;
  updatePlayTime: (seconds: number) => void;
  upgradePermanent: (upgradeId: PermanentUpgradeType) => boolean;
  resetPermanentUpgrades: () => void;
  resetAll: () => void;
  completeChapter: (map: MapType[]) => void;
}

// Create Zustand Store with persist middleware
export const useSaveStore = create<SaveStore>()(
  persist(
    (set, get) => ({
      ...DEFAULT_SAVE,

      // Actions
      addGold: (amount: number) => {
        set({ totalGold: get().totalGold + amount });
        useAppStore.getState().checkUnlocks();
      },

      spendGold: (amount: number) => {
        const state = get();
        if (state.totalGold >= amount) {
          set({ totalGold: state.totalGold - amount });
          return true;
        }
        return false;
      },

      addKills: (amount: number) => {
        set({
          totalKills: get().totalKills + amount,
        });
        useAppStore.getState().checkUnlocks();
      },

      updatePlayTime: (seconds: number) => {
        seconds = Math.floor(seconds);

        const state = get();

        const newPlayTime = state.totalPlayTime + seconds;
        const newBestTime =
          seconds > state.bestSurvivalTime ? seconds : state.bestSurvivalTime;
        set({
          totalPlayTime: newPlayTime,
          bestSurvivalTime: newBestTime,
        });
        useAppStore.getState().checkUnlocks();
      },

      upgradePermanent: (upgradeId: PermanentUpgradeType) => {
        const state = get();
        const upgrade = PERMANENT_UPGRADES.find((u) => u.id === upgradeId);
        if (!upgrade) return false;

        const currentLevel = state[upgradeId] || 0;
        if (currentLevel >= upgrade.maxLevel) return false;

        const cost = upgrade.cost(currentLevel);
        if (state.totalGold >= cost) {
          set({
            totalGold: state.totalGold - cost,
            [upgradeId]: currentLevel + 1,
          });
          return true;
        }
        return false;
      },

      resetPermanentUpgrades: () => {
        const state = get();
        let refund = 0;

        PERMANENT_UPGRADES.forEach((item) => {
          const level = state[item.id] || 0;
          for (let i = 0; i < level; i++) {
            refund += item.cost(i);
          }
        });

        refund = Math.floor(refund);

        // Reset all levels
        const resetUpgrades: Partial<Record<PermanentUpgradeType, number>> = {};
        PERMANENT_UPGRADES.forEach((item) => {
          resetUpgrades[item.id] = 0;
        });

        set({
          ...resetUpgrades,
          totalGold: state.totalGold + refund,
        });
      },

      resetAll: () => set({ ...DEFAULT_SAVE }),

      // Complete chapter and unlock corresponding characters
      completeChapter: (chapters) => {
        const { completedChapters = [] } = get();

        const list = Array.from(new Set([...completedChapters, ...chapters]));

        set({ completedChapters: list });

        useAppStore.getState().checkUnlocks();
      },
    }),
    {
      name: SAVE_KEY,
      merge: (persistedState, currentState) => ({
        ...DEFAULT_SAVE,
        ...currentState,
        ...(persistedState as Partial<GameSave>),
      }),
      storage: createJSONStorage(() => localStorage),
    },
  ),
);

const getTotalGold = (state: GameSave) => state.totalGold;
const getTotalKills = (state: GameSave) => state.totalKills;
const getBestSurvivalTime = (state: GameSave) => state.bestSurvivalTime;
const getTotalPlayTime = (state: GameSave) => state.totalPlayTime;

const getCompletedChapters = (state: GameSave) => state.completedChapters;

const getShopLevel = (
  state: GameSave,
): Record<PermanentUpgradeType, number> => ({
  attack: state.attack,
  health: state.health,
  armor: state.armor,
  speed: state.speed,
  luck: state.luck,
  expBonus: state.expBonus,
  critRate: state.critRate,
  magnetBonus: state.magnetBonus,
  collectRange: state.collectRange,
});

export const useTotalGold = () => useSaveStore(useShallow(getTotalGold));
export const useTotalKills = () => useSaveStore(useShallow(getTotalKills));
export const useBestSurvivalTime = () =>
  useSaveStore(useShallow(getBestSurvivalTime));
export const useTotalPlayTime = () =>
  useSaveStore(useShallow(getTotalPlayTime));

export const useCompletedChapters = () =>
  useSaveStore(useShallow(getCompletedChapters));

export const useShopLevel = () => useSaveStore(useShallow(getShopLevel));
