// src/game/prng.ts
// Feature f01-config-types: Deterministic PRNG using Mulberry32 algorithm.

export class PRNG {
  private state: number;

  constructor(seed: number) {
    this.state = seed | 0;
    if (this.state === 0) {
      this.state = 1;
    }
  }

  /** Returns a float in [0, 1). */
  next(): number {
    let t = (this.state += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** Returns an integer in [min, max] inclusive. */
  nextInt(min: number, max: number): number {
    const range = max - min + 1;
    return min + Math.floor(this.next() * range);
  }
}
