import { Scene, Mesh, MeshBuilder, StandardMaterial, Color3, AbstractMesh, InstancedMesh } from '@babylonjs/core';
import type { Entity } from './types';

export interface EntityMeshEntry {
  root: Mesh; label?: Mesh; labelHeight: number; scale: number;
  parts: Record<string, AbstractMesh>; threat?: Mesh; children: AbstractMesh[];
}
export type EntityMeshMap = Map<string, EntityMeshEntry>;
class Assets {
  readonly materials = new Map<string, StandardMaterial>();
  readonly templates = new Map<string, Mesh>();
  private count = 0;
  constructor(readonly scene: Scene) {}
  mat(key: string, hex: string, glow = 0): StandardMaterial {
    let value = this.materials.get(key);
    if (!value) {
      value = new StandardMaterial(key, this.scene); value.diffuseColor = Color3.FromHexString(hex);
      value.emissiveColor = value.diffuseColor.scale(glow); value.specularColor.set(.16, .16, .18);
      this.materials.set(key, value);
    }
    return value;
  }
  part(root: Mesh, key: string, material: StandardMaterial, size: number[], pos: number[], shape = 'box'): InstancedMesh {
    let template = this.templates.get(key);
    if (!template) {
      template = shape === 'sphere' ? MeshBuilder.CreateSphere('source_' + key, { diameter: 1, segments: 6 }, this.scene)
        : shape === 'cone' ? MeshBuilder.CreateCylinder('source_' + key, { height: 1, diameterTop: 0, diameterBottom: 1, tessellation: 6 }, this.scene)
        : shape === 'cylinder' ? MeshBuilder.CreateCylinder('source_' + key, { height: 1, diameter: 1, tessellation: 8 }, this.scene)
        : MeshBuilder.CreateBox('source_' + key, { size: 1 }, this.scene);
      template.material = material; template.isVisible = false; template.isPickable = false;
      this.templates.set(key, template);
    }
    const instance = template.createInstance(key + '_' + (++this.count));
    instance.parent = root; instance.isVisible = true;
    instance.position.set(pos[0], pos[1], pos[2]); instance.scaling.set(size[0], size[1], size[2]);
    return instance;
  }
  dispose(): void {
    for (const mesh of this.templates.values()) mesh.dispose(false, false);
    for (const mat of this.materials.values()) mat.dispose(false, false);
    this.templates.clear(); this.materials.clear();
  }
}
const libraries = new WeakMap<Scene, Assets>();
function assets(scene: Scene): Assets {
  let value = libraries.get(scene);
  if (!value) { value = new Assets(scene); libraries.set(scene, value); scene.onDisposeObservable.addOnce(() => disposeMeshAssets(scene)); }
  return value;
}
export function disposeMeshAssets(scene: Scene): void { libraries.get(scene)?.dispose(); libraries.delete(scene); }

function scaleFor(entity: Entity): number {
  if (entity.kind === 'weapon') return 1;
  if (entity.kind === 'boss') return 1.65;
  if (entity.kind === 'hero') return .95 + Math.min(.18, Math.log2(Math.max(1, entity.power)) * .02);
  return entity.archetype === 'spider' ? 1.05 : entity.archetype === 'goblin' ? .8 : 1;
}

export function createMeshForEntity(scene: Scene, entity: Entity, color: Color3): EntityMeshEntry {
  const a = assets(scene), root = new Mesh('entity_' + entity.id, scene);
  const parts: Record<string, AbstractMesh> = {};
  const role = entity.kind === 'enemy' ? entity.archetype ?? 'goblin' : entity.kind;
  const metal = a.mat('steel', '#BCC9D3'), dark = a.mat('dark_steel', '#354259'), gold = a.mat('gold', '#DDBA50', .15);
  const bone = a.mat('bone', '#EBE3C8'), leather = a.mat('leather', '#75533F'), wood = a.mat('wood', '#775333');
  const skin = a.mat('human_skin', '#E4B78F');
  const make = (key: string, material: StandardMaterial, size: number[], pos: number[], shape = 'box') =>
    a.part(root, key, material, size, pos, shape);
  let height = 2;
  const obstacle = entity.group === 'obstacle' || ['tree', 'wall', 'rock', 'pillar'].includes(entity.kind);
  if (obstacle) {
    const w = entity.operand ?? 1, d = entity.bonus ?? 1;
    if (entity.kind === 'tree') {
      make('tree_trunk', wood, [w * .45, 2.6, d * .45], [0, 1.3, 0], 'cylinder');
      make('tree_canopy', a.mat('leaves', '#54864D'), [w * 1.7, 2.2, d * 1.7], [0, 3, 0], 'cone'); height = 4.1;
    } else if (entity.kind === 'rock') {
      const stone = make('rock', a.mat('rock', '#8B9993'), [w, .9, d], [0, .45, 0], 'sphere'); stone.rotation.y = .4;
      height = 1;
    } else if (entity.kind === 'pillar') {
      make('ruin_pillar', a.mat('ruin_stone', '#D0B67F'), [w, 3, d], [0, 1.5, 0], 'cylinder');
      make('pillar_cap', a.mat('ruin_stone', '#D0B67F'), [w * 1.2, .25, d * 1.2], [0, 3, 0]); height = 3.2;
    } else {
      make('wall', a.mat('wall_stone', '#7F8793'), [w, 1.6, d], [0, .8, 0]);
      make('wall_top', a.mat('wall_cap', '#A4ADB3'), [w, .12, d], [0, 1.66, 0]); height = 1.8;
    }
  } else if (role === 'weapon') {
    const blade = make('pickup_sword_blade', metal, [.17, 1.25, .09], [0, 1, 0]);
    make('pickup_guard', gold, [.56, .1, .16], [0, .4, 0]);
    make('pickup_grip', leather, [.13, .35, .13], [0, .2, 0]); parts.blade = blade; height = 1.9;
  } else if (role === 'spider') {
    make('spider_abdomen', a.mat('spider', '#5F3D65'), [.9, .45, 1.1], [0, .45, -.15], 'sphere');
    make('spider_head', a.mat('spider_head', '#352D45'), [.55, .4, .45], [0, .45, .55], 'sphere');
    const eye = a.mat('spider_eyes', '#E95C50', .5);
    for (const x of [-.16, .16]) make('spider_eye', eye, [.11, .11, .11], [x, .52, .76], 'sphere');
    for (let i = 0; i < 8; i++) {
      const leg = make('spider_leg', a.mat('spider_legs', '#3D324B'), [.08, .08, 1.05], [i < 4 ? -.62 : .62, .25, (i % 4 - 1.5) * .3]);
      leg.rotation.y = (i < 4 ? -1 : 1) * (1 + (i % 4 - 1.5) * .25); parts['leg' + i] = leg;
    }
    height = 1.2;
  } else {
    const boss = role === 'boss';
    const archetype = boss ? (entity.id.includes('B01') ? 'troll' : entity.id.includes('B02') ? 'guardian' : 'demon_guard') : role;
    const skeleton = archetype === 'skeleton';
    const cloth = a.mat('cloth_' + archetype, archetype === 'hero' ? '#4875B9' : archetype === 'dark_knight' ? '#43405F' :
      archetype === 'demon_guard' ? '#9A3D56' : archetype === 'guardian' ? '#8E9EB0' : archetype === 'orc' ? '#79914A' : archetype === 'troll' ? '#758B4E' : '#6CA66B');
    const face = skeleton ? bone : archetype === 'hero' ? skin : cloth;
    const torso = make('torso_' + archetype, skeleton ? bone : cloth, [boss ? .9 : .58, .7, .35], [0, .95, 0], skeleton ? 'cylinder' : 'box');
    parts.torso = torso;
    make('head_' + archetype, face, [.44, .47, .4], [0, 1.62, 0], 'sphere');
    const leftLeg = make('leg_' + archetype, skeleton ? bone : dark, [.18, .65, .22], [-.18, .35, 0]);
    const rightLeg = make('leg_' + archetype, skeleton ? bone : dark, [.18, .65, .22], [.18, .35, 0]);
    const leftArm = make('arm_' + archetype, face, [.17, .62, .2], [-.41, 1, .02]);
    const rightArm = make('arm_' + archetype, face, [.17, .62, .2], [.41, 1, .02]);
    Object.assign(parts, { leftLeg, rightLeg, leftArm, rightArm });
    if (archetype === 'hero' || archetype === 'dark_knight' || archetype === 'guardian') {
      make('breastplate_' + archetype, archetype === 'hero' ? metal : dark, [.62, .42, .13], [0, 1.05, .2]);
      make('helmet_' + archetype, dark, [.48, .22, .45], [0, 1.85, 0], 'sphere');
      make('armor_trim', gold, [.64, .08, .15], [0, .87, .22]);
    }
    if (skeleton) {
      const black = a.mat('skull_sockets', '#272B35');
      for (const x of [-.11, .11]) make('skull_eye', black, [.12, .12, .06], [x, 1.67, .2], 'sphere');
      for (let i = 0; i < 3; i++) make('rib', bone, [.53, .07, .3], [0, .83 + i * .15, 0]);
    }
    if (archetype === 'goblin' || archetype === 'demon_guard' || archetype === 'dark_knight' || boss) {
      for (const x of [-.3, .3]) {
        const horn = make('horn_' + archetype, archetype === 'goblin' ? cloth : bone, [.18, .48, .18], [x, 1.9, 0], 'cone');
        horn.rotation.z = x > 0 ? -.5 : .5;
      }
    }
    const swordPivot = new Mesh('weapon_arm', scene); swordPivot.parent = root; swordPivot.position.set(.48, 1.02, .12);
    const sword = a.part(swordPivot, 'character_sword', metal, [.13, .95, .09], [0, .55, .15]);
    a.part(swordPivot, 'sword_guard', gold, [.4, .08, .13], [0, .1, .15]);
    if (archetype === 'orc' || archetype === 'troll') a.part(swordPivot, 'axe_head', metal, [.6, .4, .2], [0, .8, .15]);
    parts.swordPivot = swordPivot; parts.sword = sword;
    if (archetype === 'dark_knight' || archetype === 'guardian')
      make('knight_shield', dark, [.48, .65, .15], [-.52, 1, .25]);
    height = boss ? 2.6 : 2.1;
  }
  let threat: Mesh | undefined;
  if (!obstacle && role !== 'hero') {
    threat = MeshBuilder.CreateTorus('target_threat_ring', { diameter: role === 'boss' ? 2.2 : 1.25, thickness: .045, tessellation: 24 }, scene);
    threat.parent = root; threat.position.y = .04;
    threat.material = a.mat(role === 'weapon' ? 'pickup_ring' : color.g > color.r ? 'threat_green' : 'threat_red',
      role === 'weapon' ? '#F8CF64' : color.g > color.r ? '#50DC78' : '#FF5966', .35);
  }
  root.metadata = { entityId: entity.id, kind: entity.kind, archetype: entity.archetype, obstacle };
  const children = root.getChildMeshes();
  for (const child of children) {
    child.metadata = { entityId: entity.id, kind: entity.kind, obstacle };
    child.isPickable = !obstacle && role !== 'hero';
  }
  root.isPickable = false;
  const entry = { root, parts, threat, children, labelHeight: height, scale: scaleFor(entity) };
  updateMeshFromEntity(entry, entity);
  return entry;
}

export function setEntityThreat(entry: EntityMeshEntry, color: Color3): void {
  if (!entry.threat) return;
  const green = color.g > color.r;
  entry.threat.material = assets(entry.root.getScene()).mat(green ? 'threat_green' : 'threat_red', green ? '#50DC78' : '#FF5966', .35);
}

export function updateMeshFromEntity(entry: EntityMeshEntry, entity: Entity, time = 0,
  motion: { facing?: number; animation?: string; attackProgress?: number; victory?: boolean } = {}): void {
  const { root, parts } = entry;
  entry.scale = scaleFor(entity);
  root.scaling.setAll(entry.scale);
  root.position.set(entity.x, entity.y, entity.z);
  root.setEnabled(entity.active);
  if (entity.kind === 'weapon') {
    root.position.y += .25 + Math.sin(time * 3) * .13; root.rotation.y = time * 1.3;
  } else if (motion.facing !== undefined) root.rotation.y = motion.facing;
  const walking = motion.animation === 'walk', phase = time * 11;
  if (parts.leftLeg) parts.leftLeg.rotation.x = walking ? Math.sin(phase) * .45 : 0;
  if (parts.rightLeg) parts.rightLeg.rotation.x = walking ? -Math.sin(phase) * .45 : 0;
  if (parts.leftArm) parts.leftArm.rotation.x = walking ? -Math.sin(phase) * .22 : -.12;
  if (parts.swordPivot) {
    const attacking = motion.animation === 'attack';
    parts.swordPivot.rotation.x = attacking ? -.65 : -.15;
    parts.swordPivot.rotation.y = attacking ? Math.sin((motion.attackProgress ?? 0) * Math.PI) * -1.8 : .2;
    parts.swordPivot.rotation.z = motion.victory ? -.5 - Math.sin(time * 7) * .3 : -.18;
  }
  for (let i = 0; i < 8; i++) if (parts['leg' + i]) parts['leg' + i].rotation.x = Math.sin(phase + i) * .07;
  if (motion.victory) root.position.y += Math.max(0, Math.sin(time * 7)) * .25;
}
export function disposeEntityMesh(entry: EntityMeshEntry): void { entry.root.dispose(false, false); }
export function disposeAllMeshes(meshMap: EntityMeshMap): void { for (const entry of meshMap.values()) disposeEntityMesh(entry); meshMap.clear(); }
