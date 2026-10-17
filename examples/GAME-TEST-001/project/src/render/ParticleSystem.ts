import * as BABYLON from '@babylonjs/core';
import type { Vec3 } from '../game/types';

type Effect = 'muzzle' | 'hit' | 'growth' | 'loss' | 'explosion';
interface Slot { system: BABYLON.ParticleSystem; position: BABYLON.Vector3; age: number; lifetime: number; }

/**
 * Fixed 24 x 48 particle pool with a single procedural texture.
 * Repeated fire never allocates a particle system, texture, or timeout.
 * Cosmetic overload replaces the oldest burst; production projectile damage is unchanged.
 */
export class ParticleSystemManager {
  private readonly slots: Slot[] = [];
  private readonly texture: BABYLON.DynamicTexture;
  private cursor = 0;
  private disposed = false;

  constructor(scene: BABYLON.Scene) {
    this.texture = new BABYLON.DynamicTexture('shared_particle_disc', 32, scene, false);
    const ctx = this.texture.getContext();
    ctx.clearRect(0, 0, 32, 32);
    const gradient = ctx.createRadialGradient(16, 16, 0, 16, 16, 15);
    gradient.addColorStop(0, '#ffffff'); gradient.addColorStop(.35, '#ffffff');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, 32, 32);
    this.texture.hasAlpha = true; this.texture.update();
    for (let i = 0; i < 24; i++) {
      const system = new BABYLON.ParticleSystem('pooled_burst_' + i, 48, scene);
      const position = BABYLON.Vector3.Zero();
      system.particleTexture = this.texture;
      system.emitter = position;
      system.emitRate = 0;
      system.disposeOnStop = false;
      system.minEmitBox.set(-.1, 0, -.1);
      system.maxEmitBox.set(.1, .1, .1);
      system.colorDead.set(0, 0, 0, 0);
      system.blendMode = BABYLON.ParticleSystem.BLENDMODE_ADD;
      this.slots.push({ system, position, age: 0, lifetime: 0 });
    }
  }

  private burst(effect: Effect, pos: Vec3): void {
    if (this.disposed) return;
    const slot = this.slots[this.cursor++ % this.slots.length];
    const p = slot.system;
    p.stop(); p.reset();
    slot.position.set(pos.x, pos.y, pos.z);
    const explosion = effect === 'explosion', muzzle = effect === 'muzzle';
    slot.lifetime = explosion ? .8 : effect === 'growth' ? .55 : muzzle ? .12 : .3;
    slot.age = 0;
    p.minLifeTime = slot.lifetime * .6; p.maxLifeTime = slot.lifetime;
    p.minSize = explosion ? .12 : .045; p.maxSize = explosion ? .48 : muzzle ? .13 : .22;
    p.minEmitPower = muzzle ? .2 : 1; p.maxEmitPower = explosion ? 4 : 1.5;
    p.gravity.set(0, explosion ? -3 : effect === 'loss' ? -2 : 1, 0);
    p.direction1.set(muzzle ? -.3 : -1, explosion ? 1 : .2, muzzle ? 1 : -1);
    p.direction2.set(muzzle ? .3 : 1, explosion ? 4 : 2, muzzle ? 3 : 1);
    if (effect === 'growth') { p.color1.set(.15, 1, .4, 1); p.color2.set(.6, 1, .85, 1); }
    else if (effect === 'loss') { p.color1.set(1, .12, .1, 1); p.color2.set(.7, .15, .05, 1); }
    else if (effect === 'hit') { p.color1.set(1, 1, .85, 1); p.color2.set(.5, .7, 1, 1); }
    else { p.color1.set(1, .85, .25, 1); p.color2.set(1, .25, .05, 1); }
    p.manualEmitCount = explosion ? 36 : muzzle ? 4 : 14;
    p.start();
  }

  /** The caller supplies visual time derived from the real simulation. Pause freezes particles. */
  update(deltaSeconds: number): void {
    if (this.disposed) return;
    for (const slot of this.slots) {
      if (slot.lifetime <= 0) continue;
      // A manual host step may aggregate seconds of events; retain the newest burst for a visible frame.
      slot.age += Math.min(.05, deltaSeconds);
      slot.system.updateSpeed = Math.min(.05, deltaSeconds) /
        Math.max(.001, slot.system.getScene()?.getAnimationRatio() ?? 1);
      if (slot.age > slot.lifetime + .05) {
        slot.system.stop(); slot.system.reset(); slot.lifetime = 0;
      }
    }
  }

  playMuzzle(pos: Vec3): void { this.burst('muzzle', pos); }
  playExplosion(pos: Vec3): void { this.burst('explosion', pos); }
  playGrowth(pos: Vec3): void { this.burst('growth', pos); }
  playLoss(pos: Vec3): void { this.burst('loss', pos); }
  playHit(pos: Vec3): void { this.burst('hit', pos); }

  stats(): { systems: number; capacity: number; activeSystems: number; activeParticles: number } {
    return { systems: this.disposed ? 0 : this.slots.length, capacity: this.disposed ? 0 : this.slots.length * 48,
      activeSystems: this.slots.filter(s => s.lifetime > 0).length,
      activeParticles: this.slots.reduce((n, s) => n + s.system.getActiveCount(), 0) };
  }

  reset(): void {
    if (this.disposed) return;
    for (const slot of this.slots) { slot.system.stop(); slot.system.reset(); slot.lifetime = 0; }
    this.cursor = 0;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const slot of this.slots) { slot.system.stop(); slot.system.dispose(false); }
    this.slots.length = 0;
    this.texture.dispose();
  }
}
