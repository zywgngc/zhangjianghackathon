import type { GameState, GameConfig, EventSink } from '../types';
import { recalculateFormation } from './FormationSystem';
const nextIds = new WeakMap<GameState, number>();
export function addSoldiers(state: GameState, n: number): void { if(Number.isFinite(n)&&n>0) state.player.soldiers=Math.min(200,state.player.soldiers+Math.floor(n)); }
export function removeSoldiers(state: GameState, n: number): void { if(Number.isFinite(n)&&n>0) state.player.soldiers=Math.max(0,state.player.soldiers-Math.floor(n)); }
export function updateEvolution(state: GameState, _dt: number, config: GameConfig, emit: EventSink): void {
  state.player.soldiers=Math.max(0,Math.min(config.maxSoldiers,Math.floor(state.player.soldiers)));
  const count=state.player.soldiers, existing=state.entities.filter(e=>e.kind==='soldier'&&e.active);
  let next=nextIds.get(state)??1;
  while(existing.length<count){
    const entity={id:`soldier_${next++}`,kind:'soldier' as const,x:state.player.x,y:0,z:state.player.z,hp:1,maxHp:1,power:1,active:true,animation:'spawn'};
    existing.push(entity);state.entities.push(entity);
  }
  nextIds.set(state,next);
  for(let i=count;i<existing.length;i++){existing[i].active=false;existing[i].animation='death';}
  const positions=recalculateFormation(count);
  for(let i=0;i<count;i++){const e=existing[i],p=positions[i];e.x=state.player.x+p.x;e.y=state.player.y+p.y;e.z=state.player.z+p.z;if(e.animation!=='spawn')e.animation='run';}
  state.player.hp=count;state.player.maxHp=Math.max(state.player.maxHp,count);state.player.power=count;
  state.skills.effectiveStats.maxHp=state.player.maxHp;
  state.metrics.enemies=state.entities.filter(e=>e.active&&(e.kind==='enemy'||e.kind==='boss')).length;
  if(count===0&&state.phase==='PLAYING'){state.phase='DEFEAT';emit({seq:0,simTime:state.simTime,type:'defeat',data:{reason:'no_soldiers'}});}
}
