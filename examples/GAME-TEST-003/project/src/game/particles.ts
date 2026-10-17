import { Scene, Vector3, Color4, Color3, Mesh, MeshBuilder, DynamicTexture, StandardMaterial, ParticleSystem, InstancedMesh } from '@babylonjs/core';
export interface ParticleEffectHandle { dispose(): void; }
interface Burst { system: ParticleSystem; emitter: Vector3; age: number; lifetime: number; generation: number; }
interface Energy { mesh: InstancedMesh; from: Vector3; to: Vector3; age: number; duration: number; generation: number; }
interface Ring { mesh: Mesh; material: StandardMaterial; age: number; duration: number; generation: number; }
const managers = new WeakMap<Scene, ParticleManager>();

/** Fixed resources: 16x40 particles, 16 energy motes, four selection rings, one texture. */
export class ParticleManager {
  private readonly bursts: Burst[] = [];
  private readonly energy: Energy[] = [];
  private readonly rings: Ring[] = [];
  private readonly texture: DynamicTexture;
  private readonly energySource: Mesh;
  private readonly energyMaterial: StandardMaterial;
  private cursor = 0; private orbCursor = 0; private ringCursor = 0; private disposed = false;
  constructor(private readonly scene: Scene) {
    managers.set(scene, this);
    this.texture = new DynamicTexture('pooled_energy_disc', 32, scene, false);
    const ctx = this.texture.getContext(); ctx.clearRect(0, 0, 32, 32);
    const gradient = ctx.createRadialGradient(16, 16, 0, 16, 16, 16);
    gradient.addColorStop(0, 'white'); gradient.addColorStop(.4, 'white'); gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, 32, 32); this.texture.hasAlpha = true; this.texture.update();
    for (let i = 0; i < 16; i++) {
      const system = new ParticleSystem('pooled_fantasy_burst_' + i, 40, scene), emitter = Vector3.Zero();
      system.particleTexture = this.texture; system.emitter = emitter; system.emitRate = 0; system.disposeOnStop = false;
      system.colorDead.set(0, 0, 0, 0); system.blendMode = ParticleSystem.BLENDMODE_ADD;
      system.minEmitBox.set(-.1, 0, -.1); system.maxEmitBox.set(.1, .1, .1);
      this.bursts.push({ system, emitter, age: 0, lifetime: 0, generation: 0 });
    }
    this.energySource = MeshBuilder.CreateSphere('energy_mote_source', { diameter: .13, segments: 4 }, scene);
    this.energyMaterial = new StandardMaterial('energy_glow', scene);
    this.energyMaterial.emissiveColor.set(.4, 1, .6); this.energyMaterial.diffuseColor.set(.4, 1, .6);
    this.energySource.material = this.energyMaterial; this.energySource.isVisible = false; this.energySource.isPickable = false;
    for (let i = 0; i < 16; i++) {
      const mesh = this.energySource.createInstance('pooled_energy_mote_' + i); mesh.isVisible = true; mesh.isPickable = false; mesh.setEnabled(false);
      this.energy.push({ mesh, from: Vector3.Zero(), to: Vector3.Zero(), age: 0, duration: 0, generation: 0 });
    }
    for (let i = 0; i < 4; i++) {
      const mesh = MeshBuilder.CreateTorus('pooled_selection_ring_' + i, { diameter: 1.5, thickness: .045, tessellation: 24 }, scene);
      const material = new StandardMaterial('selection_ring_' + i, scene); material.disableLighting = true;
      mesh.material = material; mesh.isPickable = false; mesh.setEnabled(false);
      this.rings.push({ mesh, material, age: 0, duration: 0, generation: 0 });
    }
    scene.onDisposeObservable.addOnce(() => this.dispose());
  }

  spawnBurst(position: Vector3, color: Color4, count = 20, lifetime = .65): ParticleEffectHandle {
    if (this.disposed) return { dispose() {} };
    const slot = this.bursts[this.cursor++ % this.bursts.length], p = slot.system;
    p.stop(); p.reset(); slot.emitter.copyFrom(position); slot.age = 0; slot.lifetime = lifetime;
    const generation = ++slot.generation;
    p.color1.copyFrom(color); p.color2.set(color.r, color.g, color.b, .3);
    p.minSize = .05; p.maxSize = .18; p.minLifeTime = lifetime * .5; p.maxLifeTime = lifetime;
    p.minEmitPower = 1; p.maxEmitPower = 2.5; p.gravity.set(0, -1, 0);
    p.direction1.set(-1, .3, -1); p.direction2.set(1, 2, 1);
    p.manualEmitCount = Math.max(1, Math.min(40, Math.floor(count))); p.start();
    return { dispose: () => { if (slot.generation === generation) { p.stop(); p.reset(); slot.lifetime = 0; } } };
  }

  spawnAbsorption(from: Vector3, to: Vector3, _color: Color4, duration = .5): ParticleEffectHandle {
    if (this.disposed) return { dispose() {} };
    const used: { slot: Energy; generation: number }[] = [];
    for (let i = 0; i < 5; i++) {
      const slot = this.energy[this.orbCursor++ % this.energy.length];
      slot.from.copyFrom(from); slot.to.copyFrom(to); slot.age = -i * .025; slot.duration = duration;
      slot.mesh.position.copyFrom(from); slot.mesh.setEnabled(true); const generation = ++slot.generation;
      used.push({ slot, generation });
    }
    return { dispose: () => { for (const { slot, generation } of used) if (slot.generation === generation) { slot.duration = 0; slot.mesh.setEnabled(false); } } };
  }

  spawnSelectionRing(position: Vector3, color: Color4, radius = .65): ParticleEffectHandle {
    const ring = this.rings[this.ringCursor++ % this.rings.length], generation = ++ring.generation;
    ring.age = 0; ring.duration = 1.2; ring.mesh.position.copyFrom(position); ring.mesh.position.y += .04;
    ring.mesh.scaling.setAll(radius / .75); ring.material.emissiveColor.set(color.r, color.g, color.b); ring.material.alpha = .95; ring.mesh.setEnabled(true);
    return { dispose: () => { if (ring.generation === generation) { ring.duration = 0; ring.mesh.setEnabled(false); } } };
  }

  update(deltaSeconds: number): void {
    if (this.disposed) return;
    for (const slot of this.bursts) {
      slot.system.updateSpeed = Math.min(.05, Math.max(0, deltaSeconds)) / Math.max(.001, this.scene.getAnimationRatio());
      if (slot.lifetime <= 0) continue;
      slot.age += deltaSeconds;
      if (slot.age > slot.lifetime) { slot.system.stop(); slot.system.reset(); slot.lifetime = 0; }
    }
    for (const slot of this.energy) {
      if (slot.duration <= 0) continue;
      slot.age += deltaSeconds; const t = Math.max(0, Math.min(1, slot.age / slot.duration));
      Vector3.LerpToRef(slot.from, slot.to, t, slot.mesh.position);
      slot.mesh.position.y += Math.sin(t * Math.PI) * .7;
      slot.mesh.scaling.setAll(.7 + Math.sin(t * Math.PI) * .5);
      if (t >= 1) { slot.duration = 0; slot.mesh.setEnabled(false); }
    }
    for (const ring of this.rings) {
      if (ring.duration <= 0) continue;
      ring.age += deltaSeconds; ring.material.alpha = Math.max(0, 1 - ring.age / ring.duration);
      if (ring.age >= ring.duration) { ring.duration = 0; ring.mesh.setEnabled(false); }
    }
  }
  reset(): void {
    if (this.disposed) return;
    for (const slot of this.bursts) { slot.system.stop(); slot.system.reset(); slot.lifetime = 0; }
    for (const slot of this.energy) { slot.duration = 0; slot.mesh.setEnabled(false); }
    for (const ring of this.rings) { ring.duration = 0; ring.mesh.setEnabled(false); }
  }
  stats(): { capacity: number; active: number; motes: number } {
    return { capacity: this.disposed ? 0 : this.bursts.length * 40,
      active: this.bursts.reduce((n, p) => n + p.system.getActiveCount(), 0),
      motes: this.energy.filter(p => p.duration > 0).length };
  }
  dispose(): void {
    if (this.disposed) return; this.disposed = true;
    for (const slot of this.bursts) slot.system.dispose(false);
    for (const slot of this.energy) slot.mesh.dispose(false, false);
    for (const ring of this.rings) { ring.mesh.dispose(false, false); ring.material.dispose(false, false); }
    this.energySource.dispose(false, false); this.energyMaterial.dispose(false, false); this.texture.dispose();
    this.bursts.length = 0; this.energy.length = 0; this.rings.length = 0; managers.delete(this.scene);
  }
}
function manager(scene: Scene): ParticleManager { return managers.get(scene) ?? new ParticleManager(scene); }
export function createBurstEffect(scene: Scene, position: Vector3, color: Color4, count = 20, lifetime = .8): ParticleEffectHandle {
  return manager(scene).spawnBurst(position, color, count, lifetime);
}
export function createAbsorptionEffect(scene: Scene, from: Vector3, to: Vector3, color: Color4, duration = .5): ParticleEffectHandle {
  return manager(scene).spawnAbsorption(from, to, color, duration);
}
export function createSelectionRing(scene: Scene, position: Vector3, color: Color4, radius = .5): ParticleEffectHandle {
  return manager(scene).spawnSelectionRing(position, color, radius);
}
export function createTrailEffect(scene: Scene, color: Color4): { update(position: Vector3): void; dispose(): void } {
  let handle: ParticleEffectHandle | undefined;
  return { update(position) { handle?.dispose(); handle = manager(scene).spawnBurst(position, color, 3, .2); }, dispose() { handle?.dispose(); } };
}
