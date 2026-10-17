import type { GameState, GameConfig, InputCommand, GameEvent, SimulationOptions, FixtureData, EngineInfo, ProjectedCoord, Entity, SaveData, Phase } from './types';
import { EventLog } from './EventLog';
import { PRNG } from './prng';
import { GAME_CONFIG } from './config';
import { loadLevel, checkLevelCompletion } from './systems/LevelManager';
import { updateNavigation } from './systems/NavigationSystem';
import { updateGates } from './systems/GateSystem';
import { updateEvolution } from './systems/EvolutionSystem';
import { updateCombat, clearCombat } from './systems/CombatSystem';
import { checkObstacleCollisions, checkEnemyCollisions } from './systems/CollisionSystem';
import { updateBoss } from './systems/BossSystem';
import { updateEconomy, canonicalUpgrade } from './systems/EconomySystem';
import { Persistence } from './systems/Persistence';

export interface Snapshot {
  phase: GameState['phase']; level: number; simTime: number; player: GameState['player']; entities: GameState['entities'];
  skills: GameState['skills']; progress: GameState['progress']; metrics: GameState['metrics']; effectiveStats: GameState['skills']['effectiveStats'];
}
export interface IsolatedSim {
  snapshot(): Snapshot; config(): GameConfig; dispatch(cmd: InputCommand): void; advance(ms: number): void;
  containerId?: string;
  events(afterSeq?: number): GameEvent[]; dispose(): void; engineInfo(): EngineInfo; projectEntity(id: string): ProjectedCoord | null;
}
const FIXED_MS=1000/60;
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
const bounded=(value:unknown,min:number,max:number,fallback:number):number=>typeof value==='number'&&Number.isFinite(value)?Math.max(min,Math.min(max,value)):fallback;
function progressFrom(save?: Partial<SaveData> | null): SaveData {
  const upgrades=save?.upgrades??{};
  return {gold:Math.floor(bounded(save?.gold,0,1e12,0)),unlockedLevel:Math.floor(bounded(save?.unlockedLevel,1,3,1)),
    completedLevels:[...new Set((save?.completedLevels??[]).filter(n=>Number.isInteger(n)&&n>=1&&n<=3))],
    upgrades:{soldiers:Math.floor(bounded(upgrades.soldiers??upgrades.initialSoldiers??upgrades.initialSoldier,0,10,0)),damage:Math.floor(bounded(upgrades.damage??upgrades.weaponDamage,0,10,0))},
    audio:{music:save?.audio?.music??true,sfx:save?.audio?.sfx??true}};
}
function blank(level:number,progress:SaveData,config:GameConfig):GameState {
  return {phase:'MENU',level,simTime:0,player:{x:0,y:0,z:0,hp:1,maxHp:1,power:1,soldiers:1},entities:[],
    skills:{active:[],passive:[],ultimate:[],upgradeChoices:[],effectiveStats:{maxHp:1,damageMultiplier:1,damageReduction:0,moveSpeed:config.lateralSpeed,cooldownReduction:0,healOnKillChance:0,xpMultiplier:1,rangeMultiplier:1,bulletDamage:config.weapon.bulletDamage+(progress.upgrades.damage??0)}},
    progress:clone(progress),metrics:{updateLoops:0,ticks:0,projectiles:0,enemies:0,events:0,shotsFired:0,totalDamage:0,enemiesDefeated:0,skillAttacks:{}}};
}
export class Simulation {
  private state:GameState;
  private gameConfig:GameConfig=clone(GAME_CONFIG);
  private eventLog=new EventLog();
  private rng:PRNG;
  private seed:number;
  private accumulator=0;
  private generation=0;
  private disposed=false;
  private manualClock=false;
  private targetX:number|null=null;
  private sceneHandle:any=null;
  private persistence:Persistence|null;
  private children=new Set<Simulation>();
  private viewFactory:((sim:Simulation,opts:{ui?:boolean})=>{containerId:string;dispose:()=>void})|null=null;
  private viewCleanup:(()=>void)|null=null;
  private opts:SimulationOptions;
  constructor(opts:SimulationOptions={}) {
    this.opts=clone(opts);this.seed=opts.seed??Date.now();this.rng=new PRNG(this.seed);
    this.persistence=opts.isolated?null:new Persistence();
    const loaded=opts.save??this.persistence?.load();
    const fixture=opts.fixture;
    const saved=progressFrom({...loaded,...fixture?.progress,upgrades:{...loaded?.upgrades,...fixture?.progress?.upgrades}});
    const level=Math.floor(bounded(fixture?.level??opts.level,1,3,1));
    if(fixture?.weapons&&!Array.isArray(fixture.weapons))Object.assign(this.gameConfig.weapon,fixture.weapons);
    if(typeof fixture?.trackEnd==='number'&&Number.isFinite(fixture.trackEnd)&&fixture.trackEnd>0)this.gameConfig.levels.find(l=>l.level===level)!.trackLength=fixture.trackEnd;
    this.state=this.initialize(level,saved,opts.isolated?'PLAYING':'MENU');
    if(fixture?.phase&&['MENU','UPGRADE','UPGRADES','LEVEL_UP','PAUSED','SETTINGS'].includes(fixture.phase))this.state.phase=fixture.phase;
    this.refreshMetrics();
  }
  private initialize(level:number,progress:SaveData,phase:Phase):GameState {
    const state=blank(level,progress,this.gameConfig);loadLevel(state,level,this.gameConfig,null);
    const fixture=this.opts.fixture;
    if(fixture){
      if(typeof fixture.trackEnd==='number'&&Number.isFinite(fixture.trackEnd)&&fixture.trackEnd>0)this.gameConfig.levels.find(l=>l.level===level)!.trackLength=fixture.trackEnd;
      if(fixture.spawnEnabled===false)state.entities.length=0;
      if(fixture.enemies!==undefined){
        state.entities=state.entities.filter(e=>e.kind!=='enemy');
        for(const [i,e] of fixture.enemies.entries())state.entities.push(this.normalizedEntity(e,'enemy',`fixture_enemy_${i}`,10));
      }
      if(fixture.gates!==undefined){
        state.entities=state.entities.filter(e=>e.kind!=='gate');
        for(const [i,g] of fixture.gates.entries()){
          if('gates' in g){
            for(const [lane,gate] of g.gates.entries())state.entities.push(this.normalizedEntity({...gate,x:-this.gameConfig.roadWidth/2+this.gameConfig.roadWidth/g.gates.length*(lane+.5),z:g.z,group:`fixture_group_${i}`},'gate',`fixture_gate_${i}_${lane}`,1));
          }else state.entities.push(this.normalizedEntity(g,'gate',`fixture_gate_${i}`,1));
        }
      }
      if(fixture.obstacles!==undefined){
        state.entities=state.entities.filter(e=>e.kind!=='obstacle');
        for(const [i,o] of fixture.obstacles.entries()){
          const raw=o as Record<string,any>,kind=raw.archetype??raw.obstacleType??raw.kind??'sandbag';
          const loss=raw.loss??raw.bonus??this.gameConfig.obstacleLoss[kind as keyof GameConfig['obstacleLoss']]??2;
          state.entities.push(this.normalizedEntity({...raw,archetype:kind,obstacleType:kind,bonus:loss},'obstacle',`fixture_obstacle_${i}`,1));
        }
      }
      if(fixture.boss!==undefined){
        state.entities=state.entities.filter(e=>e.kind!=='boss');
        if(fixture.boss!==null)state.entities.push(this.normalizedEntity(fixture.boss,'boss','fixture_boss',this.gameConfig.levels.find(l=>l.level===level)!.bossHp));
      }
      Object.assign(state.player,fixture.player??{});
      state.player.soldiers=Math.floor(bounded(state.player.soldiers,0,this.gameConfig.maxSoldiers,1));
      if(fixture.skills){
        state.skills={...state.skills,...clone(fixture.skills),effectiveStats:{...state.skills.effectiveStats,...fixture.skills.effectiveStats}};
      }
    }
    state.phase=phase;updateEvolution(state,0,this.gameConfig,()=>{});
    state.metrics.updateLoops=phase==='PLAYING'?1:0;
    return state;
  }
  private normalizedEntity(raw:Record<string,any>,kind:Entity['kind'],id:string,hp:number):Entity {
    const op=raw.operator==='*'?'×':raw.operator==='/'?'÷':raw.operator;
    return {...clone(raw),id:typeof raw.id==='string'?raw.id:id,kind,archetype:raw.archetype??(raw.kind!==kind?raw.kind:undefined),
      x:bounded(raw.x,-1e6,1e6,0),y:bounded(raw.y,-1e6,1e6,0),z:bounded(raw.z,-1e6,1e6,0),hp:bounded(raw.hp,0,1e12,hp),maxHp:bounded(raw.maxHp,0,1e12,raw.hp??hp),
      power:bounded(raw.power,0,1e12,0),active:raw.active!==false,operator:op,consumed:raw.consumed===true};
  }
  public setSceneHandle(handle:any):void {if(handle===null&&this.disposed)return;this.sceneHandle=handle;}
  public setViewFactory(factory:((sim:Simulation,opts:{ui?:boolean})=>{containerId:string;dispose:()=>void})|null):void {this.viewFactory=factory;}
  /** Internal renderer/UI borrow of this exact state; never exposed through the host bridge. */
  public getRenderState():Snapshot {this.refreshMetrics();return {...this.state,effectiveStats:this.state.skills.effectiveStats};}
  public eventCursor():{generation:number;sequence:number} {return {generation:this.generation,sequence:this.eventLog.count};}
  public snapshot():Snapshot {
    this.refreshMetrics();const s=clone(this.state);
    // Input/save aliases refer to the same two actual canonical upgrades, never extra upgrade types.
    s.progress.upgrades.initialSoldiers=s.progress.upgrades.soldiers??0;s.progress.upgrades.weaponDamage=s.progress.upgrades.damage??0;
    return {...s,effectiveStats:clone(s.skills.effectiveStats)};
  }
  public config():GameConfig {
    const config=clone(this.gameConfig);
    // Public names resolve to the very same two production attributes and level definitions.
    config.permanentUpgrades=config.upgrades.types.map(t=>({id:({soldiers:'initialSoldiers',damage:'weaponDamage'} as Record<string,string>)[t.id]??t.id,canonicalId:t.id,name:t.name,maxLevel:config.upgrades.maxLevel,baseCost:config.upgrades.baseCost}));
    config.enemies=config.levels.flatMap(level=>level.enemies.map(enemy=>({...enemy,kind:'enemy' as const,level:level.level})));
    return config;
  }
  public dispatch(cmd:InputCommand):void {
    if(this.disposed||!cmd||typeof cmd.type!=='string')return;
    if(cmd.type==='start'||cmd.type==='restart'){
      const level=cmd.type==='restart'?this.state.level:(cmd.level??this.state.level);
      if(!Number.isInteger(level)||level<1||level>3||level>this.state.progress.unlockedLevel)return;
      const saved=progressFrom(this.state.progress);clearCombat(this.state);
      if(typeof cmd.seed==='number'&&Number.isFinite(cmd.seed))this.seed=cmd.seed;
      this.rng=new PRNG(this.seed);this.accumulator=0;this.targetX=null;this.eventLog.clear();this.generation++;
      this.state=this.initialize(level,saved,'PLAYING');
      this.emit('navigation',{action:cmd.type,level});
      for(const e of this.state.entities)if(e.active&&(e.kind==='boss'||e.kind==='enemy'))this.emit('spawn',{id:e.id,kind:e.kind,hp:e.hp,x:e.x,y:e.y,z:e.z});
      return;
    }
    if(cmd.type==='pause'&&this.state.phase==='PLAYING'){this.state.phase='PAUSED';this.emit('navigation',{action:'pause'});}
    else if(cmd.type==='resume'&&this.state.phase==='PAUSED'){this.state.phase='PLAYING';this.emit('navigation',{action:'resume'});}
    else if(cmd.type==='move'&&this.state.phase==='PLAYING'){
      if(typeof cmd.x==='number'&&Number.isFinite(cmd.x))this.targetX=Math.max(-this.gameConfig.clampX,Math.min(this.gameConfig.clampX,cmd.x));
      else if(typeof cmd.dx==='number'&&Number.isFinite(cmd.dx))this.targetX=cmd.dx===0?null:(cmd.dx>0?this.gameConfig.clampX:-this.gameConfig.clampX);
    }
    else if(cmd.type==='selectLevel'&&['MENU','UPGRADE','UPGRADES','LEVEL_UP','VICTORY','DEFEAT'].includes(this.state.phase)&&cmd.level&&Number.isInteger(cmd.level)&&cmd.level>=1&&cmd.level<=this.state.progress.unlockedLevel)this.state.level=cmd.level;
    else if(cmd.type==='purchase'){
      const id=canonicalUpgrade(cmd.id??'');if(id){updateEconomy(this.state,{...cmd,id},this.gameConfig,this.relay);this.persist();}
    }
    else if(cmd.type==='setAudio'){
      if(typeof cmd.music==='boolean')this.state.progress.audio.music=cmd.music;if(typeof cmd.sfx==='boolean')this.state.progress.audio.sfx=cmd.sfx;this.persist();
    }
    else if(cmd.type==='menu'){this.state.phase='MENU';this.targetX=null;}
    else if(cmd.type==='upgrades'&&this.state.phase!=='PLAYING'){this.state.phase='UPGRADE';}
    else if(cmd.type==='settings'&&this.state.phase!=='PLAYING'){this.state.phase='SETTINGS';}
    this.refreshMetrics();
  }
  /** Explicit host/manual stepping takes ownership from RAF without dropping requested time. */
  public advance(ms:number):void {if(!Number.isFinite(ms)||ms<0)return;this.manualClock=true;this.consume(ms);}
  /** The UI is the only realtime driver; host advance disables it on this instance. */
  public tickRealtime(ms:number):void {if(!this.manualClock)this.consume(ms);}
  private consume(ms:number):void {
    if(this.disposed||this.state.phase!=='PLAYING'||!Number.isFinite(ms)||ms<0)return;
    this.accumulator+=ms;
    while(this.accumulator+1e-8>=FIXED_MS&&this.state.phase==='PLAYING'){
      this.fixedUpdate(FIXED_MS/1000);this.accumulator-=FIXED_MS;
    }
    if(this.state.phase!=='PLAYING')this.accumulator=0;
    this.refreshMetrics();
  }
  public events(afterSeq=0):GameEvent[]{return clone(this.eventLog.getEvents(afterSeq));}
  public isolatedSimulation(params:{caseId?:string;seed?:number;fixture?:FixtureData;render?:boolean;ui?:boolean}):IsolatedSim {
    const child=new Simulation({level:params.fixture?.level??this.opts.level??this.state.level,seed:params.seed??this.seed,fixture:params.fixture,isolated:true});
    this.children.add(child);child.setViewFactory(this.viewFactory);
    const view=(params.render||params.ui)&&this.viewFactory?this.viewFactory(child,{ui:params.ui}):null;
    if(view)child.viewCleanup=view.dispose;
    return {containerId:view?.containerId,snapshot:()=>child.snapshot(),config:()=>child.config(),dispatch:cmd=>child.dispatch(cmd),advance:ms=>child.advance(ms),events:after=>child.events(after),
      engineInfo:()=>child.engineInfo(),projectEntity:id=>child.projectEntity(id),dispose:()=>{child.dispose();this.children.delete(child);}};
  }
  public engineInfo():EngineInfo {return this.sceneHandle?.engineInfo?.()??{name:'babylon',version:'unattached',running:false};}
  public projectEntity(id:string):ProjectedCoord|null {return this.sceneHandle?.projectEntity?.(id)??null;}
  public dispose():void {
    if(this.disposed)return;this.disposed=true;this.viewCleanup?.();this.viewCleanup=null;for(const child of this.children)child.dispose();this.children.clear();
    clearCombat(this.state);this.sceneHandle?.dispose?.();this.state.entities.length=0;this.eventLog.clear();this.refreshMetrics();
  }
  private relay=(event:Pick<GameEvent,'type'|'data'>):void=>{this.emit(event.type,event.data);};
  private emit(type:GameEvent['type'],data:Record<string,unknown>):void {this.eventLog.emit(this.state.simTime,type,data);this.state.metrics.events=this.eventLog.count;}
  private persist():void {this.persistence?.save(progressFrom(this.state.progress));}
  private refreshMetrics():void {
    this.state.metrics.updateLoops=!this.disposed&&this.state.phase==='PLAYING'?1:0;
    this.state.metrics.events=this.eventLog.count;
    this.state.metrics.enemies=this.state.entities.filter(e=>e.active&&(e.kind==='enemy'||e.kind==='boss')).length;
    this.state.skills.effectiveStats.bulletDamage=this.gameConfig.weapon.bulletDamage+(this.state.progress.upgrades.damage??0);
  }
  private fixedUpdate(dt:number):void {
    this.state.simTime+=dt;this.state.metrics.ticks=(this.state.metrics.ticks??0)+1;
    updateNavigation(this.state,dt,this.targetX,this.gameConfig);
    updateGates(this.state,dt,this.gameConfig,this.relay);
    updateEvolution(this.state,dt,this.gameConfig,this.relay);
    if(this.state.phase==='PLAYING'){
      updateBoss(this.state,dt,this.gameConfig,this.relay);
      updateCombat(this.state,dt,this.gameConfig,this.rng,this.relay);
      checkEnemyCollisions(this.state,this.relay);checkObstacleCollisions(this.state,this.relay);
      updateEvolution(this.state,dt,this.gameConfig,this.relay);
      updateBoss(this.state,dt,this.gameConfig,this.relay);
      checkLevelCompletion(this.state,this.gameConfig,this.relay);
    }
    updateEconomy(this.state,null,this.gameConfig,this.relay);
    if(this.state.phase==='VICTORY')this.persist();
    this.refreshMetrics();
  }
}
