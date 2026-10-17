import * as BABYLON from '@babylonjs/core';

/** A driven runner camera; input is owned exclusively by InputHandler. */
export class CameraRig {
  private readonly camera: BABYLON.FreeCamera;
  private target: BABYLON.AbstractMesh | null = null;
  private readonly offset = new BABYLON.Vector3(0, 14, -18);
  private readonly desired = BABYLON.Vector3.Zero();
  private readonly focus = BABYLON.Vector3.Zero();
  private count = 1;
  private portrait = false;
  private initialized = false;
  private disposed = false;

  constructor(private readonly scene: BABYLON.Scene, _canvas: HTMLCanvasElement) {
    this.camera = new BABYLON.FreeCamera('squad_follow_camera', this.offset.clone(), scene);
    // Do not attach mouse/touch orbit controls to the gameplay drag surface.
    this.camera.inputs.clear();
    this.camera.minZ = .1;
    this.camera.maxZ = 600;
    this.camera.inertia = 0;
    this.camera.fov = 1.02;
    this.scene.activeCamera = this.camera;
    this.adjustForOrientation();
  }

  setTarget(mesh: BABYLON.AbstractMesh): void {
    this.target = mesh;
    this.initialized = false;
    this.update(0, true);
  }

  setFormationSize(count: number): void {
    const next = Math.max(0, Math.min(200, Math.floor(count)));
    if (next === this.count) return;
    this.count = next;
    this.adjustForOrientation();
  }

  /** Uses actual camera.position, not an unused interpolation accumulator. */
  update(deltaSeconds: number, force = false): void {
    if (this.disposed || !this.target || this.target.isDisposed()) return;
    const p = this.target.position;
    // Follow forward progress fully; retain both alternative lanes when strafing.
    this.desired.set(p.x * .25 + this.offset.x, p.y + this.offset.y, p.z + this.offset.z);
    if (force || !this.initialized || BABYLON.Vector3.DistanceSquared(this.camera.position, this.desired) > 24 * 24) {
      this.camera.position.copyFrom(this.desired);
      this.initialized = true;
    } else {
      BABYLON.Vector3.LerpToRef(this.camera.position, this.desired,
        1 - Math.exp(-Math.max(0, Math.min(deltaSeconds, .1)) * 12), this.camera.position);
    }
    this.focus.set(p.x * .25, p.y + .2, p.z + (this.portrait ? 9 : 11));
    this.camera.setTarget(this.focus);
    this.camera.getViewMatrix(true);
  }

  adjustForOrientation(): void {
    const engine = this.scene.getEngine();
    this.portrait = engine.getRenderWidth() < engine.getRenderHeight();
    const depth = Math.max(0, Math.ceil(this.count / 15) - 1) * .8;
    this.offset.set(0, (this.portrait ? 14 : 12) + depth * .85,
      -(this.portrait ? 18 : 17) - depth * .85);
    this.camera.fov = this.portrait ? 1.02 : .88;
    this.update(0, true);
  }

  resize(): void { this.adjustForOrientation(); }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.target = null;
    this.camera.dispose();
  }
}
