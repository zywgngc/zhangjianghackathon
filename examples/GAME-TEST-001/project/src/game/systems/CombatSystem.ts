import type { GameState, GameConfig, Entity, GameEvent } from '../types';
import type { PRNG } from '../prng';
import { ProjectilePool } from './ProjectilePool';
export type EventSink = (event: Omit<GameEvent, 'seq' | 'simTime'>) => void;
interface CombatRuntime { pool: ProjectilePool; elapsed: number; }
let runtimes = new WeakMap<GameState, CombatRuntime>();
// Compatibility export for callers of the old module. Production uses the per-state pool below.
export const projectilePool = new ProjectilePool();
function runtime(state: GameState): CombatRuntime {
  let value = runtimes.get(state);
  if (!value) { value = { pool: new ProjectilePool(), elapsed: 0 }; runtimes.set(state, value); }
  return value;
}
function targetAhead(state: GameState, soldier: Entity): Entity | null {
  let result: Entity | null = null, score = Infinity;
  for (const e of state.entities) {
    if (!e.active || (e.kind !== 'enemy' && e.kind !== 'boss') || e.z <= state.player.z) continue;
    const distance = Math.hypot(e.x - soldier.x, e.z - soldier.z);
    // Closest forward enemy first, with a small same-lane tie preference.
    const value = distance + Math.abs(e.x - soldier.x) * 0.01;
    if (value < score) { score = value; result = e; }
  }
  return result;
}
function segmentDistanceSq(p: Entity, e: Entity): number {
  const ax = p.previousX ?? p.x, ay = p.previousY ?? p.y, az = p.previousZ ?? p.z;
  const dx = p.x - ax, dy = p.y - ay, dz = p.z - az;
  const length = dx * dx + dy * dy + dz * dz;
  const t = length > 0 ? Math.max(0, Math.min(1, ((e.x-ax)*dx + (e.y+.75-ay)*dy + (e.z-az)*dz) / length)) : 0;
  return (ax+t*dx-e.x)**2 + (ay+t*dy-(e.y+.75))**2 + (az+t*dz-e.z)**2;
}
export function updateCombat(state: GameState, dt: number, config: GameConfig, _rng: PRNG, emit: EventSink): void {
  if (state.phase !== 'PLAYING' || state.player.soldiers <= 0) return;
  const current = runtime(state), interval = 1 / config.weapon.attacksPerSecond;
  current.elapsed += dt;
  while (current.elapsed + 1e-9 >= interval) {
    current.elapsed -= interval;
    const soldiers = state.entities.filter(e => e.active && e.kind === 'soldier');
    for (const soldier of soldiers) {
      const target = targetAhead(state, soldier);
      if (!target) continue;
      const p = current.pool.acquire();
      p.x = soldier.x; p.y = soldier.y+.75; p.z = soldier.z;
      const dx = target.x-p.x, dy = target.y+.75-p.y, dz = target.z-p.z;
      const length = Math.hypot(dx, dy, dz) || 1;
      p.vx = dx / length * config.weapon.projectileSpeed;
      p.vy = dy / length * config.weapon.projectileSpeed;
      p.vz = dz / length * config.weapon.projectileSpeed;
      p.power = config.weapon.bulletDamage + (state.progress.upgrades.damage ?? 0);
      p.targetId = target.id; soldier.animation = 'shoot';
      state.metrics.shotsFired++;
      emit({ type: 'shoot', data: { from: soldier.id, targetId: target.id, amount: p.power, skillId: 'rifle', x: p.x, y: p.y, z: p.z } });
    }
  }
  current.pool.update(dt);
  const targets = state.entities.filter(e => e.active && (e.kind === 'enemy' || e.kind === 'boss'));
  const active = current.pool.getActive();
  for (let pi=active.length-1; pi>=0; pi--) {
    const p=active[pi];
    if ((p.lifetime ?? 0) > config.weapon.projectileLifetime + 1e-9) { current.pool.release(p); continue; }
    for (const e of targets) {
      if (!e.active) continue;
      const radius = e.kind === 'boss' ? 2 : 1;
      if (segmentDistanceSq(p, e) > radius * radius) continue;
      e.hp = Math.max(0, e.hp-p.power); e.hitTime = state.simTime;
      state.metrics.totalDamage += p.power;
      emit({ type: 'damage', data: { targetId: e.id, amount: p.power, damage: p.power, hp: e.hp, skillId: 'rifle', x: e.x, y: e.y, z: e.z } });
      if (e.hp <= 0) {
        e.active = false; e.animation = 'death'; state.metrics.enemiesDefeated++;
        emit({ type: 'death', data: { entityId: e.id, kind: e.kind, source: 'projectile', x: e.x, y: e.y, z: e.z } });
      }
      current.pool.release(p); break;
    }
  }
  // Expose actual pooled projectiles to the one production renderer/snapshot.
  let write = 0;
  for (const e of state.entities) if (e.kind !== 'projectile') state.entities[write++] = e;
  state.entities.length = write;
  for (const p of current.pool.getActive()) state.entities.push(p);
  state.metrics.projectiles = current.pool.getActive().length;
  state.metrics.pools = { projectiles: state.metrics.projectiles, reusable: current.pool.getReusableCount() };
}
export function clearCombat(state?: GameState): void {
  if (state) { runtimes.get(state)?.pool.clear(); runtimes.delete(state); }
  else { projectilePool.clear(); runtimes = new WeakMap(); }
}
