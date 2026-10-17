// src/audio/AudioEngine.ts
// Feature f09-audio: Web Audio API synthesis for all game sounds and music.

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private musicGain: GainNode | null = null;
  private sfxGain: GainNode | null = null;
  
  private musicEnabled: boolean = true;
  private sfxEnabled: boolean = true;
  private isInitialized: boolean = false;
  private musicInterval: number | null = null;
  private musicStep: number = 0;

  // Simple pentatonic scale for background music
  private readonly musicScale: number[] = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25];

  constructor() {
    // Context is created on init() to comply with autoplay policies
  }

  public init(): void {
    if (this.isInitialized) return;

    try {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioContextClass) {
        console.warn('Web Audio API not supported');
        return;
      }

      this.ctx = new AudioContextClass();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 0.5; // Master volume
      this.masterGain.connect(this.ctx.destination);

      this.musicGain = this.ctx.createGain();
      this.musicGain.gain.value = 0.3; // Music volume
      this.musicGain.connect(this.masterGain);

      this.sfxGain = this.ctx.createGain();
      this.sfxGain.gain.value = 0.6; // SFX volume
      this.sfxGain.connect(this.masterGain);

      this.isInitialized = true;
      
      // Start music loop if enabled
      if (this.musicEnabled) {
        this.startMusicLoop();
      }
    } catch (e) {
      console.error('Failed to initialize AudioEngine', e);
    }
  }

  public resumeContext(): void {
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().then(() => {
        if (this.musicEnabled && !this.musicInterval) {
          this.startMusicLoop();
        }
      }).catch(e => console.error('Failed to resume audio context', e));
    }
  }

  public setMusic(enabled: boolean): void {
    this.musicEnabled = enabled;
    if (this.musicGain) {
      this.musicGain.gain.setTargetAtTime(enabled ? 0.3 : 0, this.ctx!.currentTime, 0.1);
    }
    if (enabled && this.isInitialized && this.ctx?.state === 'running') {
      this.startMusicLoop();
    } else if (!enabled) {
      this.stopMusicLoop();
    }
  }

  public setSFX(enabled: boolean): void {
    this.sfxEnabled = enabled;
    if (this.sfxGain) {
      this.sfxGain.gain.setTargetAtTime(enabled ? 0.6 : 0, this.ctx!.currentTime, 0.1);
    }
  }

  public play(event: string): void {
    if (!this.isInitialized || !this.sfxEnabled || !this.ctx || this.ctx.state !== 'running') {
      return;
    }

    switch (event) {
      case 'start':
        this.playTone(440, 'triangle', 0.18, 0.3);
        break;
      case 'shoot':
        this.playShoot();
        break;
      case 'gate_positive':
        this.playGatePositive();
        break;
      case 'gate_negative':
        this.playGateNegative();
        break;
      case 'enemy_hit':
        this.playEnemyHit();
        break;
      case 'soldier_loss':
        this.playSoldierLoss();
        break;
      case 'explosion':
        this.playExplosion();
        break;
      case 'boss_destroyed':
        this.playBossDestroyed();
        break;
      case 'victory':
        this.playVictory();
        break;
      case 'defeat':
        this.playDefeat();
        break;
      default:
        // Unknown event, ignore or play generic beep
        break;
    }
  }

  public dispose(): void {
    this.stopMusicLoop();
    if (this.ctx) {
      this.ctx.close().catch(e => console.error('Error closing audio context', e));
      this.ctx = null;
    }
    this.masterGain = null;
    this.musicGain = null;
    this.sfxGain = null;
    this.isInitialized = false;
  }

  // --- Synthesis Helpers ---

  private playTone(freq: number, type: OscillatorType, duration: number, volume: number = 1, slideTo?: number): void {
    if (!this.ctx || !this.sfxGain) return;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();

    osc.type = type;
    osc.frequency.setValueAtTime(freq, this.ctx.currentTime);
    if (slideTo !== undefined) {
      osc.frequency.exponentialRampToValueAtTime(slideTo, this.ctx.currentTime + duration);
    }

    gain.gain.setValueAtTime(volume, this.ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + duration);

    osc.connect(gain);
    gain.connect(this.sfxGain);

    osc.start();
    osc.stop(this.ctx.currentTime + duration);
  }

  private playNoise(duration: number, volume: number = 1): void {
    if (!this.ctx || !this.sfxGain) return;

    const bufferSize = this.ctx.sampleRate * duration;
    const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);

    for (let i = 0; i < bufferSize; i++) {
      data[i] = Math.random() * 2 - 1;
    }

    const noise = this.ctx.createBufferSource();
    noise.buffer = buffer;

    const gain = this.ctx.createGain();
    gain.gain.setValueAtTime(volume, this.ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + duration);

    // Lowpass filter for explosion-like sound
    const filter = this.ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 1000;

    noise.connect(filter);
    filter.connect(gain);
    gain.connect(this.sfxGain);

    noise.start();
  }

  // --- Specific Sound Effects ---

  private playShoot(): void {
    // Short high-pitched square wave
    this.playTone(800, 'square', 0.1, 0.3, 400);
  }

  private playGatePositive(): void {
    // Ascending arpeggio
    if (!this.ctx || !this.sfxGain) return;
    const now = this.ctx.currentTime;
    [523.25, 659.25, 783.99].forEach((freq, i) => {
      const osc = this.ctx!.createOscillator();
      const gain = this.ctx!.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.1);
      gain.gain.linearRampToValueAtTime(0.4, now + i * 0.1 + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.01, now + i * 0.1 + 0.3);
      osc.connect(gain);
      gain.connect(this.sfxGain!);
      osc.start(now + i * 0.1);
      osc.stop(now + i * 0.1 + 0.3);
    });
  }

  private playGateNegative(): void {
    // Descending buzz
    this.playTone(300, 'sawtooth', 0.3, 0.4, 100);
  }

  private playEnemyHit(): void {
    // Short noise burst
    this.playNoise(0.1, 0.2);
  }

  private playSoldierLoss(): void {
    // Low thud
    this.playTone(150, 'sine', 0.2, 0.5, 50);
  }

  private playExplosion(): void {
    // Long noise with lowpass
    this.playNoise(0.5, 0.6);
    this.playTone(100, 'sawtooth', 0.5, 0.3, 20);
  }

  private playBossDestroyed(): void {
    // Massive explosion sequence
    this.playExplosion();
    setTimeout(() => this.playExplosion(), 100);
    setTimeout(() => this.playTone(60, 'square', 1.0, 0.5, 20), 200);
  }

  private playVictory(): void {
    // Major chord arpeggio
    if (!this.ctx || !this.sfxGain) return;
    const now = this.ctx.currentTime;
    [523.25, 659.25, 783.99, 1046.50].forEach((freq, i) => {
      const osc = this.ctx!.createOscillator();
      const gain = this.ctx!.createGain();
      osc.type = 'triangle';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.15);
      gain.gain.linearRampToValueAtTime(0.5, now + i * 0.15 + 0.1);
      gain.gain.exponentialRampToValueAtTime(0.01, now + i * 0.15 + 0.8);
      osc.connect(gain);
      gain.connect(this.sfxGain!);
      osc.start(now + i * 0.15);
      osc.stop(now + i * 0.15 + 0.8);
    });
  }

  private playDefeat(): void {
    // Descending minor chord
    if (!this.ctx || !this.sfxGain) return;
    const now = this.ctx.currentTime;
    [392.00, 349.23, 329.63, 261.63].forEach((freq, i) => {
      const osc = this.ctx!.createOscillator();
      const gain = this.ctx!.createGain();
      osc.type = 'sawtooth';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.2);
      gain.gain.linearRampToValueAtTime(0.4, now + i * 0.2 + 0.1);
      gain.gain.exponentialRampToValueAtTime(0.01, now + i * 0.2 + 1.0);
      osc.connect(gain);
      gain.connect(this.sfxGain!);
      osc.start(now + i * 0.2);
      osc.stop(now + i * 0.2 + 1.0);
    });
  }

  // --- Background Music ---

  private startMusicLoop(): void {
    if (this.musicInterval) return;
    
    // Simple procedural loop using setInterval for step sequencing
    // In a real engine, this would be scheduled precisely with AudioContext time
    this.musicStep = 0;
    this.musicInterval = window.setInterval(() => {
      if (!this.ctx || !this.musicGain || this.ctx.state !== 'running') return;
      
      const note = this.musicScale[this.musicStep % this.musicScale.length];
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      
      osc.type = 'triangle';
      osc.frequency.value = note / 2; // Bass line
      
      gain.gain.setValueAtTime(0.1, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.4);
      
      osc.connect(gain);
      gain.connect(this.musicGain);
      
      osc.start();
      osc.stop(this.ctx.currentTime + 0.4);
      
      // Occasional higher note
      if (this.musicStep % 4 === 0) {
        const osc2 = this.ctx.createOscillator();
        const gain2 = this.ctx.createGain();
        osc2.type = 'sine';
        osc2.frequency.value = note * 2;
        gain2.gain.setValueAtTime(0.05, this.ctx.currentTime);
        gain2.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.2);
        osc2.connect(gain2);
        gain2.connect(this.musicGain);
        osc2.start();
        osc2.stop(this.ctx.currentTime + 0.2);
      }

      this.musicStep++;
    }, 250); // 120 BPM approx (8th notes)
  }

  private stopMusicLoop(): void {
    if (this.musicInterval) {
      clearInterval(this.musicInterval);
      this.musicInterval = null;
    }
  }
}
