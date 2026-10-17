import { Engine } from '@babylonjs/core/Engines/engine';
import { Scene } from '@babylonjs/core/scene';
import { ArcRotateCamera } from '@babylonjs/core/Cameras/arcRotateCamera';
import { HemisphericLight } from '@babylonjs/core/Lights/hemisphericLight';
import { Vector3 } from '@babylonjs/core/Maths/math.vector';
import { Color3, Color4 } from '@babylonjs/core/Maths/math.color';
import { MeshBuilder } from '@babylonjs/core/Meshes/meshBuilder';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial';
export interface GameHandle { pause(): void; resume(): void; restart(): void; resize(): void; destroy(): void; }
export function mountGame(container: HTMLElement): GameHandle {
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-label', 'Babylon 三维游戏场景');
  container.appendChild(canvas);
  const engine = new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
  const scene = new Scene(engine);
  scene.clearColor = new Color4(0.06, 0.1, 0.18, 1);
  const camera = new ArcRotateCamera('camera', -Math.PI / 2, 1.1, 12, new Vector3(0, 1, 0), scene);
  camera.attachControl(canvas, true);
  new HemisphericLight('light', new Vector3(0.4, 1, -0.3), scene);
  const ground = MeshBuilder.CreateGround('ground', { width: 14, height: 14 }, scene);
  const floor = new StandardMaterial('floor', scene);
  floor.diffuseColor = new Color3(0.12, 0.23, 0.32);
  ground.material = floor;
  const mesh = MeshBuilder.CreateBox('player', { size: 1.6 }, scene);
  mesh.position.y = 1.4;
  const material = new StandardMaterial('player-material', scene);
  material.diffuseColor = new Color3(0.25, 0.9, 0.65);
  mesh.material = material;
  let paused = false;
  let frames = 0;
  engine.runRenderLoop(() => {
    if (!paused) mesh.rotation.y += Math.min(engine.getDeltaTime(), 50) / 1000;
    scene.render();
    container.dataset.engineReady = 'babylon';
    container.dataset.renderFrames = String(++frames);
  });
  const resize = () => engine.resize();
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  return { pause() { paused = true; }, resume() { paused = false; }, restart() { paused = false; mesh.rotation.y = 0; }, resize() { engine.resize(); }, destroy() { observer.disconnect(); scene.dispose(); engine.dispose(); canvas.remove(); } };
}
