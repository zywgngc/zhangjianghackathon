import * as BABYLON from '@babylonjs/core';

interface HumanoidParts {
  leftLeg: BABYLON.InstancedMesh; rightLeg: BABYLON.InstancedMesh;
  leftArm: BABYLON.InstancedMesh; rightArm: BABYLON.InstancedMesh; rifle: BABYLON.InstancedMesh;
}
interface GateVisual {
  texture: BABYLON.DynamicTexture; material: BABYLON.StandardMaterial;
  panel: BABYLON.Mesh; frame: BABYLON.AbstractMesh[]; key: string;
}

/** Original procedural military assets. Instances share geometry and materials. */
export class MeshFactory {
  private readonly materials = new Map<string, BABYLON.StandardMaterial>();
  private readonly prototypes = new Map<string, BABYLON.Mesh>();
  private readonly gates = new Map<BABYLON.Mesh, GateVisual>();
  private nextId = 0;
  private disposed = false;

  constructor(private readonly scene: BABYLON.Scene) {}

  material(key: string, hex: string, glow = 0): BABYLON.StandardMaterial {
    let material = this.materials.get(key);
    if (!material) {
      material = new BABYLON.StandardMaterial(key, this.scene);
      material.diffuseColor = BABYLON.Color3.FromHexString(hex);
      material.emissiveColor = material.diffuseColor.scale(glow);
      material.specularColor.set(.08, .08, .08);
      this.materials.set(key, material);
    }
    return material;
  }

  private part(root: BABYLON.Mesh, key: string, size: [number, number, number],
      material: BABYLON.StandardMaterial, at: [number, number, number], shape = 'box'): BABYLON.InstancedMesh {
    let prototype = this.prototypes.get(key);
    if (!prototype) {
      prototype = shape === 'sphere'
        ? BABYLON.MeshBuilder.CreateSphere('prototype_' + key, { diameter: 1, segments: 6 }, this.scene)
        : shape === 'cylinder'
          ? BABYLON.MeshBuilder.CreateCylinder('prototype_' + key, { diameter: 1, height: 1, tessellation: 8 }, this.scene)
          : BABYLON.MeshBuilder.CreateBox('prototype_' + key, { size: 1 }, this.scene);
      prototype.material = material;
      prototype.isVisible = false;
      prototype.isPickable = false;
      this.prototypes.set(key, prototype);
    }
    const instance = prototype.createInstance(key + '_' + (++this.nextId));
    instance.parent = root;
    instance.isVisible = true;
    instance.isPickable = false;
    instance.scaling.set(size[0], size[1], size[2]);
    instance.position.set(at[0], at[1], at[2]);
    return instance;
  }

  private humanoid(enemy: boolean): BABYLON.Mesh {
    const root = new BABYLON.Mesh(enemy ? 'enemy_root' : 'soldier_root', this.scene);
    const role = enemy ? 'enemy' : 'soldier';
    const cloth = this.material(role + '_uniform', enemy ? '#A2553C' : '#536A3B');
    const helmet = this.material(role + '_helmet', enemy ? '#513C34' : '#344729');
    const skin = this.material('skin', '#D7AE84');
    const steel = this.material('rifle_steel', '#293138');
    const boots = this.material('boots', '#2C2D26');
    this.part(root, role + '_torso', [.42, .55, .28], cloth, [0, .74, 0]);
    this.part(root, role + '_head', [.30, .33, .30], skin, [0, 1.17, 0], 'sphere');
    this.part(root, role + '_helmet', [.37, .19, .37], helmet, [0, 1.31, -.015], 'sphere');
    const leftLeg = this.part(root, role + '_leg', [.14, .48, .17], cloth, [-.12, .27, 0]);
    const rightLeg = this.part(root, role + '_leg', [.14, .48, .17], cloth, [.12, .27, 0]);
    this.part(root, 'boots_geometry', [.18, .12, .25], boots, [-.12, .06, .035]);
    this.part(root, 'boots_geometry', [.18, .12, .25], boots, [.12, .06, .035]);
    const leftArm = this.part(root, role + '_arm', [.13, .40, .14], cloth, [-.27, .79, .03]);
    const rightArm = this.part(root, role + '_arm', [.13, .40, .14], cloth, [.27, .79, .07]);
    rightArm.rotation.x = -.65; leftArm.rotation.x = -.3;
    const rifle = this.part(root, 'assault_rifle', [.10, .12, .68], steel, [.23, .80, .28]);
    this.part(root, 'rifle_magazine', [.09, .18, .12], steel, [.23, .69, .24]);
    root.metadata = { role, children: root.getChildMeshes(), parts: { leftLeg, rightLeg, leftArm, rightArm, rifle } satisfies HumanoidParts };
    root.isPickable = false;
    return root;
  }

  createSoldier(_scene: BABYLON.Scene): BABYLON.Mesh { return this.humanoid(false); }
  createEnemy(_scene: BABYLON.Scene): BABYLON.Mesh { return this.humanoid(true); }

  animateHumanoid(root: BABYLON.AbstractMesh, time: number, moving: boolean, recoil: number, hit: number): void {
    const parts = root.metadata?.parts as HumanoidParts | undefined;
    if (!parts) return;
    const phase = time * 13 + root.uniqueId * .7;
    const stride = moving ? Math.sin(phase) * .38 : 0;
    parts.leftLeg.rotation.x = stride; parts.rightLeg.rotation.x = -stride;
    parts.leftArm.rotation.x = -.3 - stride * .3;
    parts.rightArm.rotation.x = -.65;
    parts.rifle.position.z = .28 - Math.max(0, recoil) * .08;
    root.rotation.z = hit > 0 ? Math.sin(time * 60) * .14 * hit : 0;
    for (const child of (root.metadata?.children ?? []) as BABYLON.AbstractMesh[]) {
      child.renderOverlay = hit > 0;
      if (hit > 0) { child.overlayColor.set(1, .25, .15); child.overlayAlpha = hit * .5; }
    }
  }

  createBoss(_scene: BABYLON.Scene): BABYLON.Mesh {
    const root = new BABYLON.Mesh('armored_command_vehicle', this.scene);
    const armor = this.material('boss_armor', '#675F42');
    const dark = this.material('tracks', '#262D2E');
    const steel = this.material('boss_steel', '#48525A');
    this.part(root, 'tank_chassis', [3.6, 1.2, 4.4], armor, [0, 1, 0]);
    this.part(root, 'tank_turret', [2.5, 1, 2.6], armor, [0, 2.1, -.2]);
    this.part(root, 'tank_track', [.55, .7, 4.7], dark, [-1.8, .6, 0]);
    this.part(root, 'tank_track', [.55, .7, 4.7], dark, [1.8, .6, 0]);
    const cannon = this.part(root, 'tank_cannon', [.28, 3, .28], steel, [0, 2.2, -2.35], 'cylinder');
    cannon.rotation.x = Math.PI / 2;
    for (const x of [-1.81, 1.81]) for (let z = -1.7; z <= 1.8; z += .7) {
      const wheel = this.part(root, 'track_wheel', [.5, .15, .5], steel, [x, .6, z], 'cylinder');
      wheel.rotation.z = Math.PI / 2;
    }
    this.part(root, 'tank_red_signal', [.22, .15, .22], this.material('warning_light', '#E75030', .65), [0, 2.7, -.3], 'sphere');
    const back = BABYLON.MeshBuilder.CreatePlane('boss_health_background', { width: 4.5, height: .3 }, this.scene);
    back.parent = root; back.position.set(0, 3.3, 0); back.billboardMode = BABYLON.Mesh.BILLBOARDMODE_ALL;
    back.material = this.material('health_background', '#282C24');
    const fill = BABYLON.MeshBuilder.CreatePlane('boss_health_fill', { width: 4.4, height: .23 }, this.scene);
    fill.parent = back; fill.position.z = -.02; fill.material = this.material('boss_health', '#D84A3B', .35);
    root.metadata = { role: 'boss', children: root.getChildMeshes(), healthFill: fill };
    return root;
  }

  updateBoss(root: BABYLON.AbstractMesh, hp: number, maxHp: number, hit: number): void {
    const fill = root.metadata?.healthFill as BABYLON.Mesh | undefined;
    if (fill) {
      const ratio = Math.max(0, Math.min(1, hp / Math.max(1, maxHp)));
      fill.scaling.x = ratio; fill.position.x = -(1 - ratio) * 2.2;
    }
    for (const child of (root.metadata?.children ?? []) as BABYLON.AbstractMesh[]) {
      child.renderOverlay = hit > 0;
      if (hit > 0) { child.overlayColor.set(1, .2, .05); child.overlayAlpha = hit * .5; }
    }
  }

  createGate(_scene: BABYLON.Scene): BABYLON.Mesh {
    const root = new BABYLON.Mesh('mathematical_gate', this.scene);
    const frameMaterial = this.material('gate_neutral', '#777B67');
    const frame = [
      this.part(root, 'gate_neutral_geometry', [.19, 3.6, .22], frameMaterial, [-2.62, 1.8, 0]),
      this.part(root, 'gate_neutral_geometry', [.19, 3.6, .22], frameMaterial, [2.62, 1.8, 0]),
      this.part(root, 'gate_neutral_geometry', [5.43, .18, .22], frameMaterial, [0, 3.6, 0]),
    ];
    // Frame instances use one neutral material; the result-dependent emissive sign carries feedback.
    const panel = BABYLON.MeshBuilder.CreatePlane('gate_operator_operand', { width: 4.9, height: 2.1 }, this.scene);
    panel.parent = root; panel.position.set(0, 2.55, -.14);
    const texture = new BABYLON.DynamicTexture('gate_label_' + (++this.nextId), { width: 640, height: 288 }, this.scene, false);
    const material = new BABYLON.StandardMaterial('gate_label_material_' + this.nextId, this.scene);
    material.diffuseTexture = texture; material.emissiveTexture = texture;
    material.disableLighting = true; material.backFaceCulling = false;
    material.specularColor.set(0, 0, 0); panel.material = material;
    this.gates.set(root, { texture, material, panel, frame, key: '' });
    root.metadata = { role: 'gate' };
    return root;
  }

  updateGate(root: BABYLON.Mesh, operator: string, operand: number, current: number, result: number): void {
    const gate = this.gates.get(root);
    if (!gate) return;
    const key = operator + ':' + operand + ':' + current + ':' + result;
    if (gate.key === key) return;
    gate.key = key;
    const change = result - current;
    const ctx = gate.texture.getContext() as CanvasRenderingContext2D;
    ctx.fillStyle = change > 0 ? '#137C57' : change < 0 ? '#B43339' : '#53606C';
    ctx.fillRect(0, 0, 640, 288);
    ctx.strokeStyle = change > 0 ? '#63F2BA' : change < 0 ? '#FF9D92' : '#B9C5CD';
    ctx.lineWidth = 12; ctx.strokeRect(6, 6, 628, 276);
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = '#FFFFFF'; ctx.font = 'bold 130px system-ui,sans-serif';
    ctx.fillText(operator + operand, 320, 103);
    ctx.font = 'bold 39px system-ui,sans-serif';
    ctx.fillText('士兵 ' + current + ' → ' + result, 320, 208);
    ctx.font = '25px system-ui,sans-serif';
    ctx.fillText(change > 0 ? '增援通道' : change < 0 ? '减员警告' : '兵力不变', 320, 258);
    gate.texture.update();
    root.metadata = { ...root.metadata, operator, operand, current, result, delta: change };
  }

  createProjectile(): BABYLON.AbstractMesh {
    const root = new BABYLON.Mesh('projectile_root', this.scene);
    this.part(root, 'tracer_round', [.07, .07, .38], this.material('tracer', '#FFE778', .9), [0, 0, 0]);
    root.metadata = { role: 'projectile' }; return root;
  }

  createObstacle(type: string, _scene: BABYLON.Scene): BABYLON.Mesh {
    const root = new BABYLON.Mesh('obstacle_' + type, this.scene);
    const sand = this.material('sandbag', '#A99761');
    const steel = this.material('wire', '#78838B');
    if (type === 'mine') {
      this.part(root, 'mine_body', [.7, .22, .7], this.material('mine', '#303B30'), [0, .15, 0], 'cylinder');
      this.part(root, 'mine_signal', [.12, .10, .12], this.material('warning_light', '#E75030', .65), [0, .3, 0], 'sphere');
    } else if (type === 'barbedWire' || type === 'wire') {
      for (const x of [-.9, .9]) this.part(root, 'wire_post', [.12, 1.1, .12], steel, [x, .55, 0]);
      for (let i = -3; i <= 3; i++) {
        const wire = this.part(root, 'wire_cross', [.06, 1, .06], steel, [i * .28, .55, 0]);
        wire.rotation.z = i % 2 ? .8 : -.8;
      }
      this.part(root, 'wire_rail', [2, .06, .06], steel, [0, .75, 0]);
    } else {
      for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++)
        this.part(root, 'sandbag_geometry', [.65, .3, .5], sand,
          [(col - 1) * .64 + (row % 2) * .15, .15 + row * .28, 0]);
    }
    root.metadata = { role: 'obstacle', obstacleType: type }; return root;
  }

  createDecoration(kind: string): BABYLON.Mesh {
    const root = new BABYLON.Mesh('decoration_' + kind, this.scene);
    const olive = this.material('crate', '#707144');
    const steel = this.material('tower', '#626C69');
    if (kind === 'tower') {
      for (const x of [-.7, .7]) for (const z of [-.7, .7])
        this.part(root, 'tower_support', [.14, 4.5, .14], steel, [x, 2.25, z]);
      this.part(root, 'tower_cabin', [2, 1.2, 2], olive, [0, 4.4, 0]);
      this.part(root, 'tower_roof', [2.5, .2, 2.5], steel, [0, 5.15, 0]);
    } else {
      this.part(root, 'military_crate', [1.3, 1.1, 1.1], olive, [0, .55, 0]);
      this.part(root, 'crate_band', [1.35, .12, 1.16], steel, [0, .55, 0]);
    }
    return root;
  }

  /** Disposing an entity never destroys shared geometry/materials used by other soldiers. */
  release(root: BABYLON.AbstractMesh): void {
    const gate = this.gates.get(root as BABYLON.Mesh);
    if (gate) {
      gate.panel.material = null;
      gate.material.dispose(false, false); gate.texture.dispose();
      this.gates.delete(root as BABYLON.Mesh);
    }
    root.dispose(false, false);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const root of [...this.gates.keys()]) this.release(root);
    for (const prototype of this.prototypes.values()) prototype.dispose(false, false);
    this.prototypes.clear();
    for (const material of this.materials.values()) material.dispose(false, false);
    this.materials.clear();
  }
}
