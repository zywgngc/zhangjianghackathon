import type { Snapshot, IsolatedSim } from './Simulation';
import { Simulation } from './Simulation';
import type { GameConfig, InputCommand, GameEvent, FixtureData, EngineInfo, ProjectedCoord } from './types';
export interface HarnessAPI {
  version:1;caseId:string;seed:number;snapshot():Snapshot;config():GameConfig;dispatch(cmd:InputCommand):void;advance(ms:number):void;
  events(afterSeq?:number):GameEvent[];engineInfo():EngineInfo;projectEntity(id:string):ProjectedCoord|null;
  isolatedSimulation(opts:{caseId?:string;seed?:number;fixture?:FixtureData;render?:boolean;ui?:boolean}):IsolatedSim;
}
declare global {interface Window {__HARNESS__?:HarnessAPI;}}
export function connectHarness(sim:Simulation):void {
 window.__HARNESS__={version:1,caseId:'GAME-TEST-001',seed:20261016,snapshot:()=>sim.snapshot(),config:()=>sim.config(),
 dispatch:cmd=>sim.dispatch(cmd),advance:ms=>sim.advance(ms),events:after=>sim.events(after),engineInfo:()=>sim.engineInfo(),projectEntity:id=>sim.projectEntity(id),isolatedSimulation:opts=>sim.isolatedSimulation(opts)};
}
export function disconnectHarness():void {delete window.__HARNESS__;}
