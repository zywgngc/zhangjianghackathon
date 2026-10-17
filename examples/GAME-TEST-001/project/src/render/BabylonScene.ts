import * as BABYLON from '@babylonjs/core';
import { MeshFactory } from './MeshFactory';
import { ParticleSystemManager } from './ParticleSystem';
import { CameraRig } from './CameraRig';
import type { Simulation, Snapshot } from '../game/Simulation';
import type { Entity, Vec3, EngineInfo, ProjectedCoord } from '../game/types';

export interface RendererEngineInfo extends EngineInfo {
  disposed: boolean;
  resources: {
    meshes: number; materials: number; textures: number; geometries: number;
    particleSystems: number; particleCapacity: number; activeParticles: number;
    activeMeshes: number; renderedSoldiers: number; renderedProjectiles: number;
    renderedEntities: number; retiringMeshes: number; renderLoops: number; canvases: number;
  };
  frames: number;
  dimensions: { width: number; height: number };
  camera: { x: number; y: number; z: number } | null;
}
export interface SceneHandle {
  resize(): void;
  dispose(): void;
  engineInfo(): RendererEngineInfo;
  projectEntity(id: string): ProjectedCoord | null;
}
interface Visual {
  mesh: BABYLON.AbstractMesh; kind: Entity['kind']; born: number; hit: number; shot: number;
}
interface Retiring { mesh: BABYLON.AbstractMesh; born: number; }

/** Read-only production view. The renderer never dispatches or advances Simulation. */
export function createScene(container: HTMLElement, simulation: Simulation): SceneHandle {
  const canvas = document.createElement('canvas');
  canvas.dataset.renderer = 'babylon';
  Object.assign(canvas.style, { width: '100%', height: '100%', display: 'block', touchAction: 'none' });
  canvas.setAttribute('aria-label', '数字门枪手三维战场');
  container.appendChild(canvas);
  const engine = new BABYLON.Engine(canvas, true, { stencil: false, preserveDrawingBuffer: false, antialias: true });
  // Cap costly mobile high-DPI buffers; leave CSS dimensions and game state intact.
  engine.setHardwareScalingLevel(1 / Math.min(1.5, window.devicePixelRatio || 1));
  const scene = new BABYLON.Scene(engine);
  scene.clearColor.set(.74, .79, .72, 1);
  scene.ambientColor.set(.25, .25, .22);
  scene.fogMode = BABYLON.Scene.FOGMODE_LINEAR;
  scene.fogStart = 75; scene.fogEnd = 150;
  scene.fogColor.set(.74, .79, .72);
  const hemisphere = new BABYLON.HemisphericLight('daylight', new BABYLON.Vector3(0, 1, 0), scene);
  hemisphere.intensity = .9;
  const sunlight = new BABYLON.DirectionalLight('sun', new BABYLON.Vector3(-.6, -1, .6), scene);
  sunlight.intensity = .65;
  const factory = new MeshFactory(scene);
  const particles = new ParticleSystemManager(scene);
  const camera = new CameraRig(scene, canvas);
  const anchor = new BABYLON.Mesh('squad_center_anchor', scene);
  camera.setTarget(anchor);

  const ground = BABYLON.MeshBuilder.CreateGround('battlefield_terrain', { width: 80, height: 1 }, scene);
  const road = BABYLON.MeshBuilder.CreateGround('road_12_units', { width: 12, height: 1 }, scene);
  const terrainMaterial = factory.material('terrain', '#C6B47B');
  const roadMaterial = factory.material('road', '#998D65');
  ground.material = terrainMaterial; road.material = roadMaterial; road.position.y = .015;
  const edgeMaterial = factory.material('road_edges', '#DDD3A6', .1);
  const edges = [-6, 6].map(x => {
    const edge = BABYLON.MeshBuilder.CreateGround('road_boundary_' + x, { width: .12, height: 1 }, scene);
    edge.position.x = x; edge.position.y = .025; edge.material = edgeMaterial; return edge;
  });
  const decor: BABYLON.Mesh[] = [];
  const visuals = new Map<string, Visual>();
  const retiring: Retiring[] = [];
  const activeIds = new Set<string>();
  let level = -1, disposed = false, frames = 0;
  let lastSeq = 0, lastSimTime = -1, lastTicks = -1, lastPhase = '', lastGeneration = -1;
  let visualTime = 0;
  const worldPoint = BABYLON.Vector3.Zero();
  const identity = BABYLON.Matrix.Identity();

  function environment(snapshot: Snapshot): void {
    if (level === snapshot.level) return;
    level = snapshot.level;
    const config = simulation.config().levels.find(l => l.level === level);
    const length = (config?.trackLength ?? 360) + 70;
    for (const mesh of [ground, road, ...edges]) {
      mesh.scaling.z = length; mesh.position.z = length / 2 - 25;
    }
    terrainMaterial.diffuseColor.copyFrom(BABYLON.Color3.FromHexString(level === 1 ? '#C6B47B' : level === 2 ? '#89896B' : '#666E64'));
    roadMaterial.diffuseColor.copyFrom(BABYLON.Color3.FromHexString(level === 1 ? '#998D65' : level === 2 ? '#7B7860' : '#687278'));
    for (const mesh of decor) factory.release(mesh);
    decor.length = 0;
    // Side scenery is outside the valid 12-unit gameplay road; never collidable.
    for (let z = 10, i = 0; z < length; z += 28, i++) {
      const mesh = factory.createDecoration(i % 5 === 4 ? 'tower' : 'crate');
      mesh.position.set(i % 2 ? 9 : -9, 0, z);
      decor.push(mesh);
      if (level === 2) {
        const trench = factory.createObstacle('sandbag', scene);
        trench.position.set(i % 2 ? -8 : 8, 0, z + 10); decor.push(trench);
      }
    }
  }

  function createVisual(entity: Entity): Visual {
    let mesh: BABYLON.AbstractMesh;
    switch (entity.kind) {
      case 'soldier': mesh = factory.createSoldier(scene); break;
      case 'enemy': mesh = factory.createEnemy(scene); break;
      case 'boss': mesh = factory.createBoss(scene); break;
      case 'gate': mesh = factory.createGate(scene); break;
      case 'obstacle': mesh = factory.createObstacle(entity.obstacleType ?? entity.archetype ?? 'sandbag', scene); break;
      case 'projectile': mesh = factory.createProjectile(); break;
    }
    mesh.name = 'entity_' + entity.id;
    mesh.metadata = { ...mesh.metadata, entityId: entity.id, kind: entity.kind };
    const visual = { mesh, kind: entity.kind, born: visualTime, hit: -100, shot: -100 };
    visuals.set(entity.id, visual);
    return visual;
  }

  function clearTransient(): void {
    for (const old of retiring) factory.release(old.mesh);
    retiring.length = 0;
    // Re-created scene objects may reuse gameplay IDs after restart.
    for (const visual of visuals.values()) factory.release(visual.mesh);
    visuals.clear();
    lastSeq = 0;
    particles.reset();
  }

  function sync(snapshot: Snapshot, reset: boolean): void {
    if (reset) clearTransient();
    environment(snapshot);
    anchor.position.set(snapshot.player.x, snapshot.player.y, snapshot.player.z);
    camera.setFormationSize(snapshot.player.soldiers);
    activeIds.clear();
    for (const entity of snapshot.entities) {
      if (!entity.active || entity.consumed) continue;
      activeIds.add(entity.id);
      let visual = visuals.get(entity.id);
      if (visual && visual.kind !== entity.kind) { factory.release(visual.mesh); visuals.delete(entity.id); visual = undefined; }
      visual ??= createVisual(entity);
      const mesh = visual.mesh;
      mesh.position.set(entity.x, entity.y, entity.z);
      if (entity.kind === 'gate') {
        const n = snapshot.player.soldiers, op = entity.operator ?? '+', operand = entity.operand ?? 0;
        const raw = op === '+' ? n + operand : op === '-' ? n - operand : op === '×' ? n * operand : operand > 0 ? Math.floor(n / operand) : n;
        const result = Math.max(0, Math.min(200, Math.floor(raw)));
        factory.updateGate(mesh as BABYLON.Mesh, op, operand, n, result);
      }
      if (entity.kind === 'projectile') mesh.rotation.y = Math.atan2(entity.vx ?? 0, entity.vz ?? 1);
      mesh.setEnabled(true);
    }
    for (const [id, visual] of visuals) {
      if (activeIds.has(id)) continue;
      if (visual.kind === 'soldier' || visual.kind === 'enemy' || visual.kind === 'boss') {
        if (retiring.length >= 200) factory.release(retiring.shift()!.mesh);
        retiring.push({ mesh: visual.mesh, born: visualTime });
      } else factory.release(visual.mesh);
      visuals.delete(id);
    }
  }

  function eventPosition(data: Record<string, unknown>, snapshot: Snapshot): Vec3 {
    if (typeof data.x === 'number' && typeof data.z === 'number')
      return { x: data.x, y: typeof data.y === 'number' ? data.y : .7, z: data.z };
    const id = typeof data.targetId === 'string' ? data.targetId : typeof data.entityId === 'string' ? data.entityId : '';
    const visual = visuals.get(id);
    if (visual) return visual.mesh.position;
    return { x: snapshot.player.x, y: snapshot.player.y + .8, z: snapshot.player.z };
  }

  function events(snapshot: Snapshot): void {
    for (const event of simulation.events(lastSeq)) {
      const position = eventPosition(event.data, snapshot);
      const id = String(event.data.from ?? event.data.targetId ?? event.data.entityId ?? '');
      const visual = visuals.get(id);
      switch (event.type) {
        case 'shoot':
          particles.playMuzzle({ x: position.x + .2, y: position.y + .8, z: position.z + .6 });
          if (visual) visual.shot = visualTime;
          break;
        case 'damage':
          particles.playHit(position);
          if (visual) visual.hit = visualTime;
          break;
        case 'death': particles.playExplosion(position); break;
        case 'gate':
          if (event.data.isPositive === true || Number(event.data.newCount) > Number(event.data.oldCount)) particles.playGrowth(position);
          else if (Number(event.data.newCount) < Number(event.data.oldCount)) particles.playLoss(position);
          break;
        case 'collision': particles.playLoss(position); break;
        case 'victory': particles.playGrowth(position); break;
        case 'defeat': particles.playLoss(position); break;
      }
      lastSeq = event.seq;
    }
  }

  function animate(snapshot: Snapshot): void {
    if (snapshot.phase === 'PAUSED' || snapshot.phase === 'SETTINGS' || snapshot.phase === 'UPGRADE') return;
    const running = snapshot.phase === 'PLAYING';
    for (const entity of snapshot.entities) {
      const visual = visuals.get(entity.id);
      if (!visual) continue;
      const hit = Math.max(0, 1 - (visualTime - visual.hit) / .22);
      if (entity.kind === 'soldier' || entity.kind === 'enemy') {
        const recoil = Math.max(0, 1 - (visualTime - visual.shot) / .13);
        factory.animateHumanoid(visual.mesh, visualTime, running, recoil, hit);
        const spawn = Math.min(1, .65 + (visualTime - visual.born) * 2);
        visual.mesh.scaling.setAll(spawn);
        if (snapshot.phase === 'VICTORY') visual.mesh.position.y += Math.max(0, Math.sin(visualTime * 7 + visual.mesh.uniqueId)) * .15;
      } else if (entity.kind === 'boss') factory.updateBoss(visual.mesh, entity.hp, entity.maxHp ?? entity.hp, hit);
    }
    for (let i = retiring.length - 1; i >= 0; i--) {
      const old = retiring[i], progress = (visualTime - old.born) / .25;
      if (progress >= 1) { factory.release(old.mesh); retiring.splice(i, 1); }
      else { old.mesh.scaling.setAll(Math.max(.01, 1 - progress)); old.mesh.rotation.z = progress * 1.2; }
    }
  }

  function render(): void {
    if (disposed) return;
    const snapshot = simulation.getRenderState();
    const generation = simulation.eventCursor().generation;
    const ticks = snapshot.metrics.ticks ?? snapshot.metrics.updateLoops;
    const reset = (lastGeneration >= 0 && generation !== lastGeneration) || (lastSimTime >= 0 && snapshot.simTime < lastSimTime) || ticks < lastTicks || level !== snapshot.level ||
      (snapshot.phase === 'PLAYING' && lastPhase === 'MENU');
    const delta = lastSimTime < 0 || reset ? 0 : Math.max(0, snapshot.simTime - lastSimTime);
    // End-of-run flourishes may finish; pause/settings never advance visual animation.
    const ending = snapshot.phase === 'VICTORY' || snapshot.phase === 'DEFEAT';
    const visualDelta = delta || (ending ? Math.min(.05, engine.getDeltaTime() / 1000) : 0);
    visualTime += visualDelta;
    sync(snapshot, reset);
    events(snapshot);
    animate(snapshot);
    camera.update(visualDelta);
    particles.update(visualDelta);
    scene.render();
    frames++;
    lastSimTime = snapshot.simTime; lastTicks = ticks; lastPhase = snapshot.phase; lastGeneration = generation;
  }

  const resize = (): void => {
    if (disposed) return;
    engine.resize(); camera.adjustForOrientation();
  };
  engine.runRenderLoop(render);
  window.addEventListener('resize', resize);
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
  observer?.observe(container);
  resize();

  return {
    resize,
    engineInfo(): RendererEngineInfo {
      const p = particles.stats();
      return { name: 'babylon', version: BABYLON.Engine.Version, running: !disposed && frames > 0 && scene.isReady(),
        disposed, frames, dimensions: { width: disposed ? 0 : engine.getRenderWidth(), height: disposed ? 0 : engine.getRenderHeight() },
        camera: disposed || !scene.activeCamera ? null : { x: scene.activeCamera.position.x, y: scene.activeCamera.position.y, z: scene.activeCamera.position.z },
        resources: { meshes: scene.meshes.length, materials: scene.materials.length, textures: scene.textures.length,
          geometries: scene.geometries.length, particleSystems: scene.particleSystems.length, particleCapacity: p.capacity,
          activeParticles: p.activeParticles, activeMeshes: disposed ? 0 : scene.getActiveMeshes().length,
          renderedSoldiers: [...visuals.values()].filter(v => v.kind === 'soldier' && v.mesh.isEnabled()).length,
          renderedProjectiles: [...visuals.values()].filter(v => v.kind === 'projectile' && v.mesh.isEnabled()).length,
          renderedEntities: visuals.size, retiringMeshes: retiring.length,
          renderLoops: engine.activeRenderLoops.length, canvases: canvas.isConnected ? 1 : 0 } };
    },
    projectEntity(id: string): ProjectedCoord | null {
      if (disposed || !scene.activeCamera || frames === 0) return null;
      const mesh = id === 'player' ? anchor : visuals.get(id)?.mesh;
      if (!mesh || mesh.isDisposed() || !mesh.isEnabled()) return null;
      mesh.computeWorldMatrix(true);
      const world = mesh.getAbsolutePosition();
      const kind = visuals.get(id)?.kind;
      worldPoint.set(world.x, world.y + (kind === 'gate' ? 2.55 : kind === 'boss' ? 2 : id === 'player' || kind === 'soldier' || kind === 'enemy' ? .8 : .2), world.z);
      scene.activeCamera.getViewMatrix(true);
      scene.updateTransformMatrix(true);
      const width = engine.getRenderWidth(), height = engine.getRenderHeight();
      const point = BABYLON.Vector3.Project(worldPoint, identity, scene.getTransformMatrix(),
        scene.activeCamera.viewport.toGlobal(width, height));
      if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.z < 0 || point.z > 1 || point.x < 0 || point.y < 0 || point.x > width || point.y > height) return null;
      const rect = canvas.getBoundingClientRect();
      return { x: rect.left + point.x / width * rect.width, y: rect.top + point.y / height * rect.height };
    },
    dispose(): void {
      if (disposed) return;
      disposed = true;
      engine.stopRenderLoop(render);
      window.removeEventListener('resize', resize); observer?.disconnect();
      particles.dispose(); camera.dispose(); clearTransient();
      for (const mesh of decor) factory.release(mesh);
      decor.length = 0;
      anchor.dispose();
      factory.dispose();
      scene.dispose(); engine.dispose(); canvas.remove();
    },
  };
}
