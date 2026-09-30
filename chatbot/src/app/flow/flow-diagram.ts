import { Component, computed, effect, ElementRef, input, signal, viewChild } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { NodeId, Step } from '../flow.model';

interface NodeBox {
  id: NodeId;
  label: string;
  sub: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

type EdgeId = 'ce' | 'ca' | 'ae' | 'ab' | 'ag' | 'gw' | 'gh';
type EdgeState = 'idle' | 'active' | 'ok' | 'denied' | 'error' | 'blocked';

interface EdgeLine {
  id: EdgeId;
  a: NodeId;
  b: NodeId;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const NODES: NodeBox[] = [
  { id: 'chatbot', label: 'Chatbot', sub: 'browser · MSAL', x: 10, y: 128, w: 116, h: 54 },
  { id: 'entra', label: 'Entra ID', sub: 'tokens · OBO', x: 96, y: 262, w: 116, h: 48 },
  { id: 'agent', label: 'AgentCore agent', sub: 'ap-southeast-1', x: 178, y: 128, w: 132, h: 54 },
  { id: 'bedrock', label: 'Bedrock', sub: 'Claude Haiku 4.5', x: 178, y: 14, w: 132, h: 48 },
  { id: 'gateway', label: 'MCP Gateway', sub: 'k3s · Entra auth', x: 362, y: 128, w: 124, h: 54 },
  { id: 'weather', label: 'weather', sub: 'MCP server', x: 530, y: 52, w: 104, h: 48 },
  { id: 'hr-directory', label: 'hr-directory', sub: 'MCP server · PII', x: 530, y: 210, w: 104, h: 48 },
];

const EDGES: EdgeLine[] = [
  { id: 'ce', a: 'chatbot', b: 'entra', x1: 68, y1: 182, x2: 132, y2: 262 },
  { id: 'ca', a: 'chatbot', b: 'agent', x1: 126, y1: 155, x2: 178, y2: 155 },
  { id: 'ae', a: 'agent', b: 'entra', x1: 222, y1: 182, x2: 180, y2: 262 },
  { id: 'ab', a: 'agent', b: 'bedrock', x1: 244, y1: 128, x2: 244, y2: 62 },
  { id: 'ag', a: 'agent', b: 'gateway', x1: 310, y1: 155, x2: 362, y2: 155 },
  { id: 'gw', a: 'gateway', b: 'weather', x1: 486, y1: 145, x2: 530, y2: 80 },
  { id: 'gh', a: 'gateway', b: 'hr-directory', x1: 486, y1: 165, x2: 530, y2: 232 },
];

const PAIR: Record<string, EdgeId> = Object.fromEntries(
  EDGES.flatMap((e) => [
    [`${e.a}>${e.b}`, e.id],
    [`${e.b}>${e.a}`, e.id],
  ]),
);
const TARGET_EDGE: Partial<Record<NodeId, EdgeId>> = { weather: 'gw', 'hr-directory': 'gh' };
const STEP_MS = 450;

/** Is this step the gateway's own permission probe (answered by the gateway, not forwarded)? */
function isProbe(step: Step): boolean {
  return step.label.startsWith('GET /adapters');
}

@Component({
  selector: 'app-flow-diagram',
  imports: [JsonPipe],
  templateUrl: './flow-diagram.html',
  styleUrl: './flow-diagram.css',
})
export class FlowDiagram {
  /** Steps of the selected request (live while it is running). */
  readonly steps = input<Step[]>([]);
  readonly live = input(false);

  private readonly logEl = viewChild<ElementRef<HTMLElement>>('logEl');
  protected readonly nodes = NODES;
  protected readonly edges = EDGES;
  protected readonly openStep = signal<string | null>(null);

  /** During a replay, the steps shown so far (null = show the input as-is). */
  private readonly replaySteps = signal<Step[] | null>(null);
  private replayTimer: ReturnType<typeof setTimeout> | undefined;
  protected readonly replaying = computed(() => this.replaySteps() !== null);
  protected readonly shown = computed(() => this.replaySteps() ?? this.steps());

  protected readonly edgeState = computed(() => {
    const state: Record<EdgeId, EdgeState> = { ce: 'idle', ca: 'idle', ae: 'idle', ab: 'idle', ag: 'idle', gw: 'idle', gh: 'idle' };
    const set = (id: EdgeId | undefined, s: EdgeState) => {
      if (!id) return;
      // Never let a later success hide a denial on the same edge.
      if (state[id] === 'blocked' || (state[id] === 'denied' && s === 'ok')) return;
      state[id] = s;
    };
    for (const step of this.shown()) {
      const status: EdgeState = step.status === 'start' ? 'active' : step.status;
      set(PAIR[`${step.from}>${step.to}`], status);
      const downstream = step.target ? TARGET_EDGE[step.target] : undefined;
      if (!downstream) continue;
      if (isProbe(step)) {
        if (step.status === 'denied') set(downstream, 'blocked');
      } else {
        set(downstream, status);
      }
    }
    return state;
  });

  /** Direction of travel for the moving dot on active edges. */
  protected readonly edgeReversed = computed(() => {
    const rev: Partial<Record<EdgeId, boolean>> = {};
    for (const step of this.shown()) {
      const id = PAIR[`${step.from}>${step.to}`];
      const edge = EDGES.find((e) => e.id === id);
      if (edge) rev[id] = edge.a !== step.from;
    }
    return rev;
  });

  protected readonly edgeCount = computed(() => {
    const count: Partial<Record<EdgeId, number>> = {};
    for (const step of this.shown()) {
      if (step.status === 'start') continue;
      const ids = [PAIR[`${step.from}>${step.to}`]];
      if (step.target && !isProbe(step) && step.status === 'ok') ids.push(TARGET_EDGE[step.target]!);
      for (const id of ids) if (id) count[id] = (count[id] ?? 0) + 1;
    }
    return count;
  });

  protected readonly nodeBadge = computed(() => {
    const badge: Partial<Record<NodeId, { text: string; kind: 'ok' | 'deny' | 'warn' }>> = {};
    for (const step of this.shown()) {
      if (!step.target) continue;
      if (isProbe(step) && step.status === 'denied') badge[step.target] = { text: '403 blocked at gateway', kind: 'deny' };
      if (isProbe(step) && step.status === 'error') badge[step.target] = { text: 'unavailable', kind: 'warn' };
      if (step.label.startsWith('tools/call') && step.status === 'ok') {
        const prev = badge[step.target];
        const n = prev?.kind === 'ok' ? parseInt(prev.text, 10) + 1 : 1;
        badge[step.target] = { text: `${n} call${n > 1 ? 's' : ''}`, kind: 'ok' };
      }
    }
    return badge;
  });

  constructor() {
    // Keep the newest step in view while a request streams in.
    effect(() => {
      this.shown();
      queueMicrotask(() => {
        const el = this.logEl()?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      });
    });
    // A new request (or selection) cancels a running replay.
    effect(() => {
      this.steps();
      this.stopReplay();
    });
  }

  protected path(e: EdgeLine): string {
    return this.edgeReversed()[e.id] ? `M${e.x2},${e.y2} L${e.x1},${e.y1}` : `M${e.x1},${e.y1} L${e.x2},${e.y2}`;
  }

  protected mid(e: EdgeLine): { x: number; y: number } {
    return { x: (e.x1 + e.x2) / 2, y: (e.y1 + e.y2) / 2 };
  }

  protected nodeName(id: NodeId | null | undefined): string {
    return NODES.find((n) => n.id === id)?.label ?? String(id ?? '');
  }

  protected icon(step: Step): string {
    return { start: '⏳', ok: '✅', denied: '⛔', error: '⚠️' }[step.status];
  }

  protected serverSaw(step: Step): Record<string, unknown> | null {
    const saw = step.detail?.['server_saw'];
    return saw && typeof saw === 'object' ? (saw as Record<string, unknown>) : null;
  }

  protected otherDetail(step: Step): Record<string, unknown> {
    const { server_saw: _saw, ...rest } = step.detail ?? {};
    return rest;
  }

  protected toggle(id: string): void {
    this.openStep.update((cur) => (cur === id ? null : id));
  }

  protected replay(): void {
    const all = this.steps();
    if (!all.length) return;
    this.stopReplay();
    this.replaySteps.set([]);
    let i = 0;
    const tick = () => {
      if (i >= all.length) {
        this.replayTimer = setTimeout(() => this.replaySteps.set(null), STEP_MS);
        return;
      }
      const step = all[i++];
      // Show the hop in flight, then its outcome.
      this.replaySteps.update((s) => [...(s ?? []), { ...step, status: 'start' }]);
      this.replayTimer = setTimeout(() => {
        this.replaySteps.update((s) => (s ?? []).map((x) => (x.id === step.id ? step : x)));
        this.replayTimer = setTimeout(tick, STEP_MS / 2);
      }, STEP_MS / 2);
    };
    tick();
  }

  private stopReplay(): void {
    clearTimeout(this.replayTimer);
    this.replaySteps.set(null);
  }
}
