import { GameEvent } from './types';

export class EventLog {
  private events: GameEvent[] = [];
  private nextSeq: number = 1;

  public emit(simTime: number, type: GameEvent['type'], data: Record<string, unknown> = {}): void {
    const event: GameEvent = {
      seq: this.nextSeq++,
      simTime,
      type,
      data,
    };
    this.events.push(event);
  }

  public getEvents(afterSeq: number = 0): GameEvent[] {
    if (afterSeq <= 0) return [...this.events];
    // Binary search could be used for optimization if events list becomes very large,
    // but for typical game frames, linear filter or slice is sufficient.
    // Since seq is strictly increasing, we can find the start index.
    // However, simple filter is safe and clear for now.
    if (!Number.isFinite(afterSeq)) return [];
    return this.events.slice(Math.max(0,Math.floor(afterSeq)));
  }

  public clear(): void {
    this.events = [];
    this.nextSeq = 1;
  }

  public get count(): number {
    return this.events.length;
  }
}
