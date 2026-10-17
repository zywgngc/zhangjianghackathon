import { create } from "zustand";
import type {
  CharacterType,
  MapType,
  CharacterData,
  GameMap,
  WeaponType,
} from "../types";
import { useSaveStore } from "./save";
import { useShallow } from "zustand/react/shallow";
import { CHARACTERS_DATA } from "../constant";
import { MAPS } from "../constant/map";

type States = {
  selectedCharacterId: CharacterType;
  selectedMapId: MapType;
  unlockedCharacterIds: CharacterType[];
  unlockedMapIds: MapType[];
  ownedWeapons: WeaponType[];
};

type Actions = {
  selectCharacter: (characterId: CharacterType) => void;
  selectMap: (mapId: MapType) => void;
  getSelectCharacter: () => CharacterData;
  getSelectMap: () => GameMap;
  checkUnlocks: () => void;
  addWeapon: (weaponId: WeaponType) => void;
  resetAll: () => void;
};

type Store = States & Actions;

const checkUnlocks = (): Partial<States> => {
  const state = useSaveStore.getState();
  const { completedChapters } = state;

  const unlockedMapIds: MapType[] = [];
  const unlockedCharacterIds: CharacterType[] = [];

  const chapters = completedChapters.map((c) => {
    const item = MAPS.find((m) => m.id === c);
    return item?.chapter ?? 0;
  });

  // Check map unlock
  MAPS.forEach((map) => {
    if (!unlockedMapIds.includes(map.id)) {
      let unlocked = false;

      switch (map.unlockCondition.type) {
        case "default":
          unlocked = true;
          break;
        case "chapter":
          unlocked = chapters.includes(map.unlockCondition.value);
          break;
      }

      if (unlocked) {
        unlockedMapIds.push(map.id);
      }
    }
  });

  // Check character unlock
  Object.values(CHARACTERS_DATA).forEach((char) => {
    if (!unlockedCharacterIds.includes(char.id)) {
      let unlocked = false;

      switch (char.unlockCondition.type) {
        case "default":
          unlocked = true;
          break;
        case "kills":
          unlocked = state.totalKills >= char.unlockCondition.value;
          break;
        case "time":
          unlocked = state.bestSurvivalTime >= char.unlockCondition.value;
          break;
        case "gold":
          unlocked = state.totalGold >= char.unlockCondition.value;
          break;
        case "chapter": {
          unlocked = chapters.includes(char.unlockCondition.value);
          break;
        }
      }

      if (unlocked) {
        unlockedCharacterIds.push(char.id);
      }
    }
  });

  return {
    unlockedCharacterIds,
    unlockedMapIds,
  };
};

export const useAppStore = create<Store>((set, get) => {
  return {
    selectedCharacterId: "destined_one",
    selectedMapId: "chapter1",
    unlockedCharacterIds: [],
    unlockedMapIds: [],
    ownedWeapons: [],
    resetAll() {
      set({
        selectedCharacterId: "destined_one",
        selectedMapId: "chapter1",
        unlockedCharacterIds: [],
        unlockedMapIds: [],
        ownedWeapons: [],
      });
    },
    addWeapon: (weaponId) => {
      const { ownedWeapons } = get();

      if (!ownedWeapons.includes(weaponId)) {
        set({ ownedWeapons: [...ownedWeapons, weaponId] });
      }
    },
    selectCharacter: (characterId: CharacterType) => {
      const { unlockedCharacterIds } = get();
      if (unlockedCharacterIds.includes(characterId)) {
        set({ selectedCharacterId: characterId });
      }
    },

    selectMap: (mapId: MapType) => {
      const { unlockedMapIds } = get();
      if (unlockedMapIds.includes(mapId)) {
        set({ selectedMapId: mapId });
      }
    },
    getSelectCharacter() {
      const { selectedCharacterId: selectedCharacter } = get();

      return (
        CHARACTERS_DATA[selectedCharacter] || CHARACTERS_DATA["destined_one"]
      );
    },
    getSelectMap() {
      const { selectedMapId: selectedMap } = get();

      return MAPS.find((map) => map.id === selectedMap) || MAPS[0];
    },

    checkUnlocks: () => {
      set(checkUnlocks());
    },
  };
});

const getSelectedCharacter = (state: Store) => state.selectedCharacterId;
const getSelectedMap = (state: Store) => state.selectedMapId;
const getUnlockedCharacters = (state: Store) => state.unlockedCharacterIds;
const getUnlockedMaps = (state: Store) => state.unlockedMapIds;

export const useSelectedCharacter = () =>
  useAppStore(useShallow(getSelectedCharacter));
export const useSelectedMap = () => useAppStore(useShallow(getSelectedMap));
export const useUnlockedCharacters = () =>
  useAppStore(useShallow(getUnlockedCharacters));
export const useUnlockedMaps = () => useAppStore(useShallow(getUnlockedMaps));
