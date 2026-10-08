import { Message } from './models';

/**
 * Time-synced threads on the IAM engineer's screen (FR-006i, US3 #16, SC-018, research R26).
 *
 * `minutes()` builds one divider list for both threads: every local minute in which either thread has a message.
 * `withDividers()` lays one thread out against it, so both threads show the same times; a run of minutes with nothing
 * in this thread becomes one "No messages from … to …" line. `TimeSync` couples the two scrolling logs: a user scroll in
 * one brings the other to its last item at or before the same time (the items carry `data-ts`), and at the bottom both
 * stay at the bottom. Scrolls it causes itself are ignored, so the two never chase each other.
 */

export type ThreadItem =
  | { kind: 'msg'; ts: number; m: Message }
  | { kind: 'divider'; ts: number; label: string }
  | { kind: 'gap'; ts: number; from: string; to: string };

const MINUTE = 60_000;

const minuteOf = (iso: string): number => Math.floor(new Date(iso).getTime() / MINUTE) * MINUTE;

export const hhmm = (ts: number): string =>
  new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

export function minutes(a: Message[], b: Message[]): number[] {
  return [...new Set([...a, ...b].map((m) => minuteOf(m.created_at)))].sort((x, y) => x - y);
}

export function withDividers(messages: Message[], all: number[] | null): ThreadItem[] {
  if (!all) return messages.map((m) => ({ kind: 'msg', ts: new Date(m.created_at).getTime(), m }));
  const out: ThreadItem[] = [];
  let i = 0;
  const flushUntil = (t: number) => {
    const empty: number[] = [];
    while (i < all.length && all[i] < t) empty.push(all[i++]);
    if (empty.length) out.push({ kind: 'gap', ts: empty[0], from: hhmm(empty[0]), to: hhmm(empty[empty.length - 1]) });
  };
  let current = -1;
  for (const m of messages) {
    const t = minuteOf(m.created_at);
    if (t !== current) {
      flushUntil(t);
      if (i < all.length && all[i] === t) i++;
      out.push({ kind: 'divider', ts: t, label: hhmm(t) });
      current = t;
    }
    out.push({ kind: 'msg', ts: new Date(m.created_at).getTime(), m });
  }
  flushUntil(Number.POSITIVE_INFINITY);
  return out;
}

const BOTTOM = 48;
const IGNORE_MS = 120;

export class TimeSync {
  /** Scroll events within this window after we moved a log are our own, not the user's. */
  private readonly ignoreUntil = new WeakMap<HTMLElement, number>();
  private frame = 0;
  private readonly handlers: [HTMLElement, () => void][] = [];

  constructor(
    private readonly a: HTMLElement,
    private readonly b: HTMLElement,
    private readonly enabled: () => boolean,
  ) {
    for (const [from, to] of [[a, b], [b, a]] as const) {
      const handler = () => this.onScroll(from, to);
      from.addEventListener('scroll', handler, { passive: true });
      this.handlers.push([from, handler]);
    }
  }

  destroy(): void {
    cancelAnimationFrame(this.frame);
    for (const [el, h] of this.handlers) el.removeEventListener('scroll', h);
  }

  private onScroll(from: HTMLElement, to: HTMLElement): void {
    if (performance.now() < (this.ignoreUntil.get(from) ?? 0)) return;  // our own scroll of this log
    if (!this.enabled()) return;
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => this.align(from, to));
  }

  /** Bring `to` to the moment shown at the top of `from` (or both to the bottom). */
  align(from: HTMLElement, to: HTMLElement): void {
    let target: number;
    if (from.scrollHeight - from.scrollTop - from.clientHeight < BOTTOM) {
      target = to.scrollHeight;
    } else {
      const t = topTime(from);
      if (t === null) return;
      const items = [...to.querySelectorAll<HTMLElement>('[data-ts]')];
      let best: HTMLElement | null = null;
      for (const el of items) {
        if (Number(el.dataset['ts']) <= t) best = el;
        else break;
      }
      target = best ? best.offsetTop : 0;
    }
    if (Math.abs(to.scrollTop - target) < 2) return;
    this.ignoreUntil.set(to, performance.now() + IGNORE_MS);
    to.scrollTop = target;
  }
}

function topTime(log: HTMLElement): number | null {
  for (const el of log.querySelectorAll<HTMLElement>('[data-ts]')) {
    if (el.offsetTop + el.offsetHeight > log.scrollTop) return Number(el.dataset['ts']);
  }
  return null;
}
