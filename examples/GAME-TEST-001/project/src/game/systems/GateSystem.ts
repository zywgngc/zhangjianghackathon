import type { GameState, GameConfig, GateDef, LevelConfig, GameEvent } from '../types';
type EventSink = (event: Omit<GameEvent, 'seq' | 'simTime'>) => void;
export interface SkillDef { id: string; name: string; description: string; type: 'active'; level: number; cooldown: number; operator: string; operand: number; groupId: number; }
export function applyGate(gate: GateDef, currentCount: number): number {
  let value = currentCount;
  if (gate.operator === '+') value += gate.operand;
  else if (gate.operator === '-') value -= gate.operand;
  else if (gate.operator === '×') value *= gate.operand;
  else if (gate.operator === '÷' && gate.operand !== 0) value = Math.floor(value / gate.operand);
  return Math.max(0, Math.min(200, Math.floor(value)));
}
export function isPositiveGate(gate: GateDef, currentCount: number): boolean { return applyGate(gate,currentCount)>currentCount; }
export function buildGateSkills(level: LevelConfig): SkillDef[] {
  return level.gateGroups.flatMap((group,gi)=>group.gates.map((g,i)=>({id:`gate_${gi}_${i}`,name:`${g.operator}${g.operand}`,description:`数值门${g.operator}${g.operand}`,type:'active' as const,level:1,cooldown:0,operator:g.operator,operand:g.operand,groupId:gi})));
}
export function updateGates(state: GameState, dt: number, config: GameConfig, emit: EventSink): void {
  if (state.phase !== 'PLAYING') return;
  const groups = new Map<string | number, typeof state.entities>();
  for (const e of state.entities) if (e.kind==='gate' && e.active && !e.consumed) {
    const key = e.group ?? `z:${e.z}`; const list=groups.get(key)??[]; list.push(e); groups.set(key,list);
  }
  for (const [groupId,gates] of groups) {
    const z=gates[0].z;
    if (state.player.z+1e-7 < z || state.player.z-config.forwardSpeed*dt-1e-7 > z) continue;
    const chosen=gates.reduce((a,b)=>Math.abs(a.x-state.player.x)<=Math.abs(b.x-state.player.x)?a:b);
    if (!chosen.operator || !Number.isFinite(chosen.operand)) continue;
    const oldCount=state.player.soldiers;
    const newCount=applyGate({operator:chosen.operator,operand:chosen.operand!},oldCount);
    state.player.soldiers=newCount;
    for (const gate of gates) {gate.active=false;gate.consumed=true;}
    emit({type:'gate',data:{gateId:chosen.id,groupId,operator:chosen.operator,operand:chosen.operand,oldCount,newCount,result:newCount,isPositive:newCount>oldCount,x:chosen.x,y:chosen.y,z:chosen.z}});
  }
}
