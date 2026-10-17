import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Simulation, type Snapshot } from './game/Simulation';
import { connectHarness, disconnectHarness } from './game/bridge';
import { createScene } from './render/BabylonScene';
import { InputHandler } from './game/systems/InputHandler';
import { AudioEngine } from './audio/AudioEngine';
import { MainMenu } from './ui/MainMenu';
import { HUD } from './ui/HUD';
import { PauseMenu } from './ui/PauseMenu';
import { VictoryScreen } from './ui/VictoryScreen';
import { DefeatScreen } from './ui/DefeatScreen';
import { UpgradeScreen } from './ui/UpgradeScreen';
import { SettingsScreen } from './ui/SettingsScreen';
import type { InputCommand } from './game/types';
import './ui/styles.css';
let isolatedViewId=0;
function mountIsolatedView(sim:Simulation,{ui}:{ui?:boolean}):{containerId:string;dispose:()=>void}{
  const container=document.createElement('div');container.id=`isolated-game-${++isolatedViewId}`;
  container.className='isolated-game';document.body.appendChild(container);
  const root=createRoot(container);root.render(<GameView simulation={sim} realtime={false} showUI={ui===true}/>);
  return {containerId:container.id,dispose:()=>{root.unmount();container.remove();}};
}
/** The same React view and Babylon renderer bind active and isolated production cores. */
export function GameView({simulation:sim,realtime=true,showUI=true}:{simulation:Simulation;realtime?:boolean;showUI?:boolean}):React.JSX.Element {
  const host=useRef<HTMLDivElement>(null),audio=useRef<AudioEngine|null>(null);
  const [snap,setSnap]=useState<Snapshot>(()=>sim.snapshot());
  useEffect(()=>{
    if(!host.current)return;
    const scene=createScene(host.current,sim);sim.setSceneHandle(scene);sim.setViewFactory(mountIsolatedView);
    const input=new InputHandler();input.bind(host.current);input.setRoadHalfWidth(sim.config().clampX);
    const sound=new AudioEngine();audio.current=sound;
    let frame=0,previous=0,lastSeq=0,wasDragging=false,disposed=false,lastUI=0,lastSimTime=0,generation=-1;
    let lastMusic:boolean|undefined,lastSfx:boolean|undefined;
    const loop=(now:number)=>{
      if(disposed)return;
      const before=sim.getRenderState();input.setCurrentX(before.player.x);
      const target=input.getTargetX();
      if(target!==null){sim.dispatch({type:'move',x:target});wasDragging=true;}
      else if(wasDragging){sim.dispatch({type:'move',dx:0});wasDragging=false;}
      if(realtime&&previous)sim.tickRealtime(Math.min(100,Math.max(0,now-previous)));
      previous=now;
      const next=sim.getRenderState();
      const cursor=sim.eventCursor();if(cursor.generation!==generation||next.simTime<lastSimTime){lastSeq=0;generation=cursor.generation;}lastSimTime=next.simTime;
      const events=sim.events(lastSeq);let shots=0,hits=0,explosions=0;
      for(const event of events){
        if(event.type==='navigation'&&['start','restart'].includes(String(event.data.action)))sound.play('start');
        else if(event.type==='shoot'){if(shots++<3)sound.play('shoot');}
        else if(event.type==='gate')sound.play(event.data.isPositive?'gate_positive':'gate_negative');
        else if(event.type==='damage'&&hits++<2)sound.play('enemy_hit');
        else if(event.type==='collision')sound.play('soldier_loss');
        else if(event.type==='death'&&explosions++<2)sound.play(event.data.kind==='boss'?'boss_destroyed':'explosion');
        else if(event.type==='victory'||event.type==='defeat')sound.play(event.type);
        lastSeq=event.seq;
      }
      if(lastMusic!==next.progress.audio.music){lastMusic=next.progress.audio.music;sound.setMusic(lastMusic);}
      if(lastSfx!==next.progress.audio.sfx){lastSfx=next.progress.audio.sfx;sound.setSFX(lastSfx);}
      if(now-lastUI>40){setSnap({...next,player:{...next.player},progress:{...next.progress,upgrades:{...next.progress.upgrades},audio:{...next.progress.audio}},entities:next.entities.filter(e=>e.kind==='boss').map(e=>({...e})),metrics:{...next.metrics}});lastUI=now;}
      frame=requestAnimationFrame(loop);
    };
    frame=requestAnimationFrame(loop);
    return()=>{disposed=true;cancelAnimationFrame(frame);input.unbind();scene.dispose();sound.dispose();sim.setSceneHandle(null);audio.current=null;};
  },[sim,realtime]);
  const dispatch=(command:InputCommand)=>{
    audio.current?.init();audio.current?.resumeContext();sim.dispatch(command);setSnap(sim.snapshot());
  };
  const level=sim.config().levels.find(l=>l.level===snap.level)!;
  const boss=snap.entities.find(e=>e.kind==='boss'&&e.active),bossPercent=boss?boss.hp/(boss.maxHp??level.bossHp)*100:0;
  const playing=snap.phase==='PLAYING';
  return <main className="production-game" data-testid="game-root">
    <div ref={host} className="game-host" aria-label="数字门枪手三维战场"/>
    {showUI&&<div className="ui-layer">
      {playing&&<HUD soldiers={snap.player.soldiers} progress={snap.player.z/level.trackLength} bossHP={bossPercent} bossHealth={boss?.hp??0} bossMaxHealth={boss?.maxHp??level.bossHp} level={snap.level} onPause={()=>dispatch({type:'pause'})} onRestart={()=>dispatch({type:'restart'})}/>}
      {snap.phase==='MENU'&&<><MainMenu level={snap.level} gold={snap.progress.gold} onStart={()=>dispatch({type:'start'})} onUpgrade={()=>dispatch({type:'upgrades'})} onSettings={()=>dispatch({type:'settings'})}/>
        <div className="level-select">{[1,2,3].map(n=><button key={n} type="button" data-action={`level-${n}`} disabled={n>snap.progress.unlockedLevel} aria-pressed={snap.level===n} onClick={()=>dispatch({type:'selectLevel',level:n})}>第{n}关{n>snap.progress.unlockedLevel?' 🔒':''}</button>)}</div></>}
      {snap.phase==='PAUSED'&&<PauseMenu onResume={()=>dispatch({type:'resume'})} onRestart={()=>dispatch({type:'restart'})} onMenu={()=>dispatch({type:'menu'})}/>}
      {snap.phase==='VICTORY'&&<VictoryScreen gold={level.reward} level={snap.level} onNext={()=>dispatch(snap.level<3?{type:'start',level:snap.level+1}:{type:'menu'})} onMenu={()=>dispatch({type:'menu'})} onReplay={()=>dispatch({type:'restart'})}/>}
      {snap.phase==='DEFEAT'&&<DefeatScreen reason={snap.player.soldiers===0?'士兵全部阵亡':'未能在道路终点前摧毁装甲车'} onRetry={()=>dispatch({type:'restart'})} onMenu={()=>dispatch({type:'menu'})}/>}
      {['UPGRADE','UPGRADES','LEVEL_UP'].includes(snap.phase)&&<UpgradeScreen gold={snap.progress.gold} soldierLv={snap.progress.upgrades.soldiers??0} damageLv={snap.progress.upgrades.damage??0} onBuy={id=>dispatch({type:'purchase',id:id==='soldier'?'soldiers':id})} onBack={()=>dispatch({type:'menu'})}/>}
      {snap.phase==='SETTINGS'&&<SettingsScreen music={snap.progress.audio.music} sfx={snap.progress.audio.sfx} onToggleMusic={()=>dispatch({type:'setAudio',music:!snap.progress.audio.music})} onToggleSfx={()=>dispatch({type:'setAudio',sfx:!snap.progress.audio.sfx})} onBack={()=>dispatch({type:'menu'})}/>}
      <output className="wallet" data-testid="score">金币：{snap.progress.gold}</output>
      <output className="game-status" data-testid="game-status">{({MENU:'主菜单',PLAYING:'战斗中',PAUSED:'游戏暂停',VICTORY:'闯关成功',DEFEAT:'挑战失败',UPGRADE:'永久升级',SETTINGS:'设置'} as Record<string,string>)[snap.phase]??snap.phase}</output>
    </div>}
  </main>;
}
export default function App():React.JSX.Element {
  const [sim]=useState(()=>new Simulation({level:1,seed:20261016}));
  useEffect(()=>{connectHarness(sim);return()=>{disconnectHarness();sim.dispose();};},[sim]);
  return <GameView simulation={sim}/>;
}
