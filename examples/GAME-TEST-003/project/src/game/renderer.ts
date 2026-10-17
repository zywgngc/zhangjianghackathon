import { Engine, Scene, ArcRotateCamera, HemisphericLight, DirectionalLight, Vector3, Color4, MeshBuilder,
  StandardMaterial, Color3, Mesh, Matrix, DynamicTexture } from '@babylonjs/core';
import type { Simulation } from './Simulation';
import type { Snapshot, Entity, EngineInfo } from './types';
import { createMeshForEntity, updateMeshFromEntity, setEntityThreat, disposeAllMeshes, disposeEntityMesh, disposeMeshAssets,
  type EntityMeshMap, type EntityMeshEntry } from './meshes';
import { ParticleManager } from './particles';

export interface SceneHandle {
  resize(): void; dispose(): void; engineInfo(): EngineInfo;
  projectEntity(id: string): { x: number; y: number } | null;
}
interface Label { element: HTMLElement; line: HTMLElement; value: HTMLElement; name: HTMLElement; power: number; pulseAt: number; }
interface Fading { entry: EntityMeshEntry; born: number; }
interface Floater { element: HTMLElement; x: number; y: number; z: number; born: number; }

/** Same production view for live and isolated simulations. Never advances gameplay. */
export function createScene(container: HTMLElement, simulation: Simulation): SceneHandle {
  const existingCanvas = container instanceof HTMLCanvasElement;
  const host = existingCanvas ? container.parentElement ?? document.body : container;
  const originalPosition = host.style.position;
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  const canvas = existingCanvas ? container : document.createElement('canvas');
  Object.assign(canvas.style, { width: '100%', height: '100%', display: 'block', touchAction: 'none' });
  canvas.dataset.renderer = 'babylon';
  canvas.setAttribute('aria-label', '百炼英雄三维战场，点击敌人或武器选择目标');
  if (!existingCanvas) host.appendChild(canvas);
  const overlay = document.createElement('div');
  overlay.dataset.powerLabels = 'production';
  Object.assign(overlay.style, { position: 'absolute', inset: '0', overflow: 'hidden', pointerEvents: 'none', zIndex: '2' });
  host.appendChild(overlay);
  const engine = new Engine(canvas, true, { stencil: false, preserveDrawingBuffer: false });
  engine.setHardwareScalingLevel(1 / Math.min(1.5, window.devicePixelRatio || 1));
  const scene = new Scene(engine);
  scene.clearColor = new Color4(.31, .41, .42, 1);
  const camera = new ArcRotateCamera('isometric_follow_camera', Math.PI / 4, Math.PI / 3.2, 26, Vector3.Zero(), scene);
  camera.inputs.clear(); camera.minZ = .1; camera.maxZ = 200; camera.fov = .95;
  const light = new HemisphericLight('fantasy_daylight', new Vector3(.3, 1, .2), scene);
  light.intensity = .95; light.groundColor.set(.22, .25, .23);
  const sun = new DirectionalLight('soft_sun', new Vector3(-.5, -1, -.3), scene); sun.intensity = .6;
  const ground = MeshBuilder.CreateGround('signed_centered_map', { width: 1, height: 1 }, scene);
  const groundMaterial = new StandardMaterial('environment_ground', scene); groundMaterial.specularColor.set(0, 0, 0);
  ground.material = groundMaterial; ground.position.y = -.025; ground.isPickable = true;
  const sceneryMaterial = new StandardMaterial('environment_scenery', scene);
  const detailMaterial = new StandardMaterial('camp_cloth_or_torch_flame', scene);
  const scenery: Mesh[] = [];
  let groundTexture: DynamicTexture | null = null;
  const meshMap: EntityMeshMap = new Map();
  const labels = new Map<string, Label>();
  const fading: Fading[] = [], floaters: Floater[] = [];
  const particles = new ParticleManager(scene);
  const active = new Set<string>();
  const world = Vector3.Zero(), identity = Matrix.Identity(), desiredTarget = Vector3.Zero();
  const green = Color3.FromHexString('#50DC78'), red = Color3.FromHexString('#FF5966');
  const config = simulation.config();
  const enemyNames = new Map(config.enemies.map(e => [e.kind, e.name]));
  let level = -1, lastGeneration = -1, lastSequence = 0, lastTime = -1, visualTime = 0;
  let disposed = false, frames = 0, pointer: { id: number; x: number; y: number } | null = null;

  function environment(snapshot: Snapshot): void {
    if (snapshot.level === level) return;
    level = snapshot.level;
    const cfg = config.levels.find(row => row.id === level) ?? config.levels[0];
    ground.scaling.set(cfg.mapWidth, 1, cfg.mapHeight);
    ground.position.x = 0; ground.position.z = 0;
    groundTexture?.dispose();
    groundTexture = new DynamicTexture('terrain_' + cfg.theme, 256, scene, false);
    const ctx = groundTexture.getContext();
    const forest = cfg.theme === 'forest', desert = cfg.theme === 'desert';
    const colors = forest ? ['#70A768', '#78AC6D', '#6CA365'] : desert ? ['#C7AA73', '#D0B67D', '#C4A970'] : ['#666C86', '#737A94', '#5D657E'];
    for (let x = 0; x < 16; x++) for (let y = 0; y < 16; y++) {
      ctx.fillStyle = colors[(x * 7 + y * 11) % 3]; ctx.fillRect(x * 16, y * 16, 16, 16);
    }
    groundTexture.update(); groundTexture.uScale = 4; groundTexture.vScale = 4; groundMaterial.diffuseTexture = groundTexture;
    scene.clearColor.set(forest ? .46 : desert ? .72 : .25, forest ? .65 : desert ? .64 : .29, forest ? .63 : desert ? .48 : .4, 1);
    for (const mesh of scenery) mesh.dispose(false, false); scenery.length = 0;
    sceneryMaterial.diffuseColor.set(forest ? .5 : desert ? .7 : .34, forest ? .36 : desert ? .59 : .38, forest ? .22 : desert ? .41 : .5);
    for (let side = -1; side <= 1; side += 2) {
      for (let i = 0; i < 8; i++) {
        const fence = MeshBuilder.CreateBox('boundary_structure', { width: forest ? .12 : .7, height: forest ? .8 : 1.2, depth: 2.2 }, scene);
        fence.position.set(side * (cfg.mapWidth / 2 + .4), forest ? .4 : .6, -cfg.mapHeight / 2 + (i + .5) * cfg.mapHeight / 8);
        fence.material = sceneryMaterial; fence.isPickable = false; scenery.push(fence);
      }
    }
    if (forest) {
      detailMaterial.diffuseColor.set(.78, .68, .43); detailMaterial.emissiveColor.set(0, 0, 0);
      for (const x of [-cfg.mapWidth / 3, cfg.mapWidth / 3]) {
        const tent = MeshBuilder.CreateCylinder('fantasy_camp_tent', {height:2.6,diameter:2.5,tessellation:3}, scene);
        tent.rotation.x = Math.PI / 2; tent.position.set(x, .65, cfg.mapHeight / 2 - 2);
        tent.material = detailMaterial; tent.isPickable = false; scenery.push(tent);
      }
    } else if (!desert) {
      detailMaterial.diffuseColor.set(1, .53, .12); detailMaterial.emissiveColor.set(1, .38, .05);
      for (const x of [-cfg.mapWidth / 2 + .8, cfg.mapWidth / 2 - .8]) for (const z of [-cfg.mapHeight / 3, cfg.mapHeight / 3]) {
        const pole = MeshBuilder.CreateCylinder('castle_torch_pole', {height:2,diameter:.15,tessellation:6}, scene);
        pole.position.set(x, 1, z); pole.material = sceneryMaterial; pole.isPickable = false; scenery.push(pole);
        const flame = MeshBuilder.CreateCylinder('castle_torch_flame', {height:.6,diameterBottom:.38,diameterTop:0,tessellation:5}, scene);
        flame.position.set(x, 2.2, z); flame.material = detailMaterial; flame.isPickable = false; scenery.push(flame);
      }
    }
    resize();
  }
  function projection(x: number, y: number, z: number): { x: number; y: number } | null {
    if (disposed || frames === 0) return null;
    camera.getViewMatrix(true); scene.updateTransformMatrix(true);
    world.set(x, y, z);
    const width = engine.getRenderWidth(), height = engine.getRenderHeight();
    const point = Vector3.Project(world, identity, scene.getTransformMatrix(), camera.viewport.toGlobal(width, height));
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.z < 0 || point.z > 1 ||
      point.x < 0 || point.x > width || point.y < 0 || point.y > height) return null;
    const rect = canvas.getBoundingClientRect();
    return { x: rect.left + point.x / width * rect.width, y: rect.top + point.y / height * rect.height };
  }
  function entityName(entity: Entity): string {
    if (entity.kind === 'hero') return '英雄';
    if (entity.kind === 'boss') return config.levels.find(l => l.id === level)?.boss.name ?? '首领';
    if (entity.kind === 'weapon') return '武器';
    return entity.name ?? enemyNames.get(entity.archetype ?? entity.kind) ?? '敌人';
  }
  function createLabel(entity: Entity): Label {
    const button = entity.kind !== 'hero';
    const element = document.createElement(button ? 'button' : 'div');
    element.dataset.entityId = entity.id; element.dataset.kind = entity.kind; element.dataset.powerLabel = '';
    if (element instanceof HTMLButtonElement) {
      element.type = 'button';
      element.addEventListener('click', event => { event.stopPropagation(); choose(entity.id); });
    }
    Object.assign(element.style, { position: 'absolute', transform: 'translate(-50%,-100%)', fontFamily: 'system-ui,sans-serif',
      fontWeight: '800', textAlign: 'center', color: '#FFFFFF', background: '#1D2C42', border: '2px solid #8EB1D3',
      borderRadius: '7px', padding: '3px 7px', minWidth: '36px', lineHeight: '1.05', whiteSpace: 'nowrap',
      boxShadow: '0 2px 4px #14213288', pointerEvents: button ? 'auto' : 'none', cursor: button ? 'pointer' : 'default' });
    const line = document.createElement('div');
    Object.assign(line.style, {position:'absolute',height:'1px',background:'#DBE8F9A8',transformOrigin:'0 0',pointerEvents:'none',zIndex:'0'});
    element.style.zIndex = '1';
    const name = document.createElement('span'), value = document.createElement('strong');
    Object.assign(name.style, { display: 'block', fontSize: '10px', fontWeight: '500', color: '#EEF2F7' });
    Object.assign(value.style, { display: 'block', fontSize: '19px', fontWeight: '900' });
    element.append(name, value); overlay.append(line, element);
    const label = { element, line, value, name, power: -1, pulseAt: -100 };
    labels.set(entity.id, label); return label;
  }
  function choose(id: string): void {
    const snapshot = simulation.getRenderState();
    if (snapshot.phase !== 'PLAYING' || snapshot.interactionLock) return;
    const entity = snapshot.entities.find(row => row.id === id && row.active);
    if (!entity || entity.group === 'obstacle') return;
    simulation.dispatch({ type: 'selectTarget', id });
    if (simulation.getRenderState().targetId === id)
      particles.spawnSelectionRing(new Vector3(entity.x, .02, entity.z), entity.kind === 'weapon' ? new Color4(1, .83, .25, 1) : new Color4(.3, 1, .5, 1));
  }
  function floating(text: string, x: number, y: number, z: number, color = '#FFF1A5'): void {
    if (floaters.length >= 12) floaters.shift()!.element.remove();
    const element = document.createElement('div'); element.textContent = text;
    Object.assign(element.style, { position: 'absolute', font: '900 22px system-ui,sans-serif', color,
      textShadow: '0 2px 3px #16213E', whiteSpace: 'nowrap', transform: 'translate(-50%,-100%)' });
    overlay.append(element); floaters.push({ element, x, y, z, born: visualTime });
  }
  function removeTransient(): void {
    particles.reset();
    for (const item of fading) disposeEntityMesh(item.entry); fading.length = 0;
    for (const item of floaters) item.element.remove(); floaters.length = 0;
    for (const label of labels.values()) { label.element.remove(); label.line.remove(); } labels.clear();
    disposeAllMeshes(meshMap); lastSequence = 0;
  }
  function sync(snapshot: Snapshot): void {
    active.clear();
    const hero: Entity = { id: 'player', kind: 'hero', x: snapshot.player.x, y: snapshot.player.y, z: snapshot.player.z,
      power: snapshot.player.power, hp: snapshot.player.hp, active: true };
    const all = [hero, ...snapshot.entities];
    const placed: { x: number; y: number; width: number; height: number }[] = [];
    const hostRect = host.getBoundingClientRect();
    for (const entity of all) {
      if (!entity.active) continue;
      active.add(entity.id);
      const enemy = entity.kind === 'enemy' || entity.kind === 'boss' || entity.group === 'enemy' || entity.group === 'boss';
      const color = entity.power < snapshot.player.power ? green : red;
      let entry = meshMap.get(entity.id);
      if (!entry) { entry = createMeshForEntity(scene, entity, color); meshMap.set(entity.id, entry); }
      const paused = snapshot.phase === 'PAUSED' || snapshot.phase === 'MENU';
      if (!paused || entity.kind !== 'hero') updateMeshFromEntity(entry, entity, visualTime, entity.kind === 'hero' ? {
        facing: snapshot.player.facing ?? snapshot.player.rotationY ?? 0, animation: snapshot.player.animation ?? 'idle',
        attackProgress: 1 - snapshot.attackTimer / config.attackDuration, victory: snapshot.phase === 'VICTORY' } : {});
      if (enemy) setEntityThreat(entry, color);
      if (entity.kind === 'hero' || enemy || entity.kind === 'weapon') {
        const label = labels.get(entity.id) ?? createLabel(entity);
        const number = entity.kind === 'weapon' ? entity.bonus ?? entity.power : entity.power;
        if (label.power !== number) {
          if (label.power >= 0 && entity.kind === 'hero' && number > label.power) label.pulseAt = visualTime;
          label.power = number; label.value.textContent = (entity.kind === 'weapon' ? '+' : '') + String(number);
        }
        label.name.textContent = entityName(entity);
        label.element.dataset.power = String(number);
        const threat = entity.kind === 'hero' ? 'hero' : entity.kind === 'weapon' ? 'weapon' : entity.power < snapshot.player.power ? 'lower' : 'equal-or-higher';
        label.element.dataset.threat = threat;
        const badge = threat === 'hero' ? '#315DAD' : threat === 'weapon' ? '#7A5A19' : threat === 'lower' ? '#226642' : '#9D3449';
        label.element.style.background = badge;
        label.element.style.borderColor = threat === 'hero' ? '#95BFFF' : threat === 'weapon' ? '#FFE282' : threat === 'lower' ? '#64F29A' : '#FF8390';
        label.value.style.color = threat === 'lower' ? '#86FFAD' : threat === 'equal-or-higher' ? '#FFBEC7' : '#FFFFFF';
        label.element.setAttribute('aria-label', entityName(entity) + ' 战力 ' + (entity.kind === 'weapon' ? '+' : '') + number);
        const point = projection(entry.root.position.x, entry.root.position.y + entry.labelHeight * entry.scale + .2, entry.root.position.z);
        label.element.hidden = !point; label.line.hidden = !point;
        if (point) {
          const ax = point.x - hostRect.left, ay = point.y - hostRect.top;
          const pulse = Math.max(0, 1 - (visualTime - label.pulseAt) / .4), scale = 1 + pulse * .16;
          const width = Math.max(54, label.element.offsetWidth) * scale, height = Math.max(35, label.element.offsetHeight) * scale;
          let x = Math.max(width / 2 + 4, Math.min(hostRect.width - width / 2 - 4, ax));
          let y = Math.max(height + 4, Math.min(hostRect.height - 4, ay));
          let best = Infinity;
          for (let row = -4; row <= 4; row++) for (let col = -3; col <= 3; col++) {
            const cx = Math.max(width / 2 + 4, Math.min(hostRect.width - width / 2 - 4, ax + col * (width + 6)));
            const cy = Math.max(height + 4, Math.min(hostRect.height - 4, ay + row * (height + 6)));
            if (placed.some(p => Math.abs(p.x - cx) < (p.width + width) / 2 + 3 &&
              cy - height < p.y + 3 && cy + 3 > p.y - p.height)) continue;
            const cost = (cx - ax) ** 2 + (cy - ay) ** 2 * 1.1;
            if (cost < best) { best = cost; x = cx; y = cy; }
          }
          placed.push({ x, y, width, height }); label.element.style.left = x + 'px'; label.element.style.top = y + 'px';
          label.element.style.transform = 'translate(-50%,-100%) scale(' + scale + ')';
          const dx = x - ax, dy = y - ay, length = Math.hypot(dx, dy);
          label.line.hidden = length < 4;
          label.line.style.left = ax + 'px'; label.line.style.top = ay + 'px';
          label.line.style.width = length + 'px'; label.line.style.transform = 'rotate(' + Math.atan2(dy, dx) + 'rad)';
        }
      }
    }
    for (const [id, entry] of meshMap) if (!active.has(id)) {
      labels.get(id)?.element.remove(); labels.get(id)?.line.remove(); labels.delete(id);
      if (entry.root.metadata?.kind === 'enemy' || entry.root.metadata?.kind === 'boss') {
        if (fading.length >= 24) disposeEntityMesh(fading.shift()!.entry);
        for (const child of entry.children) child.isPickable = false;
        fading.push({ entry, born: visualTime });
      } else disposeEntityMesh(entry);
      meshMap.delete(id);
    }
  }
  function events(snapshot: Snapshot): void {
    for (const event of simulation.events(lastSequence)) {
      const data = event.data, id = String(data.enemyId ?? data.entityId ?? data.bossId ?? data.targetId ?? '');
      const entity = snapshot.entities.find(row => row.id === id);
      const from = new Vector3(entity?.x ?? Number(data.fromX ?? snapshot.player.x), .7, entity?.z ?? Number(data.fromZ ?? snapshot.player.z));
      const to = new Vector3(snapshot.player.x, .9, snapshot.player.z);
      if (event.type === 'enemy_defeated') {
        particles.spawnBurst(from, new Color4(1, .75, .3, 1), 18, .4);
        particles.spawnAbsorption(from, to, new Color4(.4, 1, .6, 1), config.absorptionDuration);
      } else if (event.type === 'power_absorbed' || event.type === 'WEAPON_PICKUP') {
        const bonus = event.type === 'WEAPON_PICKUP' ? Number(data.bonus ?? 0) : Number(data.absorbedPower ?? 0);
        if (bonus > 0) floating('+' + bonus, snapshot.player.x, 2.8, snapshot.player.z);
        particles.spawnBurst(to, new Color4(.5, 1, .6, 1), 16, .45);
      } else if (event.type === 'combat_invalid_warning' || event.type === 'NAV_UNREACHABLE') {
        particles.spawnSelectionRing(from, new Color4(1, .15, .22, 1));
        floating(String(data.message ?? '战力不足'), from.x, 2.2, from.z, '#FF8B95');
      } else if (event.type === 'boss_defeated') particles.spawnBurst(from, new Color4(1, .55, .1, 1), 40, .8);
      lastSequence = event.seq;
    }
  }
  function render(): void {
    if (disposed) return;
    const snapshot = simulation.getRenderState(), generation = simulation.eventCursor().generation;
    const reset = generation !== lastGeneration;
    if (reset) { removeTransient(); lastTime = snapshot.simTime; lastGeneration = generation; }
    const delta = Math.max(0, snapshot.simTime - lastTime);
    const ending = snapshot.phase === 'VICTORY', dt = delta || (ending ? Math.min(.05, engine.getDeltaTime() / 1000) : 0);
    visualTime += dt;
    environment(snapshot);
    desiredTarget.set(snapshot.player.x, .7, snapshot.player.z);
    if (reset || Vector3.DistanceSquared(camera.target, desiredTarget) > 64) camera.target.copyFrom(desiredTarget);
    else if (snapshot.phase === 'PLAYING') Vector3.LerpToRef(camera.target, desiredTarget,
      1 - Math.exp(-Math.min(.05, engine.getDeltaTime() / 1000) * 12), camera.target);
    camera.getViewMatrix(true); scene.updateTransformMatrix(true);
    particles.update(dt);
    sync(snapshot);
    events(snapshot);
    for (let i = fading.length - 1; i >= 0; i--) {
      const item = fading[i], t = (visualTime - item.born) / .45;
      if (t >= 1) { disposeEntityMesh(item.entry); fading.splice(i, 1); }
      else { item.entry.root.rotation.z = t * 1.4; item.entry.root.scaling.setAll(item.entry.scale * Math.max(.01, 1 - t)); }
    }
    const rect = host.getBoundingClientRect();
    for (let i = floaters.length - 1; i >= 0; i--) {
      const item = floaters[i], age = visualTime - item.born, point = projection(item.x, item.y + age, item.z);
      if (age > .9) { item.element.remove(); floaters.splice(i, 1); }
      else { item.element.hidden = !point; if (point) { item.element.style.left = point.x - rect.left + 'px'; item.element.style.top = point.y - rect.top + 'px'; item.element.style.opacity = String(1 - age / .9); } }
    }
    scene.render(); frames++; lastTime = snapshot.simTime;
  }
  function resize(): void {
    if (disposed) return;
    engine.resize(); camera.radius = engine.getRenderWidth() < engine.getRenderHeight() ? 27 : 23;
  }
  function down(event: PointerEvent): void {
    if (!event.isPrimary || event.button !== 0) return;
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY }; canvas.setPointerCapture(event.pointerId);
  }
  function up(event: PointerEvent): void {
    const start = pointer; pointer = null;
    if (!start || event.pointerId !== start.id || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 14) return;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (simulation.getRenderState().phase !== 'PLAYING' || simulation.getRenderState().interactionLock) return;
    const rect = canvas.getBoundingClientRect();
    // Babylon Scene.pick consumes CSS-space canvas coordinates, applying hardware scaling itself.
    const pick = scene.pick((event.clientX - rect.left) * canvas.clientWidth / rect.width,
      (event.clientY - rect.top) * canvas.clientHeight / rect.height,
      mesh => mesh === ground || !!mesh.metadata?.entityId && !mesh.metadata?.obstacle && mesh.metadata?.kind !== 'hero');
    const id = pick?.pickedMesh?.metadata?.entityId;
    if (pick?.hit && typeof id === 'string') choose(id);
    else if (pick?.hit && pick.pickedMesh === ground && pick.pickedPoint)
      simulation.dispatch({ type: 'selectTarget', ground: { x: pick.pickedPoint.x, z: pick.pickedPoint.z } });
  }
  function cancel(): void { pointer = null; }
  function wheel(event: WheelEvent): void {
    event.preventDefault(); camera.radius = Math.max(16, Math.min(42, camera.radius + Math.sign(event.deltaY) * 1.3));
  }
  canvas.addEventListener('pointerdown', down); canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', cancel);
  canvas.addEventListener('wheel', wheel, { passive: false });
  window.addEventListener('resize', resize);
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize); observer?.observe(host);
  resize(); engine.runRenderLoop(render);

  return {
    resize,
    projectEntity(id) {
      if (disposed) return null;
      const entry = meshMap.get(id === '__hero__' ? 'player' : id);
      if (!entry || !entry.root.isEnabled()) return null;
      return projection(entry.root.position.x, entry.root.position.y + entry.labelHeight * entry.scale * .45, entry.root.position.z);
    },
    engineInfo(): EngineInfo {
      const stats = particles.stats();
      return { name: 'babylon', version: Engine.Version, running: !disposed && frames > 0 && scene.isReady(),
        resources: { meshes: scene.meshes.length, geometries: scene.geometries.length, materials: scene.materials.length,
          textures: scene.textures.length, particleSystems: scene.particleSystems.length, particleCapacity: stats.capacity,
          activeParticles: stats.active, energyMotes: stats.motes, labels: labels.size, floaters: floaters.length,
          retiring: fading.length, renderLoops: engine.activeRenderLoops.length, canvases: canvas.isConnected ? 1 : 0 } };
    },
    dispose() {
      if (disposed) return; disposed = true; engine.stopRenderLoop(render);
      canvas.removeEventListener('pointerdown', down); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('pointercancel', cancel);
      canvas.removeEventListener('wheel', wheel); window.removeEventListener('resize', resize); observer?.disconnect();
      removeTransient(); particles.dispose();
      for (const mesh of scenery) mesh.dispose(false, false); scenery.length = 0;
      disposeMeshAssets(scene); groundTexture?.dispose(); ground.dispose(false, false);
      groundMaterial.dispose(false, false); sceneryMaterial.dispose(false, false); detailMaterial.dispose(false, false);
      scene.dispose(); engine.dispose(); overlay.remove(); if (!existingCanvas) canvas.remove();
      host.style.position = originalPosition;
    },
  };
}
