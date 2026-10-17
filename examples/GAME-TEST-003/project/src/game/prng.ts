// src/game/prng.ts - Deterministic Pseudo-Random Number Generator
// Owner: system-team (f02-prng-save)

/**
 * Mulberry32 PRNG - deterministic, fast, 32-bit state.
 * Produces values in [0, 1) with uniform distribution.
 */
export class PRNG {
  private state: number;

  constructor(seed: number) {
    // Ensure seed is a 32-bit integer
    this.state = seed | 0;
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
    if (min > max) {
      const tmp = min;
      min = max;
      max = tmp;
    }
    const range = max - min + 1;
    return min + Math.floor(this.next() * range);
  }
}
