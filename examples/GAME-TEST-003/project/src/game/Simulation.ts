// One production state owner for UI, renderer and independent test worlds.
import type { GameState, Snapshot, GameConfig, InputCommand, SimulationOptions,
  GameEvent, IsolationParams, SimulationHandle, Entity, SimulationFixture } from './types';
import { GAME_CONFIG } from './config';
import { PRNG } from './prng';
import { updateNavigation, handleSelectTarget } from './navigation';
import { updateCombat } from './combat';
import { updateWeapons } from './weapons';
import { updateEnemies, checkDeadlock } from './enemies';
import { updateBoss, initializeBossLifecycle, clearBossLifecycle } from './boss';
import { initLevel } from './levels';
import { SaveManager } from './save';

const FIXED_DT = 1 / 60;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
function initialState(): GameState {
  return { phase: 'MENU', uiScreen:'main', level: 0, simTime: 0,
    player:{x:0,y:0,z:0,hp:100,maxHp:100,power:10,soldiers:0,facing:0,animation:'idle'},
    entities:[],skills:{active:[],passive:[],ultimate:[],upgradeChoices:[]},
    progress:{gold:0,unlockedLevel:1,completedLevels:[],upgrades:{},audio:{music:true,sfx:true}},
    metrics:{updateLoops:1,projectiles:0,enemies:0,events:0,shotsFired:0,totalDamage:0,enemiesDefeated:0,skillAttacks:{W01:0}},
    effectiveStats:{maxHp:100,damageMultiplier:1,damageReduction:0,moveSpeed:5,cooldownReduction:0,healOnKillChance:0,xpMultiplier:1,rangeMultiplier:1},
    targetId:null,path:[],interactionLock:false,attackTimer:0,absorptionTimer:0};
}
export class Simulation {
  private state=initialState();
  private cfg:GameConfig=clone(GAME_CONFIG);
  private rng:PRNG;
  private eventLog:GameEvent[]=[];
  private eventSeq=0;
  private accumulator=0;
  private disposed=false;
  private completedThisRun=false;
  private persistent:boolean;
  private generation=0;
  private manualClock=false;
  constructor(options:SimulationOptions={}) {
    this.persistent=options.persist??!options.fixture;
    this.rng=new PRNG(options.seed??42);
    const saved=options.save??(this.persistent?SaveManager.load():null);
    if(saved?.progress)this.mergeProgress(saved.progress);
    // Initialize first, then replace an independent initial scene. A level reset
    // must never silently erase caller-specified physical initial conditions.
    if(options.fixture){this.startLevel(options.level??options.fixture.level??1,true);this.applyFixture(options.fixture);}
    else if(options.level!==undefined)this.startLevel(options.level,true);
  }
  private mergeProgress(progress:Partial<GameState['progress']>):void {
    this.state.progress={...this.state.progress,...clone(progress),
      upgrades:{...this.state.progress.upgrades,...progress.upgrades},
      audio:{...this.state.progress.audio,...progress.audio},
      completedLevels:[...(progress.completedLevels??this.state.progress.completedLevels)]};
  }
  private applyFixture(fixture:SimulationFixture):void {
    if(fixture.phase&&!['MENU','PLAYING','UPGRADE'].includes(fixture.phase))throw Error('Only initial nonterminal fixture phases are allowed');
    if(fixture.player)Object.assign(this.state.player,clone(fixture.player));
    if(fixture.progress)this.mergeProgress(fixture.progress);
    if(fixture.skills)Object.assign(this.state.skills,clone(fixture.skills));
    const make=(value:Partial<Entity>&Pick<Entity,'id'|'x'|'z'>,kind:string):Entity=>({y:0,hp:1,power:0,active:true,...clone(value),kind,group:kind});
    this.state.entities=[...(fixture.enemies??[]).map(e=>make(e,'enemy')),
      ...(fixture.weapons??[]).map(e=>make(e,'weapon')),...(fixture.boss?[make(fixture.boss,'boss')]:[])];
    const level=this.cfg.levels.find(row=>row.id===this.state.level)!;
    level.obstacles=(fixture.obstacles??[]).map(value=>({kind:'wall',...clone(value)}));
    this.state.entities.push(...level.obstacles.map(value=>({id:value.id,kind:value.kind,group:'obstacle',
      x:value.x,y:0,z:value.z,hp:999999,power:0,active:true,operand:value.width,bonus:value.depth})));
    this.state.phase=fixture.phase??'PLAYING';
    initializeBossLifecycle(this.state);
    this.eventLog=[];this.eventSeq=0;this.state.metrics.events=0;
  }
  private startLevel(level:number,constructing=false):void {
    if(!Number.isInteger(level)||!this.cfg.levels.some(row=>row.id===level))return;
    if(!constructing&&level>this.state.progress.unlockedLevel){this.emit('level_locked',{level});return;}
    initLevel(this.state,level,this.cfg,this.rng);
    for(const entity of this.state.entities)if(entity.group==='enemy'){entity.archetype=entity.kind;entity.kind='enemy';}
    this.state.uiScreen='game';this.state.player.animation='idle';this.state.player.facing=0;this.state.player.rotationY=0;
    this.state.metrics.updateLoops=1;this.accumulator=0;this.completedThisRun=false;
    initializeBossLifecycle(this.state);
    this.state.pendingAbsorption=undefined;this.generation++;
    this.eventLog=[];this.eventSeq=0;this.emit('level_start',{level});
    for(const entity of this.state.entities)this.emit('spawn',clone(entity)as unknown as Record<string,unknown>);
  }
  private emit(type:string,data:Record<string,unknown>):void {
    this.eventLog.push({seq:++this.eventSeq,simTime:this.state.simTime,type,data:clone(data)});
    this.state.metrics.events=this.eventLog.length;
  }
  snapshot():Snapshot{return clone(this.state);}
  /** Borrowed read-only view for the real renderer; never exposed on the bridge. */
  getRenderState():Snapshot{return this.state;}
  eventCursor():{generation:number;sequence:number}{return{generation:this.generation,sequence:this.eventSeq};}
  releaseManualClock():void{this.manualClock=false;}
  tickRealtime(milliseconds:number):void{if(!this.manualClock)this.integrate(milliseconds);}
  config():GameConfig{return clone(this.cfg);}
  dispatch(command:InputCommand):void {
    if(this.disposed)return;
    const sink=(event:GameEvent)=>this.emit(event.type,event.data);
    switch(command.type){
      case 'menu':case 'openLevels':case 'settings':
        this.state.phase='MENU';this.state.uiScreen=command.type==='menu'?'main':command.type==='openLevels'?'levels':'settings';
        this.state.targetId=null;this.state.path=[];return;
      case 'start':if(command.seed!==undefined)this.rng=new PRNG(command.seed);this.startLevel(command.level);return;
      case 'selectLevel':if(command.level<=this.state.progress.unlockedLevel&&this.cfg.levels.some(row=>row.id===command.level))this.state.level=command.level;return;
      case 'restart':this.startLevel(this.state.level||1);return;
      case 'pause':if(this.state.phase==='PLAYING'){this.state.phase='PAUSED';this.emit('paused',{});}return;
      case 'resume':if(this.state.phase==='PAUSED'){this.state.phase='PLAYING';this.emit('resumed',{});}return;
      case 'selectTarget':handleSelectTarget(this.state,this.cfg,command.id,command.ground,sink);return;
      case 'move':{
        if(this.state.phase!=='PLAYING'||this.state.interactionLock)return;
        let x=command.x??this.state.player.x,z=command.z??this.state.player.z;
        if(command.dx!==undefined||command.dy!==undefined){const length=Math.hypot(command.dx??0,command.dy??0);if(!length)return;
          x=this.state.player.x+(command.dx??0)/length*2;z=this.state.player.z+(command.dy??0)/length*2;}
        handleSelectTarget(this.state,this.cfg,undefined,{x,z},sink);return;
      }
      case 'setAudio':if(command.music!==undefined)this.state.progress.audio.music=command.music;
        if(command.sfx!==undefined)this.state.progress.audio.sfx=command.sfx;this.persist();return;
      case 'purchase':case 'chooseUpgrade':return;
    }
  }
  advance(milliseconds:number):void{this.manualClock=true;this.integrate(milliseconds);}
  private integrate(milliseconds:number):void {
    if(this.disposed||this.state.phase!=='PLAYING')return;
    if(!Number.isFinite(milliseconds)||milliseconds<0)throw Error('advance requires nonnegative finite milliseconds');
    this.accumulator+=milliseconds/1000;
    while(this.accumulator+1e-10>=FIXED_DT&&this.state.phase==='PLAYING'){
      this.accumulator=Math.max(0,this.accumulator-FIXED_DT);this.step(FIXED_DT);
    }
    if(this.state.phase!=='PLAYING')this.accumulator=0;
  }
  private step(dt:number):void {
    if(this.state.phase!=='PLAYING')return;
    this.state.simTime+=dt;
    const sink=(event:GameEvent)=>this.emit(event.type,event.data);
    const before={x:this.state.player.x,z:this.state.player.z};
    updateNavigation(this.state,dt,this.cfg,this.rng,sink);
    const dx=this.state.player.x-before.x,dz=this.state.player.z-before.z;
    if(Math.hypot(dx,dz)>1e-8)this.state.player.facing=Math.atan2(dx,dz);
    const target=this.state.entities.find(entity=>entity.id===this.state.targetId&&entity.active);
    if(target&&(this.state.attackTimer>0||this.state.interactionLock))this.state.player.facing=Math.atan2(target.x-this.state.player.x,target.z-this.state.player.z);
    updateCombat(this.state,dt,this.cfg,this.rng,sink);
    updateWeapons(this.state,dt,this.cfg,this.rng,sink);
    updateEnemies(this.state,dt,this.cfg,this.rng,sink);
    this.state.player.animation=this.state.attackTimer>0?'attack':this.state.absorptionTimer>0?'absorb':Math.hypot(dx,dz)>1e-8?'walk':'idle';
    if(this.state.attackTimer>0&&target)this.state.player.facing=Math.atan2(target.x-this.state.player.x,target.z-this.state.player.z);
    this.state.player.rotationY=this.state.player.facing;
    if(updateBoss(this.state,dt,this.cfg,this.rng,sink))this.finishVictory();
    else if(!this.state.interactionLock&&checkDeadlock(this.state,this.cfg)){this.state.phase='DEADLOCK';this.emit('deadlock',{level:this.state.level});}
  }
  private finishVictory():void {
    if(this.completedThisRun)return;
    this.completedThisRun=true;this.state.phase='VICTORY';const level=this.state.level;
    if(!this.state.progress.completedLevels.includes(level))this.state.progress.completedLevels.push(level);
    this.state.progress.unlockedLevel=Math.max(this.state.progress.unlockedLevel,Math.min(level+1,this.cfg.levels.length));
    this.emit('victory',{level,finalPower:this.state.player.power});this.persist();
  }
  private persist():void{if(this.persistent)SaveManager.save({progress:this.state.progress});}
  events(afterSequence=0):GameEvent[]{return clone(this.eventLog.filter(event=>event.seq>afterSequence));}
  isolatedSimulation(params:IsolationParams):SimulationHandle {
    if(params.caseId&&params.caseId!=='GAME-TEST-003')throw Error('Wrong isolated case identifier');
    const simulation=new Simulation({seed:params.seed??42,fixture:params.fixture??{},persist:false});
    return{snapshot:()=>simulation.snapshot(),config:()=>simulation.config(),dispatch:command=>simulation.dispatch(command),
      advance:milliseconds=>simulation.advance(milliseconds),events:sequence=>simulation.events(sequence),dispose:()=>simulation.dispose()};
  }
  dispose():void{if(this.disposed)return;this.disposed=true;clearBossLifecycle(this.state);this.state.metrics.updateLoops=0;this.eventLog=[];this.state.entities=[];}
}
