/**
 * WebAudio programmatic audio synthesis manager.
 * All sounds are generated offline using OscillatorNode / AudioBuffer.
 * No external assets, no CDN, fully offline-capable.
 * Respects mobile browser autoplay policy: AudioContext is created
 * lazily on first user interaction.
 */

export class AudioManager {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private musicGain: GainNode | null = null;
  private sfxGain: GainNode | null = null;
  private musicEnabled = true;
  private sfxEnabled = true;
  private disposed = false;
  private activeSources: AudioBufferSourceNode[] = [];
  private bgmSource: AudioBufferSourceNode | null = null;
  private bgmInterval: ReturnType<typeof setInterval> | null = null;
  private buffers: Map<string, AudioBuffer> = new Map();
  private resumeHandler: (() => void) | null = null;

  constructor() {
    // Context is created lazily to respect autoplay policy
  }

  /** Lazily initialize or resume the AudioContext after user gesture. */
  private async ensureContext(): Promise<AudioContext | null> {
    if (this.disposed) return null;
    if (!this.ctx) {
      try {
        const AC = window.AudioContext || (window as any).webkitAudioContext;
        if (!AC) return null;
        this.ctx = new AC();
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = 1.0;
        this.masterGain.connect(this.ctx.destination);

        this.musicGain = this.ctx.createGain();
        this.musicGain.gain.value = this.musicEnabled ? 0.3 : 0;
        this.musicGain.connect(this.masterGain);

        this.sfxGain = this.ctx.createGain();
        this.sfxGain.gain.value = this.sfxEnabled ? 0.5 : 0;
        this.sfxGain.connect(this.masterGain);

        await this.synthesizeAllBuffers();
      } catch {
        return null;
      }
    }
    if (this.ctx.state === 'suspended') {
      try {
        await this.ctx.resume();
      } catch {
        // ignore
      }
    }
    return this.ctx;
  }

  /** Register a one-time user-gesture listener to unlock audio. */
  private registerUnlockListener(): void {
    if (this.resumeHandler) return;
    this.resumeHandler = () => {
      this.ensureContext().then(() => {
        if (this.resumeHandler) {
          document.removeEventListener('pointerdown', this.resumeHandler);
          document.removeEventListener('keydown', this.resumeHandler);
          this.resumeHandler = null;
        }
      });
    };
    document.addEventListener('pointerdown', this.resumeHandler, { once: false });
    document.addEventListener('keydown', this.resumeHandler, { once: false });
  }

  // ─── Synthesis helpers ──────────────────────────────────────────────

  private async synthesizeAllBuffers(): Promise<void> {
    if (!this.ctx) return;
    this.buffers.set('attack', this.synthAttack());
    this.buffers.set('pickup', this.synthPickup());
    this.buffers.set('invalid', this.synthInvalid());
    this.buffers.set('victory', this.synthVictory());
    this.buffers.set('defeat', this.synthDefeat());
    this.buffers.set('select', this.synthSelect());
    this.buffers.set('move', this.synthMove());
    this.buffers.set('powerup', this.synthPowerUp());
    this.buffers.set('boss_defeat', this.synthBossDefeat());
    this.buffers.set('start', this.synthStart());
    this.buffers.set('bgm_phrase', this.synthBgmPhrase());
  }

  /** Create an offline buffer of given duration and sample rate. */
  private createBuffer(durationSec: number): AudioBuffer {
    const sr = this.ctx!.sampleRate;
    const len = Math.floor(sr * durationSec);
    return this.ctx!.createBuffer(1, Math.max(len, 1), sr);
  }

  /** Write a sine wave into buffer data with envelope. */
  private writeSine(
    data: Float32Array,
    sr: number,
    freq: number,
    startSample: number,
    durationSamples: number,
    amplitude: number,
    attackRatio = 0.05,
    releaseRatio = 0.2
  ): void {
    const attackLen = Math.floor(durationSamples * attackRatio);
    const releaseLen = Math.floor(durationSamples * releaseRatio);
    for (let i = 0; i < durationSamples; i++) {
      const idx = startSample + i;
      if (idx >= data.length) break;
      let env = amplitude;
      if (i < attackLen) {
        env *= i / attackLen;
      } else if (i > durationSamples - releaseLen) {
        env *= (durationSamples - i) / releaseLen;
      }
      data[idx] += Math.sin((2 * Math.PI * freq * i) / sr) * env;
    }
  }

  /** Write noise burst. */
  private writeNoise(
    data: Float32Array,
    startSample: number,
    durationSamples: number,
    amplitude: number
  ): void {
    for (let i = 0; i < durationSamples; i++) {
      const idx = startSample + i;
      if (idx >= data.length) break;
      const env = amplitude * (1 - i / durationSamples);
      data[idx] += (Math.random() * 2 - 1) * env;
    }
  }

  // ─── Sound definitions ──────────────────────────────────────────────

  /** Short percussive hit for attack. */
  private synthAttack(): AudioBuffer {
    const buf = this.createBuffer(0.25);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    // Noise burst + low thump
    this.writeNoise(data, 0, Math.floor(sr * 0.08), 0.6);
    this.writeSine(data, sr, 120, 0, Math.floor(sr * 0.15), 0.5, 0.01, 0.4);
    this.writeSine(data, sr, 80, Math.floor(sr * 0.02), Math.floor(sr * 0.12), 0.3, 0.01, 0.5);
    return buf;
  }

  /** Bright ascending tone for pickup. */
  private synthPickup(): AudioBuffer {
    const buf = this.createBuffer(0.3);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    const dur = Math.floor(sr * 0.25);
    for (let i = 0; i < dur; i++) {
      const t = i / sr;
      const freq = 600 + 800 * (i / dur); // sweep 600→1400 Hz
      const env = 0.4 * (1 - i / dur);
      data[i] += Math.sin(2 * Math.PI * freq * t) * env;
    }
    return buf;
  }

  /** Low buzz for invalid action. */
  private synthInvalid(): AudioBuffer {
    const buf = this.createBuffer(0.3);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    this.writeSine(data, sr, 150, 0, Math.floor(sr * 0.25), 0.4, 0.02, 0.1);
    this.writeSine(data, sr, 155, 0, Math.floor(sr * 0.25), 0.3, 0.02, 0.1);
    return buf;
  }

  /** Triumphant major chord arpeggio for victory. */
  private synthVictory(): AudioBuffer {
    const buf = this.createBuffer(1.5);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    const notes = [523.25, 659.25, 783.99, 1046.5]; // C5 E5 G5 C6
    for (let n = 0; n < notes.length; n++) {
      const start = Math.floor(sr * 0.15 * n);
      const dur = Math.floor(sr * 0.6);
      this.writeSine(data, sr, notes[n], start, dur, 0.3, 0.05, 0.4);
    }
    // Final sustained chord
    const chordStart = Math.floor(sr * 0.7);
    const chordDur = Math.floor(sr * 0.7);
    for (const freq of [523.25, 659.25, 783.99]) {
      this.writeSine(data, sr, freq, chordStart, chordDur, 0.15, 0.1, 0.5);
    }
    return buf;
  }

  /** Descending minor tones for defeat. */
  private synthDefeat(): AudioBuffer {
    const buf = this.createBuffer(1.0);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    const notes = [400, 350, 300, 200];
    for (let n = 0; n < notes.length; n++) {
      const start = Math.floor(sr * 0.2 * n);
      const dur = Math.floor(sr * 0.3);
      this.writeSine(data, sr, notes[n], start, dur, 0.35, 0.05, 0.3);
    }
    return buf;
  }

  /** Short click for target selection. */
  private synthSelect(): AudioBuffer {
    const buf = this.createBuffer(0.1);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    this.writeSine(data, sr, 880, 0, Math.floor(sr * 0.06), 0.3, 0.01, 0.3);
    return buf;
  }

  /** Soft blip for movement confirmation. */
  private synthMove(): AudioBuffer {
    const buf = this.createBuffer(0.12);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    this.writeSine(data, sr, 440, 0, Math.floor(sr * 0.08), 0.2, 0.02, 0.4);
    return buf;
  }

  /** Rising sparkle for power increase. */
  private synthPowerUp(): AudioBuffer {
    const buf = this.createBuffer(0.4);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    const dur = Math.floor(sr * 0.35);
    for (let i = 0; i < dur; i++) {
      const t = i / sr;
      const freq = 500 + 600 * (i / dur);
      const env = 0.3 * (1 - 0.5 * (i / dur));
      data[i] += Math.sin(2 * Math.PI * freq * t) * env;
    }
    return buf;
  }

  /** Dramatic hit for boss defeat. */
  private synthBossDefeat(): AudioBuffer {
    const buf = this.createBuffer(0.8);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    this.writeNoise(data, 0, Math.floor(sr * 0.15), 0.7);
    this.writeSine(data, sr, 60, 0, Math.floor(sr * 0.6), 0.6, 0.01, 0.5);
    this.writeSine(data, sr, 200, Math.floor(sr * 0.1), Math.floor(sr * 0.4), 0.3, 0.05, 0.4);
    this.writeSine(data, sr, 400, Math.floor(sr * 0.2), Math.floor(sr * 0.5), 0.2, 0.05, 0.5);
    return buf;
  }

  /** Brief fanfare for game start. */
  private synthStart(): AudioBuffer {
    const buf = this.createBuffer(0.5);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    this.writeSine(data, sr, 523.25, 0, Math.floor(sr * 0.2), 0.3, 0.02, 0.3);
    this.writeSine(data, sr, 783.99, Math.floor(sr * 0.15), Math.floor(sr * 0.3), 0.35, 0.02, 0.3);
    return buf;
  }

  /** Looping BGM phrase: gentle fantasy arpeggio (~8 seconds). */
  private synthBgmPhrase(): AudioBuffer {
    const buf = this.createBuffer(8.0);
    const data = buf.getChannelData(0);
    const sr = buf.sampleRate;
    // Pentatonic melody pattern: C D E G A in two octaves
    const scale = [261.63, 293.66, 329.63, 392.0, 440.0, 523.25, 587.33, 659.25];
    const pattern = [0, 2, 4, 5, 4, 2, 3, 1, 0, 2, 4, 7, 5, 4, 2, 0];
    const noteDur = 0.45;
    const noteSamples = Math.floor(sr * noteDur);

    for (let p = 0; p < pattern.length; p++) {
      const startSample = Math.floor(sr * p * noteDur);
      const freq = scale[pattern[p]];
      // Main tone (triangle-like via harmonics)
      this.writeSine(data, sr, freq, startSample, noteSamples, 0.12, 0.1, 0.4);
      // Soft octave harmonic
      this.writeSine(data, sr, freq * 2, startSample, Math.floor(noteSamples * 0.6), 0.04, 0.1, 0.5);
    }

    // Gentle bass drone
    const bassDur = Math.floor(sr * 8.0);
    this.writeSine(data, sr, 130.81, 0, bassDur, 0.06, 0.2, 0.2);
    this.writeSine(data, sr, 196.0, 0, bassDur, 0.03, 0.2, 0.2);

    return buf;
  }

  // ─── Playback ───────────────────────────────────────────────────────

  private playBuffer(name: string, destination: GainNode | null): void {
    if (!this.ctx || !destination || this.disposed) return;
    const buffer = this.buffers.get(name);
    if (!buffer) return;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(destination);
    source.start(0);
    this.activeSources.push(source);
    source.onended = () => {
      const idx = this.activeSources.indexOf(source);
      if (idx >= 0) this.activeSources.splice(idx, 1);
    };
  }

  private startBgmLoop(): void {
    if (!this.ctx || !this.musicGain || !this.musicEnabled || this.disposed) return;
    this.stopBgm();
    const loop = (): void => {
      if (!this.ctx || !this.musicGain || !this.musicEnabled || this.disposed) return;
      const buffer = this.buffers.get('bgm_phrase');
      if (!buffer) return;
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.musicGain);
      source.start(0);
      this.bgmSource = source;
      source.onended = () => {
        if (this.bgmSource === source) {
          this.bgmSource = null;
        }
      };
    };
    loop();
    // Schedule next loop (phrase is ~8s)
    this.bgmInterval = setInterval(() => {
      if (this.musicEnabled && !this.disposed) {
        loop();
      }
    }, 8000);
  }

  private stopBgm(): void {
    if (this.bgmInterval !== null) {
      clearInterval(this.bgmInterval);
      this.bgmInterval = null;
    }
    if (this.bgmSource) {
      try {
        this.bgmSource.stop();
      } catch {
        // already stopped
      }
      this.bgmSource = null;
    }
  }

  // ─── Public API ─────────────────────────────────────────────────────

  setMusic(enabled: boolean): void {
    this.musicEnabled = enabled;
    if (this.musicGain) {
      this.musicGain.gain.value = enabled ? 0.3 : 0;
    }
    if (enabled) {
      this.ensureContext().then(() => {
        this.startBgmLoop();
      });
    } else {
      this.stopBgm();
    }
  }

  setSfx(enabled: boolean): void {
    this.sfxEnabled = enabled;
    if (this.sfxGain) {
      this.sfxGain.gain.value = enabled ? 0.5 : 0;
    }
  }

  play(name: string): void {
    this.registerUnlockListener();
    this.ensureContext().then(() => {
      if (this.disposed || !this.ctx) return;

      // Map spec sound events to synthesized buffers
      const sfxMap: Record<string, string> = {
        attack: 'attack',
        pickup: 'pickup',
        invalid: 'invalid',
        victory: 'victory',
        defeat: 'defeat',
        select: 'select',
        move: 'move',
        powerup: 'powerup',
        boss_defeat: 'boss_defeat',
        start: 'start',
        bgm: 'bgm_phrase',
      };

      const bufferName = sfxMap[name] || name;

      if (name === 'bgm') {
        this.startBgmLoop();
        return;
      }

      if (!this.sfxEnabled) return;
      this.playBuffer(bufferName, this.sfxGain);
    });
  }

  stopAll(): void {
    this.stopBgm();
    for (const src of this.activeSources) {
      try {
        src.stop();
      } catch {
        // already stopped
      }
    }
    this.activeSources = [];
  }

  dispose(): void {
    this.disposed = true;
    this.stopAll();
    if (this.resumeHandler) {
      document.removeEventListener('pointerdown', this.resumeHandler);
      document.removeEventListener('keydown', this.resumeHandler);
      this.resumeHandler = null;
    }
    if (this.ctx) {
      try {
        this.ctx.close();
      } catch {
        // ignore
      }
      this.ctx = null;
    }
    this.buffers.clear();
    this.masterGain = null;
    this.musicGain = null;
    this.sfxGain = null;
  }
}
