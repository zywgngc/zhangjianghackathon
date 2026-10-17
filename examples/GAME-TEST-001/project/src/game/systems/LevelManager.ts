import type { GameState, GameConfig, SaveData, EventSink, Entity } from '../types';
import { GAME_CONSTANTS } from '../config';

export function loadLevel(state: GameState, levelId: number, config: GameConfig, save: SaveData | null): void {
  const level = config.levels.find(l => l.level === levelId);
  if (!level) return;
  state.phase = 'PLAYING'; state.level = levelId; state.simTime = 0;
  if (save) state.progress = { ...save, completedLevels: [...save.completedLevels], upgrades: { ...save.upgrades }, audio: { ...save.audio } };
  const count = Math.min(config.maxSoldiers, config.initialSoldiers + (state.progress.upgrades.soldiers ?? 0));
  state.player = { x: 0, y: GAME_CONSTANTS.PLAYER_Y, z: 0, hp: count, maxHp: count, power: count, soldiers: count };
  state.skills.effectiveStats.bulletDamage = config.weapon.bulletDamage + (state.progress.upgrades.damage ?? 0);
  state.entities.length = 0;
  let index = 0;
  for (let groupId=0; groupId<level.gateGroups.length; groupId++) {
    const group = level.gateGroups[groupId];
    const width = config.roadWidth / group.gates.length;
    for (let lane=0; lane<group.gates.length; lane++) {
      const gate = group.gates[lane];
      state.entities.push({ id: `gate_${levelId}_${index++}`, kind: 'gate', x: -config.roadWidth/2 + width*(lane+.5),
        y: 0, z: group.z, hp: 1, power: 0, active: true, consumed: false, group: groupId, operator: gate.operator, operand: gate.operand });
    }
  }
  for (const [i,o] of level.obstacles.entries()) state.entities.push({ id: `obstacle_${levelId}_${i}`, kind: 'obstacle', archetype: o.kind,
    obstacleType: o.kind, x: o.x, y: 0, z: o.z, hp: 1, power: 0, active: true, bonus: o.loss });
  for (const e of level.enemies) state.entities.push({ ...e, kind: 'enemy', y: 0, hp: 10, maxHp: 10, power: 0, active: true });
  const boss: Entity = { id: `boss_${levelId}`, kind: 'boss', x: 0, y: 0, z: level.trackLength + GAME_CONSTANTS.BOSS_Z_OFFSET,
    hp: level.bossHp, maxHp: level.bossHp, power: 0, active: false, activationZ: level.bossActivationZ };
  state.entities.push(boss);
  Object.assign(state.metrics, { updateLoops: 1, ticks: 0, projectiles: 0, enemies: level.enemies.length+1,
    events: 0, shotsFired: 0, totalDamage: 0, enemiesDefeated: 0, skillAttacks: {} });
}

/** Terminal state only: EconomySystem owns the single actual reward transaction. */
export function checkLevelCompletion(state: GameState, config: GameConfig, emit: EventSink): void {
  if (state.phase !== 'PLAYING') return;
  const level = config.levels.find(l => l.level === state.level);
  if (!level) return;
  const bosses = state.entities.filter(e => e.kind === 'boss');
  let reason = '';
  if (state.player.soldiers <= 0) reason = 'no_soldiers';
  else if (bosses.length && bosses.every(b => b.hp <= 0)) {
    state.phase = 'VICTORY';
    emit({ seq: 0, simTime: state.simTime, type: 'victory', data: { level: state.level, reason: 'boss_defeated', reward: level.reward } });
    return;
  } else if (state.player.z >= level.trackLength) reason = bosses.some(b => b.active && b.hp > 0) ? 'boss_alive_at_road_end' : 'road_end_without_boss_defeat';
  if (reason) { state.phase = 'DEFEAT'; emit({seq:0,simTime:state.simTime,type:'defeat',data:{level:state.level,reason}}); }
}
export function unlockNextLevel(state: GameState): void { state.progress.unlockedLevel = Math.max(state.progress.unlockedLevel, Math.min(3, state.level+1)); }
