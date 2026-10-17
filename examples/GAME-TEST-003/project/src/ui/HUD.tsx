import {Simulation} from '../game/Simulation';
import {createScene,type SceneHandle} from '../game/renderer';
import {AudioManager} from '../game/audio';
import {MainMenu} from './MainMenu';
import {LevelSelect} from './LevelSelect';
import {PauseMenu} from './PauseMenu';
import {VictoryScreen} from './VictoryScreen';
import {DeadlockScreen} from './DeadlockScreen';
import {Settings} from './Settings';
import React, {useLayoutEffect,useRef,useState} from 'react';
import type { Snapshot, InputCommand } from '../game/types';

export type Dispatch = (command: InputCommand) => void;

export interface HUDProps {
  snapshot: Snapshot;
  dispatch: Dispatch;
}

export const HUD: React.FC<HUDProps> = ({ snapshot, dispatch }) => {
  const handlePause = () => {
    dispatch({ type: 'pause' });
  };

  const handleRestart = () => {
    dispatch({ type: 'restart' });
  };

  const handleMenu = () => {
    dispatch({ type: 'menu' });
  };

  return (
    <div
      data-testid="hud"
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        padding: '8px 16px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        pointerEvents: 'auto',
        flexWrap: 'wrap',
        gap: '6px',
        color: '#fff',
        fontFamily: 'sans-serif',
        fontSize: '14px',
        textShadow: '1px 1px 2px rgba(0,0,0,0.8)',
        zIndex: 10,
      }}
    >
      <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
        <span data-testid="score">
          战力: {snapshot.player.power}
        </span>
        <span>
          生命: {snapshot.player.hp}/{snapshot.player.maxHp}
        </span>
        <span>
          关卡: {snapshot.level}
        </span>
        <span>
          时间: {Math.floor(snapshot.simTime)}秒
        </span>
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <button
          type="button"
          data-action="pause"
          onClick={handlePause}
          style={{
            padding: '4px 12px',
            cursor: 'pointer',
            background: 'rgba(255,255,255,0.2)',
            border: '1px solid rgba(255,255,255,0.4)',
            borderRadius: '4px',
            color: '#fff',
          }}
        >
          暂停
        </button>
        <button
          type="button"
          data-action="restart"
          onClick={handleRestart}
          style={{
            padding: '4px 12px',
            cursor: 'pointer',
            background: 'rgba(255,255,255,0.2)',
            border: '1px solid rgba(255,255,255,0.4)',
            borderRadius: '4px',
            color: '#fff',
          }}
        >
          重新开始
        </button>
        <button
          type="button"
          data-action="menu"
          onClick={handleMenu}
          style={{
            padding: '4px 12px',
            cursor: 'pointer',
            background: 'rgba(255,255,255,0.2)',
            border: '1px solid rgba(255,255,255,0.4)',
            borderRadius: '4px',
            color: '#fff',
          }}
        >
          主菜单
        </button>
      </div>
    </div>
  );
};

// The main game and an independent rendered boundary mount this same view.
// autoAdvance changes the clock owner only; neither state nor UI is substituted.
export function GameView({simulation,autoAdvance=true,onReady}:{simulation:Simulation;autoAdvance?:boolean;onReady?:(handle:SceneHandle)=>void}) {
  const host=useRef<HTMLDivElement>(null),audio=useRef<AudioManager|null>(null);
  const [snapshot,setSnapshot]=useState(()=>simulation.snapshot());
  const [warning,setWarning]=useState('');
  useLayoutEffect(()=>{
    if(!host.current)return;
    const handle=createScene(host.current,simulation);
    const sounds=autoAdvance?new AudioManager():null;audio.current=sounds;
    onReady?.(handle);
    let raf=0,lastTime=performance.now(),lastSequence=0,warningUntil=0,lastSimTime=-1;
    let savedAudio='';
    const loop=(now:number)=>{
      if(autoAdvance)simulation.tickRealtime(Math.min(50,Math.max(0,now-lastTime)));
      lastTime=now;
      const current=simulation.snapshot();setSnapshot(current);
      const audioIdentity=JSON.stringify(current.progress.audio);
      if(sounds&&savedAudio!==audioIdentity){savedAudio=audioIdentity;sounds.setSfx(current.progress.audio.sfx);sounds.setMusic(current.progress.audio.music);}
      if(current.simTime<lastSimTime||current.metrics.events<lastSequence)lastSequence=0;
      lastSimTime=current.simTime;
      const events=simulation.events(lastSequence);
      for(const event of events){
        lastSequence=event.seq;
        const message=event.data.message;
        if(typeof message==='string'){setWarning(message);warningUntil=now+2200;}
        const sound=event.type==='combat_attack_start'?'attack':event.type==='enemy_defeated'?'powerup':event.type==='WEAPON_PICKUP'?'pickup':event.type==='victory'?'victory':event.type==='combat_invalid_warning'?'invalid':null;
        if(sound)sounds?.play(sound);
      }
      if(warningUntil&&now>=warningUntil){setWarning('');warningUntil=0;}
      raf=requestAnimationFrame(loop);
    };
    raf=requestAnimationFrame(loop);
    return()=>{cancelAnimationFrame(raf);handle.dispose();sounds?.dispose();audio.current=null;};
  },[simulation,autoAdvance,onReady]);
  const dispatch=(command:InputCommand)=>{
    if(command.type==='start'){audio.current?.play('start');audio.current?.play('bgm');}
    simulation.releaseManualClock();simulation.dispatch(command);setSnapshot(simulation.snapshot());
  };
  const phase=snapshot.phase,screen=snapshot.uiScreen??'main';
  return <main className="game-container" data-production-view="power-hunter">
    <div className="game-host" ref={host} data-testid="game-root"/>
    {phase==='PLAYING'&&<HUD snapshot={snapshot} dispatch={dispatch}/>}
    {phase==='MENU'&&screen==='main'&&<MainMenu dispatch={dispatch}/>}
    {phase==='MENU'&&screen==='levels'&&<LevelSelect snapshot={snapshot} dispatch={dispatch}/>}
    {phase==='MENU'&&screen==='settings'&&<Settings music={snapshot.progress.audio.music} sfx={snapshot.progress.audio.sfx} dispatch={dispatch}/>}
    {phase==='PAUSED'&&<PauseMenu dispatch={dispatch}/>}
    {phase==='VICTORY'&&<VictoryScreen snapshot={snapshot} dispatch={dispatch}/>}
    {phase==='DEADLOCK'&&<DeadlockScreen dispatch={dispatch}/>}
    {warning&&<div role="status" className="game-warning">{warning}</div>}
  </main>;
}
