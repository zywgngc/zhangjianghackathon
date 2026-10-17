import Phaser from 'phaser';
export interface GameHandle { pause(): void; resume(): void; restart(): void; destroy(): void; }
export function mountGame(container: HTMLElement): GameHandle {
  let paused = false;
  class SmokeScene extends Phaser.Scene {
    private orb!: Phaser.GameObjects.Arc;
    private elapsed = 0;
    create() {
      this.cameras.main.setBackgroundColor('#172640');
      this.add.grid(400, 270, 800, 540, 40, 40, 0x1a3250, 1, 0x315078, 0.45);
      this.add.text(24, 24, 'Phaser · 本地 2D 引擎', { fontSize: '26px', color: '#edf8ff' });
      this.orb = this.add.circle(400, 270, 38, 0x69e5b6).setStrokeStyle(5, 0xb6ffde);
      container.dataset.engineReady = 'phaser';
      container.dataset.renderFrames = '0';
    }
    update(_time: number, delta: number) {
      container.dataset.renderFrames = String(Number(container.dataset.renderFrames || 0) + 1);
      if (paused) return;
      this.elapsed += Math.min(delta, 50);
      this.orb.x = 400 + Math.sin(this.elapsed / 900) * 180;
      this.orb.y = 270 + Math.cos(this.elapsed / 650) * 80;
    }
  }
  const game = new Phaser.Game({
    type: Phaser.AUTO, parent: container, width: 800, height: 540,
    backgroundColor: '#172640', scene: SmokeScene,
    scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
    audio: { noAudio: true }, render: { antialias: true },
  });
  return { pause() { paused = true; }, resume() { paused = false; }, restart() { paused = false; game.scene.getScenes(true)[0]?.scene.restart(); }, destroy() { game.destroy(true); } };
}
