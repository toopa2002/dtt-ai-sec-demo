import { Component, computed, effect, ElementRef, inject, input, output, signal, viewChild } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { MsalService } from '@azure/msal-angular';
import { Engine, HttpExchange, NodeId, Step } from '../flow.model';
import { AGENT_VIA, IDENTITIES, TOOLS_VIA } from '../app.config';

interface NodeBox {
  id: NodeId;
  label: string;
  sub: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** AWS resource names drawn under the subtitle (e.g. the AgentCore Gateway names). */
  res?: string[];
}

/** A dashed area grouping nodes that run in the same place (AWS, or the internal network behind the MCP Gateway). */
interface Boundary {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
}
const BOUNDARIES: { id: string; label: string; members: NodeId[]; padBottom: number }[] = [
  { id: 'aws', label: 'AWS Bedrock Platform', members: ['agent-gw', 'agent', 'bedrock', 'agentcore-gw'], padBottom: 12 },
  // Bottom padding leaves room for the badge under hr-directory ("403 blocked at gateway").
  { id: 'internal', label: 'Internal Service', members: ['gateway', 'weather', 'hr-directory'], padBottom: 24 },
];
const LINE = 11;

/** Grow a box around its centre so its text lines fit (label, subtitle, resource names, "as:" line). */
function fit(n: NodeBox): NodeBox {
  const lines = 3 + (n.res?.length ?? 0);
  const h = Math.max(n.h, 18 + 13 + (lines - 1) * LINE);
  return h === n.h ? n : { ...n, y: n.y - (h - n.h) / 2, h };
}

/** Keep edge ends on box tops/bottoms after fit() grew the boxes. */
function reattach(e: EdgeLine, before: Map<NodeId, NodeBox>, after: Map<NodeId, NodeBox>): EdgeLine {
  const move = (y: number, id: NodeId) => {
    const o = before.get(id)!;
    const n = after.get(id)!;
    return y === o.y ? n.y : y === o.y + o.h ? n.y + n.h : y;
  };
  return { ...e, y1: move(e.y1, e.a), y2: move(e.y2, e.b) };
}

// ax/xe/xg exist only in the AgentCore Gateway layout (agent -> AgentCore Gateway -> MCP Gateway);
// ci/ia only in the inbound layout (chatbot -> AgentCore Gateway -> agent).
type EdgeId = 'ce' | 'ca' | 'ci' | 'ia' | 'ae' | 'ab' | 'ag' | 'ax' | 'xe' | 'xg' | 'gw' | 'gh';
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

/**
 * Layout when the agent reaches the tools through an AWS Bedrock AgentCore Gateway (TOOLS_VIA=agentcore-gateway):
 * the AgentCore Gateway sits between the agent and the MCP Gateway and does the on-behalf-of exchange with Entra.
 */
const SHIFT = 164;
const shifted = (n: NodeBox): NodeBox => (['gateway', 'weather', 'hr-directory'].includes(n.id) ? { ...n, x: n.x + SHIFT } : n);
const AGENTCORE_NODES: NodeBox[] = [
  ...NODES.map(shifted),
  { id: 'agentcore-gw', label: 'AgentCore Gateways', sub: '1 per service · OBO', x: 352, y: 128, w: 136, h: 54 },
];
const AGENTCORE_EDGES: EdgeLine[] = [
  ...EDGES.filter((e) => !['ag', 'gw', 'gh'].includes(e.id)),
  { id: 'ax', a: 'agent', b: 'agentcore-gw', x1: 310, y1: 155, x2: 352, y2: 155 },
  { id: 'xe', a: 'agentcore-gw', b: 'entra', x1: 400, y1: 182, x2: 212, y2: 290 },
  { id: 'xg', a: 'agentcore-gw', b: 'gateway', x1: 488, y1: 155, x2: 526, y2: 155 },
  { id: 'gw', a: 'gateway', b: 'weather', x1: 650, y1: 145, x2: 694, y2: 80 },
  { id: 'gh', a: 'gateway', b: 'hr-directory', x1: 650, y1: 165, x2: 694, y2: 232 },
];
const WIDTH = 644;

/**
 * Inbound layout (AGENT_VIA=agentcore-gateway): the chatbot reaches the agent through an AgentCore Gateway with
 * token passthrough. Everything from the agent rightwards moves right; the direct chatbot -> agent edge is replaced.
 */
const IN_SHIFT = 150;
const STAYS: NodeId[] = ['chatbot', 'entra'];
function withInbound(nodes: NodeBox[], edges: EdgeLine[]): { nodes: NodeBox[]; edges: EdgeLine[] } {
  const moved = (id: NodeId) => !STAYS.includes(id);
  return {
    nodes: [
      ...nodes.map((n) => (moved(n.id) ? { ...n, x: n.x + IN_SHIFT } : n)),
      { id: 'agent-gw', label: 'AgentCore Gateway', sub: 'inbound · passthrough', x: 172, y: 128, w: 128, h: 54 },
    ],
    edges: [
      ...edges
        .filter((e) => e.id !== 'ca')
        .map((e) => ({ ...e, x1: e.x1 + (moved(e.a) ? IN_SHIFT : 0), x2: e.x2 + (moved(e.b) ? IN_SHIFT : 0) })),
      { id: 'ci', a: 'chatbot', b: 'agent-gw', x1: 126, y1: 155, x2: 172, y2: 155 },
      { id: 'ia', a: 'agent-gw', b: 'agent', x1: 300, y1: 155, x2: 328, y2: 155 },
    ],
  };
}

const PAIR: Record<string, EdgeId> = Object.fromEntries(
  [...EDGES, ...AGENTCORE_EDGES].flatMap((e) => [
    [`${e.a}>${e.b}`, e.id],
    [`${e.b}>${e.a}`, e.id],
  ]),
);
const TARGET_EDGE: Partial<Record<NodeId, EdgeId>> = { weather: 'gw', 'hr-directory': 'gh' };

/** An AgentCore Gateway (one per adapter) listing that adapter's tools for this user. */
function isAgentcoreListing(step: Step): boolean {
  return step.to === 'agentcore-gw' && !!step.target && step.label.startsWith('MCP initialize');
}
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
  /** Agent platform of the request shown: relabels the agent and Bedrock nodes. */
  readonly engine = input<Engine>('claude');
  /** The chat column is hidden: offer to bring it back. */
  readonly chatHidden = input(false);
  readonly showChat = output<void>();

  private readonly logEl = viewChild<ElementRef<HTMLElement>>('logEl');
  private readonly ids = inject(IDENTITIES);
  private readonly msal = inject(MsalService);
  /**
   * AgentCore Gateway layout when the agent is deployed with TOOLS_VIA=agentcore-gateway (so it is right before the
   * first question), or when a request actually went through it (replays of older requests still render correctly).
   */
  private readonly toolsVia = inject(TOOLS_VIA);
  /** Inbound AgentCore Gateway in front of the agent (AGENT_VIA=agentcore-gateway). */
  protected readonly viaInbound = inject(AGENT_VIA) === 'agentcore-gateway';
  protected readonly viaAgentcore = computed(
    () =>
      this.toolsVia === 'agentcore-gateway' ||
      this.steps().some((s) => s.to === 'agentcore-gw' || s.from === 'agentcore-gw'),
  );
  protected readonly ariaLabel = computed(
    () =>
      'Request path: chatbot, ' +
      (this.viaInbound ? 'AgentCore Gateway (inbound), ' : '') +
      'agent, ' +
      (this.viaAgentcore() ? 'AgentCore Gateways, ' : '') +
      'MCP gateway, MCP servers',
  );
  /** The model loop runs on a Bedrock Agent for this request (the chatbot's platform switch). */
  protected readonly viaBedrockAgent = computed(
    () => this.engine() === 'bedrock-agent' || this.steps().some((s) => s.to === 'bedrock' && s.label.startsWith('InvokeAgent')),
  );
  private readonly layout = computed(() => {
    const base = this.viaAgentcore()
      ? { nodes: AGENTCORE_NODES, edges: AGENTCORE_EDGES }
      : { nodes: NODES, edges: EDGES };
    const laid = this.viaInbound ? withInbound(base.nodes, base.edges) : base;
    // The agent platform: who runs the model loop. AgentCore = the runtime calls Claude itself; Bedrock Agent = the
    // Bedrock Agent plans and picks tools, the AgentCore runtime executes them with the user's token.
    const bedrockAgent = this.viaBedrockAgent();
    const named = laid.nodes.map((n) =>
        n.id === 'bedrock'
          ? bedrockAgent
            ? { ...n, label: 'Bedrock Agent', sub: 'agent platform · tools' }
            : { ...n, label: 'Bedrock', sub: 'Claude Haiku 4.5' }
          : n.id === 'agent'
            ? bedrockAgent
              ? { ...n, label: 'AgentCore agent', sub: 'tool runner' }
              : { ...n, label: 'AgentCore agent', sub: 'agent platform' }
            : n,
    ).map((n) => ({ ...n, res: this.resources(n.id, bedrockAgent) }));
    const before = new Map(named.map((n) => [n.id, n]));
    const nodes = named.map(fit);
    const after = new Map(nodes.map((n) => [n.id, n]));
    const boundaries: Boundary[] = BOUNDARIES.flatMap((b) => {
      const inside = nodes.filter((n) => b.members.includes(n.id));
      if (!inside.length) return [];
      const x = Math.min(...inside.map((n) => n.x)) - 12;
      const y = Math.min(...inside.map((n) => n.y)) - 16;
      const w = Math.max(...inside.map((n) => n.x + n.w)) + 12 - x;
      const h = Math.max(...inside.map((n) => n.y + n.h)) + b.padBottom - y;
      return [{ id: b.id, label: b.label, x, y, w, h }];
    });
    return { nodes, edges: laid.edges.map((e) => reattach(e, before, after)), boundaries };
  });
  protected readonly nodes = computed(() => this.layout().nodes);
  protected readonly edges = computed(() => this.layout().edges);
  protected readonly boundaries = computed(() => this.layout().boundaries);
  protected readonly viewBox = computed(() => {
    const boxes = [...this.layout().nodes, ...this.layout().boundaries];
    const x0 = Math.min(0, ...boxes.map((b) => b.x)) - 4;
    const y0 = Math.min(0, ...boxes.map((b) => b.y)) - 12;
    const x1 = Math.max(WIDTH + (this.viaAgentcore() ? SHIFT : 0) + (this.viaInbound ? IN_SHIFT : 0), ...boxes.map((b) => b.x + b.w)) + 4;
    const y1 = Math.max(318, ...boxes.map((b) => b.y + b.h)) + 4;
    return `${x0} ${y0} ${x1 - x0} ${y1 - y0}`;
  });

  /** The AWS resource behind each node, by name (from config.json; names are fixed by scripts/*.sh). */
  private resources(id: NodeId, bedrockAgent: boolean): string[] | undefined {
    switch (id) {
      case 'agent-gw':
        return ['mcpdemo-gw-agent'];
      case 'agent':
        return this.ids.agentRuntimeName ? [this.ids.agentRuntimeName] : undefined;
      case 'bedrock':
        return [bedrockAgent ? this.ids.bedrockAgentName || 'mcpdemo-tools-agent' : 'direct model call'];
      case 'agentcore-gw':
        return this.ids.mcpAdapters.map((a) => `mcpdemo-gw-${a}`);
      default:
        return undefined;
    }
  }

  /** Baseline of text line i (0 = label) in a box, the lines centred vertically. */
  protected lineY(n: NodeBox, i: number): number {
    const lines = 2 + (n.res?.length ?? 0) + (this.nodeAs()[n.id] ? 1 : 0);
    const top = n.y + n.h / 2 - ((lines - 1) * LINE + 13) / 2 + 11;
    return i === 0 ? top : top + 2 + i * LINE;
  }
  protected readonly openStep = signal<string | null>(null);

  /** During a replay, the steps shown so far (null = show the input as-is). */
  private readonly replaySteps = signal<Step[] | null>(null);
  private replayTimer: ReturnType<typeof setTimeout> | undefined;
  protected readonly replaying = computed(() => this.replaySteps() !== null);
  protected readonly shown = computed(() => this.replaySteps() ?? this.steps());

  protected readonly edgeState = computed(() => {
    const state: Record<EdgeId, EdgeState> = {
      ce: 'idle', ca: 'idle', ci: 'idle', ia: 'idle', ae: 'idle', ab: 'idle', ag: 'idle', ax: 'idle', xe: 'idle', xg: 'idle',
      gw: 'idle', gh: 'idle',
    };
    const set = (id: EdgeId | undefined, s: EdgeState) => {
      if (!id) return;
      // Never let a later success hide a denial on the same edge.
      if (state[id] === 'blocked' || (state[id] === 'denied' && s === 'ok')) return;
      state[id] = s;
    };
    for (const step of this.shown()) {
      const status: EdgeState = step.status === 'start' ? 'active' : step.status;
      if (this.viaInbound && step.from === 'chatbot' && step.to === 'agent') {
        // Through the inbound gateway: a failure the chatbot saw itself (HTTP status, e.g. a 401 from the gateway) is
        // on the first leg; a denial the agent reported (azp / role checks) means the gateway passed it through.
        const atGateway = step.detail?.['http_status'] !== undefined;
        if (atGateway || status === 'active') {
          set('ci', status);
          if (status === 'active') set('ia', status);
        } else {
          set('ci', 'ok');
          set('ia', status);
        }
        continue;
      }
      set(PAIR[`${step.from}>${step.to}`], status);
      if (step.to === 'agentcore-gw') {
        // The AgentCore Gateway exchanges the token with Entra (OBO) and calls the MCP Gateway on every request;
        // a denial comes back from the MCP Gateway, so the AgentCore leg itself still worked.
        set('xe', status === 'denied' ? 'ok' : status);
        set('xg', status === 'denied' ? 'ok' : status);
        if (isAgentcoreListing(step) && step.status === 'denied') {
          set(TARGET_EDGE[step.target!], 'blocked');
          continue;
        }
      }
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
      const edge = this.edges().find((e) => e.id === id);
      if (edge) rev[id] = edge.a !== step.from;
    }
    return rev;
  });

  protected readonly edgeCount = computed(() => {
    const count: Partial<Record<EdgeId, number>> = {};
    for (const step of this.shown()) {
      if (step.status === 'start') continue;
      const inbound = this.viaInbound && step.from === 'chatbot' && step.to === 'agent';
      const ids: (EdgeId | undefined)[] = inbound ? ['ci', 'ia'] : [PAIR[`${step.from}>${step.to}`]];
      if (step.to === 'agentcore-gw' && step.status === 'ok') ids.push('xg');
      if (step.target && !isProbe(step) && step.status === 'ok') ids.push(TARGET_EDGE[step.target]!);
      for (const id of ids) if (id) count[id] = (count[id] ?? 0) + 1;
    }
    return count;
  });

  protected readonly nodeBadge = computed(() => {
    const badge: Partial<Record<NodeId, { text: string; kind: 'ok' | 'deny' | 'warn' }>> = {};
    for (const step of this.shown()) {
      if (!step.target) continue;
      if (isAgentcoreListing(step) && step.status === 'denied') badge[step.target] = { text: '403 blocked at gateway', kind: 'deny' };
      if (isAgentcoreListing(step) && step.status === 'error') badge[step.target] = { text: 'unavailable', kind: 'warn' };
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
    if (id === 'agent-gw') return 'AgentCore Gateway (inbound)';
    return AGENTCORE_NODES.find((n) => n.id === id)?.label ?? String(id ?? '');
  }

  /**
   * The account each node acts as: `short` is drawn in the box, `full` is the hover title. MCP servers show the
   * user identity the MCP Gateway forwarded on the latest tool call of the selected request.
   */
  protected readonly nodeAs = computed(() => {
    const upn = this.msal.instance.getActiveAccount()?.username ?? '';
    const user = upn ? upn.split('@')[0] : 'signed-in user';
    const role = (arn: string) => arn.split('/').pop() ?? arn;
    const agentRole = role(this.ids.agentRoleArn) || 'execution role';
    const gwRole = role(this.ids.gatewayRoleArn) || 'mcpdemo-agentcore-gateway-role';
    const as: Partial<Record<NodeId, { short: string; full: string }>> = {
      chatbot: { short: `as: ${user}`, full: `Signed-in user ${upn || '(none)'} (MSAL, token #1)` },
      entra: { short: `tenant ${this.ids.tenantId.slice(0, 8)}…`, full: `Entra ID tenant ${this.ids.tenantId}` },
      'agent-gw': { short: 'as: gateway role', full: `IAM role ${this.ids.gatewayRoleArn || gwRole}; forwards token #1 unchanged (passthrough)` },
      agent: { short: `as: role · for ${user}`, full: `IAM execution role ${this.ids.agentRoleArn || agentRole}, acting for ${upn || 'the user'} (token #1)` },
      bedrock: this.viaBedrockAgent()
        ? { short: 'as: mcpdemo-tools-agent-role', full: `Bedrock Agent ${this.ids.bedrockAgentId} (role mcpdemo-tools-agent-role); action groups weather-mcp, hr-directory-mcp return each tool call to the agent; invoked by ${this.ids.agentRoleArn || agentRole}` }
        : { short: 'called by agent role', full: `Invoked with SigV4 by ${this.ids.agentRoleArn || agentRole}` },
      'agentcore-gw': { short: `as: gw role · OBO ${user}`, full: `IAM role ${this.ids.gatewayRoleArn || gwRole}; exchanges token #1 for token #2 on behalf of ${upn || 'the user'}` },
      gateway: { short: 'app mcpdemo-gateway-api', full: `Entra app mcpdemo-gateway-api (${this.ids.gatewayApiClientId || 'client id'}); checks token #2 roles; k8s service account mcpgateway-sa` },
    };
    for (const server of ['weather', 'hr-directory'] as NodeId[]) {
      const saw = [...this.shown()].reverse().map((s) => (s.target === server ? this.serverSaw(s) : null)).find((x) => x);
      as[server] = saw
        ? { short: `as: ${String(saw['user_id'] ?? '?').slice(0, 14)}`, full: `Gateway-forwarded user ${saw['user_id']} with roles ${JSON.stringify(saw['roles'])} (pod ${saw['served_by']})` }
        : { short: 'as: forwarded user', full: 'Runs as the user id and roles the MCP Gateway forwards (X-Mcp-UserId / X-Mcp-Roles)' };
    }
    return as;
  });

  protected http(step: Step): HttpExchange[] {
    const http = step.detail?.['http'];
    return Array.isArray(http) ? (http as HttpExchange[]) : [];
  }

  protected headers(h?: Record<string, string>): [string, string][] {
    return Object.entries(h ?? {});
  }

  protected isBad(status: unknown): boolean {
    return typeof status === 'number' && status >= 400;
  }

  protected serverSaw(step: Step): Record<string, unknown> | null {
    const saw = step.detail?.['server_saw'];
    return saw && typeof saw === 'object' ? (saw as Record<string, unknown>) : null;
  }

  protected otherDetail(step: Step): Record<string, unknown> {
    const { server_saw: _saw, http: _http, ...rest } = step.detail ?? {};
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
