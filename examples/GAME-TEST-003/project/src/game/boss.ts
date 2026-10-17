import type { GameState, GameConfig, GameEvent, Entity } from './types';
import type { PRNG } from './prng';

interface Lifecycle { seenAlive:Set<string>; defeated:Set<string>; absorbed:Set<string>; }
const worlds=new WeakMap<GameState,Lifecycle>();
const isBoss=(entity:Entity):boolean=>entity.kind==='boss'||entity.group==='boss';

/** A new level/isolated world owns its own real encounter history. */
export function clearBossLifecycle(state:GameState):void {worlds.delete(state);}
/** Capture the actual initial scene after level/fixture population, before stepping. */
export function initializeBossLifecycle(state:GameState):void {
  worlds.set(state,{seenAlive:new Set(state.entities.filter(entity=>isBoss(entity)&&entity.active&&entity.hp>0).map(entity=>entity.id)),
    defeated:new Set(),absorbed:new Set()});
}

/**
 * Observe actual Boss lifecycle, never award progress or change the phase.
 * Combat owns strict power comparison, .6s attack, defeat and .5s absorption.
 * Simulation alone owns finishVictory/unlock/save after this returns true.
 */
export function updateBoss(
  state:GameState,_dt:number,_config:GameConfig,_rng:PRNG,emit:(event:GameEvent)=>void
):boolean {
  if(state.phase!=='PLAYING')return false;
  let lifecycle=worlds.get(state);
  if(!lifecycle){lifecycle={seenAlive:new Set(),defeated:new Set(),absorbed:new Set()};worlds.set(state,lifecycle);}
  const bosses=state.entities.filter(isBoss);
  if(!bosses.length)return false;
  for(const boss of bosses){
    if(boss.active&&boss.hp>0)lifecycle.seenAlive.add(boss.id);
    if(!boss.active&&boss.hp<=0&&lifecycle.seenAlive.has(boss.id))lifecycle.defeated.add(boss.id);
  }
  // Never finish from an empty scene, a pre-defeated fixture, or during the
  // real absorption lease. The power gain is applied before this lease ends.
  if(state.interactionLock||state.attackTimer>0||state.absorptionTimer>0||state.pendingAbsorption)return false;
  for(const boss of bosses){
    if(lifecycle.defeated.has(boss.id)&&!lifecycle.absorbed.has(boss.id)){
      lifecycle.absorbed.add(boss.id);
      emit({seq:0,simTime:state.simTime,type:'boss_absorption_completed',data:{bossId:boss.id,level:state.level,playerPower:state.player.power}});
    }
  }
  return bosses.every(boss=>!boss.active&&boss.hp<=0&&lifecycle.absorbed.has(boss.id));
}
