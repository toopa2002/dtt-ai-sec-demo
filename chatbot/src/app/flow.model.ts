// Events streamed by the agent (agent/agent.py) plus the chatbot's own client-side hops.

export type NodeId =
  | 'chatbot' | 'entra' | 'agent-gw' | 'agent' | 'bedrock' | 'agentcore-gw' | 'gateway' | 'weather' | 'hr-directory';
export type HopStatus = 'start' | 'ok' | 'denied' | 'error';

/** One HTTP exchange shown for a step. Tokens are never included: Authorization reads "Bearer <token #N>". */
export interface HttpExchange {
  request: { method: string; url: string; headers?: Record<string, string>; body?: string | null };
  response: { status?: number | string | null; headers?: Record<string, string>; body?: string | null };
}

export interface HopEvent {
  type: 'hop';
  id: string;
  from: NodeId;
  to: NodeId;
  target?: NodeId | null;
  label: string;
  status: HopStatus;
  ms?: number;
  detail?: Record<string, unknown>;
}

/** The agent platform / model loop a request ran on. */
export type Engine = 'claude' | 'bedrock-agent';

export interface FinalEvent {
  type: 'final';
  answer: string;
  engine?: Engine;
  tools_used: string[];
  services_allowed: string[];
  services_denied: string[];
  services_unavailable: string[];
  ms: number;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
}

export type AgentEvent = HopEvent | FinalEvent | ErrorEvent;

/** One row of the step log: a hop's start and end events merged. */
export type Step = Omit<HopEvent, 'type'>;

/**
 * Merge a hop event into the step list (start creates the step, the terminal status updates it). Details are merged,
 * and HTTP exchanges appended, because the chatbot and the agent both report parts of the same POST /invocations step.
 */
export function applyHop(steps: Step[], ev: HopEvent): Step[] {
  const { type: _type, ...step } = ev;
  const i = steps.findIndex((s) => s.id === ev.id);
  if (i < 0) return [...steps, step];
  const next = [...steps];
  const prev = next[i];
  next[i] = { ...prev, ...step, detail: mergeDetail(prev.detail, step.detail) };
  return next;
}

function mergeDetail(a?: Record<string, unknown>, b?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!a || !b) return b ?? a;
  const http = [...((a['http'] as unknown[]) ?? []), ...((b['http'] as unknown[]) ?? [])];
  return { ...a, ...b, ...(http.length ? { http } : {}) };
}
