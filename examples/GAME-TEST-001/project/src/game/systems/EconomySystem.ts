import type { GameState, InputCommand, GameConfig, EventSink } from '../types';
import { unlockNextLevel } from './LevelManager';
const rewarded = new WeakSet<GameState>();
export function canonicalUpgrade(id: string): string | null {
  if (['soldiers','soldier','initialSoldier','initialSoldiers'].includes(id)) return 'soldiers';
  if (['damage','weaponDamage'].includes(id)) return 'damage';
  return null;
}
export function calculateUpgradeCost(currentLevel: number): number { return 100*(currentLevel+1); }
export function updateEconomy(state: GameState, cmd: InputCommand | null, config: GameConfig, emit: EventSink): void {
  if(cmd?.type==='purchase'){
    const id=canonicalUpgrade(cmd.id??'');
    if(!id||!config.upgrades.types.some(t=>t.id===id)||!['MENU','UPGRADE','UPGRADES','LEVEL_UP'].includes(state.phase))return;
    const level=state.progress.upgrades[id]??0,cost=calculateUpgradeCost(level);
    if(level>=config.upgrades.maxLevel||state.progress.gold<cost)return;
    state.progress.gold-=cost;state.progress.upgrades[id]=level+1;
    emit({seq:0,simTime:state.simTime,type:'upgrade',data:{kind:'purchase_success',upgradeId:id,newLevel:level+1,cost,remainingGold:state.progress.gold}});
    return;
  }
  if(state.phase!=='VICTORY'||rewarded.has(state))return;
  const level=config.levels.find(l=>l.level===state.level);if(!level)return;
  rewarded.add(state);state.progress.gold+=level.reward;
  if(!state.progress.completedLevels.includes(state.level))state.progress.completedLevels.push(state.level);
  unlockNextLevel(state);
  emit({seq:0,simTime:state.simTime,type:'upgrade',data:{kind:'gold_reward',amount:level.reward,totalGold:state.progress.gold}});
}
