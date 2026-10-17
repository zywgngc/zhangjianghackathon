import type { GameState, GameConfig, EventSink } from '../types';
export function spawnBoss(state: GameState,hp:number,z:number):void {
  state.entities=state.entities.filter(e=>e.kind!=='boss');state.entities.push({id:`boss_${state.level}`,kind:'boss',x:0,y:0,z,hp,maxHp:hp,power:0,active:true});
}
export function damageBoss(state:GameState,damage:number,emit:EventSink):void {
  const boss=state.entities.find(e=>e.kind==='boss'&&e.active);if(!boss||damage<=0)return;
  boss.hp=Math.max(0,boss.hp-damage);state.metrics.totalDamage+=damage;
  emit({seq:0,simTime:state.simTime,type:'damage',data:{targetId:boss.id,amount:damage,hp:boss.hp,skillId:'rifle'}});
  if(boss.hp<=0){boss.active=false;state.metrics.enemiesDefeated++;emit({seq:0,simTime:state.simTime,type:'death',data:{entityId:boss.id,kind:'boss',source:'projectile'}});}
}
export function isBossDefeated(state:GameState):boolean {const bosses=state.entities.filter(e=>e.kind==='boss');return bosses.length>0&&bosses.every(b=>b.hp<=0);}
/** Road-end loss depends on squad position, never on a self-moving boss timer. */
export function updateBoss(state:GameState,_dt:number,config:GameConfig,emit:EventSink):void {
  if(state.phase!=='PLAYING')return;
  const level=config.levels.find(l=>l.level===state.level);
  for(const entity of state.entities){
    if(entity.kind==='boss'&&!entity.active&&entity.hp>0&&entity.activationZ!==undefined&&state.player.z>=entity.activationZ){
      entity.active=true;emit({seq:0,simTime:state.simTime,type:'spawn',data:{id:entity.id,kind:'boss',hp:entity.hp,maxHp:entity.maxHp,x:entity.x,y:entity.y,z:entity.z}});
    }
  }
  const boss=state.entities.find(e=>e.kind==='boss'&&e.active&&e.hp>0);
  if(level&&boss&&state.player.z>=level.trackLength){state.phase='DEFEAT';emit({seq:0,simTime:state.simTime,type:'defeat',data:{reason:'boss_alive_at_road_end',level:state.level}});}
}
