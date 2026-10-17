import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {createElement} from 'react';
import {Simulation} from './Simulation';
import {createScene,type SceneHandle} from './renderer';
import {GameView} from '../ui/HUD';
import type {InputCommand,IsolationParams,SimulationHandle,EngineInfo} from './types';

type ObservedHandle=SimulationHandle&{command:(name:string,args?:Record<string,unknown>)=>void;engineInfo?:()=>EngineInfo;projectEntity?:(id:string)=>{x:number;y:number}|null;containerId?:string};
export interface Harness extends ObservedHandle{version:number;caseId:string;isolatedSimulation:(params:IsolationParams)=>ObservedHandle|Promise<ObservedHandle>;}
declare global{interface Window{__HARNESS__?:Harness;}}
let containerSequence=0;
const commands=new Set(['start','selectLevel','pause','resume','restart','move','selectTarget','setAudio','menu','openLevels','settings']);
function observe(simulation:Simulation,scene?:SceneHandle):ObservedHandle{
  return{snapshot:()=>simulation.snapshot(),config:()=>simulation.config(),dispatch:command=>simulation.dispatch(command),
    command:(name,args={})=>{if(!commands.has(name))throw Error('Unsupported production command: '+name);simulation.dispatch({...args,type:name}as InputCommand);},
    advance:milliseconds=>simulation.advance(milliseconds),events:sequence=>simulation.events(sequence),dispose:()=>simulation.dispose(),
    engineInfo:scene?()=>scene.engineInfo():undefined,projectEntity:scene?id=>scene.projectEntity(id):undefined};
}
export function registerHarness(simulation:Simulation,scene:SceneHandle):void{
  window.__HARNESS__={...observe(simulation,scene),version:1,caseId:'GAME-TEST-003',
    isolatedSimulation:async params=>{
      if(params.caseId!=='GAME-TEST-003')throw Error('Wrong isolated case identifier');
      const isolated=new Simulation({seed:params.seed??42,fixture:params.fixture??{},persist:false});
      let container:HTMLDivElement|null=null,isolatedScene:SceneHandle|undefined,reactRoot:ReturnType<typeof createRoot>|null=null;
      if(params.render||params.ui){
        container=document.createElement('div');container.id='power-hunter-isolated-'+(++containerSequence);
        Object.assign(container.style,{position:'fixed',inset:'0',width:'min(100vw,900px)',height:'min(100vh,700px)',zIndex:'10000',background:'#101627'});
        document.body.append(container);
        if(params.ui){reactRoot=createRoot(container);flushSync(()=>reactRoot!.render(createElement(GameView,{simulation:isolated,autoAdvance:false,onReady:handle=>{isolatedScene=handle;}})));}
        else isolatedScene=createScene(container,isolated);
        if(!isolatedScene){reactRoot?.unmount();container.remove();isolated.dispose();throw Error('Production view did not mount its real scene');}
      }
      const result:ObservedHandle={...observe(isolated,isolatedScene),containerId:container?.id};
      let disposed=false;
      result.dispose=()=>{if(disposed)return;disposed=true;if(reactRoot)flushSync(()=>reactRoot!.unmount());else isolatedScene?.dispose();container?.remove();isolated.dispose();};
      // Creating a scene and drawing its first real frame are distinct. Return
      // a rendered handle only after the actual engine reports real readiness.
      if(isolatedScene){
        for(let frames=0;frames<120&&!isolatedScene.engineInfo().running;frames++)
          await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
        if(!isolatedScene.engineInfo().running){result.dispose();throw Error('Production isolated renderer did not become ready');}
      }
      return result;
    }};
}
