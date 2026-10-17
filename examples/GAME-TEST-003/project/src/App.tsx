import {useCallback,useEffect,useState} from 'react';
import {Simulation} from './game/Simulation';
import type {SceneHandle} from './game/renderer';
import {registerHarness} from './game/bridge';
import {GameView} from './ui/HUD';

export default function App(){
  const [simulation]=useState(()=>new Simulation({seed:20261017,persist:true}));
  const ready=useCallback((scene:SceneHandle)=>registerHarness(simulation,scene),[simulation]);
  useEffect(()=>()=>{simulation.dispose();delete window.__HARNESS__;},[simulation]);
  return <GameView simulation={simulation} onReady={ready}/>;
}
