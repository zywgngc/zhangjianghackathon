import type { Entity } from '../types';

/** Owned by one GameState, never shared between active and isolated simulations. */
export class ProjectilePool {
  private reusable: Entity[] = [];
  private active: Entity[] = [];
  private nextId = 1;
  acquire(): Entity {
    const p = this.reusable.pop() ?? { id: `projectile_${this.nextId++}`, kind: 'projectile' as const,
      x: 0, y: 0, z: 0, hp: 1, power: 0, active: true };
    p.active = true; p.lifetime = 0; p.targetId = undefined;
    p.vx = 0; p.vy = 0; p.vz = 0;
    this.active.push(p);
    return p;
  }
  release(p: Entity): void {
    const index = this.active.indexOf(p);
    if (index < 0) return;
    this.active.splice(index, 1); p.active = false; this.reusable.push(p);
  }
  update(dt: number): void {
    for (const p of this.active) {
      p.previousX = p.x; p.previousY = p.y; p.previousZ = p.z;
      p.x += (p.vx ?? 0) * dt; p.y += (p.vy ?? 0) * dt; p.z += (p.vz ?? 0) * dt;
      p.lifetime = (p.lifetime ?? 0) + dt;
    }
  }
  clear(): void { for (const p of this.active) { p.active = false; this.reusable.push(p); } this.active.length = 0; }
  getActive(): readonly Entity[] { return this.active; }
  getReusableCount(): number { return this.reusable.length; }
}
